"""Validation of Request parameters against an Endpoint (DESIGN.md §8 step 1).

It runs on the parameters as the user gave them, before anything is uploaded, estimated
or submitted, and reports every problem at once: each names the parameter, the
constraint (a JSON Schema keyword such as `required`, `type`, `enum`, `maximum`,
`maxLength`, `maxItems`, or `unknown` / `token`) and a message.

The Endpoint's full `input_schema` is checked with `jsonschema`, so conditional rules
(`if`/`then`/`else`, `allOf`) apply too; on top of that, parameters the Endpoint does not
have are rejected (most schemas allow extras, which hides typos such as `promt`) and
prompt tokens such as `<<<image_2>>>` must refer to attached media. A local file path
stands in for the URL it will be uploaded to, so it satisfies `uri` and `^https?://`.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from hfcore import config, inputs
from hfcore.catalog import Catalog, Endpoint
from hfcore.errors import HiggsError

# `uri` is not checked: a local Input is a path until it is uploaded, and the API is
# the judge of URLs. `uuid` catches a Soul ID or style id pasted wrong.
_FORMATS = FormatChecker(formats=["uuid"])

_TOKEN = re.compile(r"<<<([a-z]+)_(\d+)>>>")
_REQUIRED = re.compile(r"^'(.+)' is a required property$")
# What a local Input becomes once uploaded, for validation purposes.
_UPLOADED = "https://upload.invalid/"


class Problem(BaseModel):
    """One violated constraint. `param` is a path into the params: `duration`, `multi_prompt[0].prompt`."""

    param: str
    constraint: str
    message: str


class ValidationFailed(HiggsError):
    """The parameters break the Endpoint's constraints; `problems` lists every one."""

    code = "validation_failed"

    def __init__(self, endpoint: Endpoint, problems: list[Problem]):
        lines = "\n".join(f"  {problem.param}: {problem.message}" for problem in problems)
        super().__init__(
            f"Invalid parameters for {endpoint.id}:\n{lines}",
            hint=f"See `higgs models show {endpoint.model}`; pass --no-validate to submit them anyway.",
        )
        self.problems = problems

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["problems"] = [problem.model_dump() for problem in self.problems]
        return data


def validate_request(catalog: Catalog, endpoint_id: str, params: dict[str, Any], *, cwd: Path) -> Endpoint:
    """The Endpoint for `endpoint_id`, after checking `params` against it (raises `ValidationFailed`)."""
    try:
        endpoint = catalog.endpoint(endpoint_id)
    except HiggsError as error:
        local = config.config_dir() / "models.local.toml"
        raise HiggsError(
            error.message,
            hint=f"{error.hint} To call it anyway, pass --no-validate; to validate it, describe it in {local}.",
            code=error.code,
        ) from None
    validate_params(endpoint, params, cwd=cwd)
    return endpoint


def validate_params(endpoint: Endpoint, params: dict[str, Any], *, cwd: Path) -> None:
    """Raise `ValidationFailed` listing every problem of `params` for `endpoint`, if any."""
    found = check_params(endpoint, params, cwd=cwd)
    if found:
        raise ValidationFailed(endpoint, found)


def check_params(endpoint: Endpoint, params: dict[str, Any], *, cwd: Path) -> list[Problem]:
    """Every problem of `params` for `endpoint`; empty when they are valid."""
    as_sent = {key: _as_uploaded(value, cwd) for key, value in params.items()}
    validator = Draft202012Validator(endpoint.input_schema, format_checker=_FORMATS)
    found = [*_unknown(endpoint, params), *_tokens(endpoint, params)]
    for error in validator.iter_errors(as_sent):
        if error.validator == "additionalProperties" and not error.absolute_path:
            continue  # reported by `_unknown`, one per parameter
        found.append(_problem(error))
    unique = {(p.param, p.constraint, p.message): p for p in found}
    return sorted(unique.values(), key=lambda problem: problem.param)


def _as_uploaded(value: Any, cwd: Path) -> Any:
    if isinstance(value, str):
        return _UPLOADED + Path(value).name if inputs.is_input(value, cwd) else value
    if isinstance(value, list):
        return [_as_uploaded(item, cwd) for item in value]
    if isinstance(value, dict):
        return {key: _as_uploaded(item, cwd) for key, item in value.items()}
    return value


def _unknown(endpoint: Endpoint, params: dict[str, Any]) -> Iterable[Problem]:
    known = list(endpoint.input_schema.get("properties", {}))
    for name in params:
        if name not in known:
            close = difflib.get_close_matches(name, known, n=1)
            suggestion = f"did you mean {close[0]}? " if close else ""
            yield Problem(
                param=name,
                constraint="unknown",
                message=f"is not a parameter of this Endpoint; {suggestion}Parameters: {', '.join(known)}",
            )


def _tokens(endpoint: Endpoint, params: dict[str, Any]) -> Iterable[Problem]:
    """`<<<image_2>>>` in the prompt needs a second item in `image_urls`."""
    prompt = params.get("prompt")
    if endpoint.prompt is None or not endpoint.prompt.tokens or not isinstance(prompt, str):
        return
    kinds = {match.group(1) for token in endpoint.prompt.tokens if (match := _TOKEN.fullmatch(token))}
    for match in _TOKEN.finditer(prompt):
        token, kind, number = match.group(0), match.group(1), int(match.group(2))
        if kind not in kinds:
            allowed = ", ".join(endpoint.prompt.tokens)
            message = f"uses {token}, but this Endpoint only takes tokens like {allowed}"
            yield Problem(param="prompt", constraint="token", message=message)
            continue
        field = f"{kind}_urls"
        attached = params.get(field)
        count = len(attached) if isinstance(attached, list) else 0
        if not 1 <= number <= count:
            message = f"uses {token}, but {field} has {count} item{'' if count == 1 else 's'}"
            yield Problem(param="prompt", constraint="token", message=message)


def _problem(error: ValidationError) -> Problem:
    path = _path(error.absolute_path)
    keyword = str(error.validator)
    if keyword == "required" and (match := _REQUIRED.match(error.message)):
        name = match.group(1)
        return Problem(param=f"{path}.{name}" if path else name, constraint="required", message="is required")
    if keyword in ("anyOf", "oneOf") and error.context:
        if all(inner.validator == "type" for inner in error.context):
            types = [inner.validator_value for inner in error.context]
            message = f"must be {_types(types)}; got {_show(error.instance)}"
            return Problem(param=path, constraint="type", message=message)
        specific = next(inner for inner in error.context if inner.validator != "type")
        return _problem(specific)
    message = _message(keyword, error.validator_value, error.instance) or error.message
    return Problem(param=path, constraint=keyword, message=message)


def _message(keyword: str, limit: Any, value: Any) -> str | None:
    size = len(value) if isinstance(value, (str, list)) else None
    match keyword:
        case "type":
            return f"must be {_types([limit])}; got {_show(value)}"
        case "enum":
            return f"must be one of {', '.join(_show(item) for item in limit)}; got {_show(value)}"
        case "const":
            return f"must be {_show(limit)}; got {_show(value)}"
        case "minimum":
            return f"must be at least {limit}; got {_show(value)}"
        case "maximum":
            return f"must be at most {limit}; got {_show(value)}"
        case "exclusiveMinimum":
            return f"must be greater than {limit}; got {_show(value)}"
        case "exclusiveMaximum":
            return f"must be less than {limit}; got {_show(value)}"
        case "multipleOf":
            return f"must be a multiple of {limit}; got {_show(value)}"
        case "minLength":
            return f"must be at least {limit} characters; got {size}"
        case "maxLength":
            return f"must be at most {limit} characters; got {size}"
        case "minItems":
            return f"must have at least {limit} items; got {size}"
        case "maxItems":
            return f"must have at most {limit} items; got {size}"
        case "pattern":
            return f"must match {limit}; got {_show(value)}"
        case "format":
            return f"must be a valid {limit}; got {_show(value)}"
    return None


def _types(types: list[Any]) -> str:
    names: list[str] = []
    for item in types:
        names += item if isinstance(item, list) else [str(item)]
    articles = {"integer": "an integer", "array": "an array", "object": "an object", "null": "null"}
    return " or ".join(articles.get(name, f"a {name}") for name in names)


def _path(parts: Iterable[str | int]) -> str:
    text = ""
    for part in parts:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
    return text


def _show(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."
