"""`<config dir>/models.local.toml`: Endpoints described by hand (DESIGN.md §6, "Models not in the docs").

The file is a list of `[[endpoint]]` tables; each one is a full Endpoint:

    [[endpoint]]
    id = "kling-video/v4.0/pro/image-to-video"   # the API path
    model = "Kling 4.0"                          # Model name; joins an existing Model of that name
    family = "kling"                             # Family id; a new id adds a Family
    mode = "i2v"
    price = { usd = "0.07", unit = "second" }    # optional

    [endpoint.parameters.duration]               # one table per parameter, in order
    type = "integer"
    minimum = 3
    maximum = 15

An entry whose `id` is already in the catalog replaces that Endpoint as a whole (it is not
merged). Prompt constraints are derived from the `prompt` / `negative_prompt` parameters, as
for documented Endpoints; `[endpoint.prompt]` adds the soft ones (`truncated_after`, `tokens`).
"""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from hfcore.catalog.model import (
    MODES,
    VIDEO_MODES,
    Catalog,
    Endpoint,
    Family,
    Model,
    Parameter,
    Price,
    PromptConstraints,
    model_slug,
)
from hfcore.errors import HiggsError

LOCAL_FILE = "models.local.toml"

_Strict = ConfigDict(extra="forbid")


class _LocalParameter(BaseModel):
    model_config = _Strict

    type: Literal["string", "integer", "number", "boolean", "array", "object"] | None = None
    nullable: bool = False
    required: bool = False
    description: str | None = None
    default: Any = None
    enum: list[Any] | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    multiple_of: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    item_type: Literal["string", "integer", "number", "boolean", "array", "object"] | None = None
    format: str | None = None


class _LocalPrompt(BaseModel):
    model_config = _Strict

    truncated_after: int | None = None
    tokens: list[str] = []


class _LocalPrice(BaseModel):
    model_config = _Strict

    usd: Decimal
    unit: Literal["second", "image"]
    qualifier: Literal["from", "exact"] = "from"


class _LocalEndpoint(BaseModel):
    model_config = _Strict

    id: str
    model: str
    family: str
    family_name: str | None = None
    mode: str
    tier: str | None = None
    title: str | None = None
    doc_url: str | None = None
    notes: list[str] = []
    price: _LocalPrice | None = None
    prompt: _LocalPrompt | None = None
    parameters: dict[str, _LocalParameter] = {}

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, mode: str) -> str:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; modes: {', '.join(MODES)}")
        return mode


class _LocalFile(BaseModel):
    model_config = _Strict

    endpoint: list[_LocalEndpoint] = []


def extend(catalog: Catalog, path: Path) -> Catalog:
    """`catalog` with the Endpoints described in `path` added (or replacing same-id ones)."""
    if not path.is_file():
        return catalog
    try:
        described = _LocalFile.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except tomllib.TOMLDecodeError as error:
        raise _invalid(path, str(error)) from None
    except ValidationError as error:
        raise _invalid(path, _describe(error)) from None

    families = {family.id: family for family in catalog.families}
    models = {model.slug: model for model in catalog.models}
    endpoints = {endpoint.id: endpoint for endpoint in catalog.endpoints}
    seen: set[str] = set()
    for entry in described.endpoint:
        endpoint = _endpoint(entry)
        if endpoint.id in seen:
            raise _invalid(path, f"endpoint {endpoint.id} is described twice")
        seen.add(endpoint.id)
        families.setdefault(entry.family, Family(id=entry.family, name=entry.family_name or entry.family))
        model = models.setdefault(
            endpoint.model, Model(slug=endpoint.model, name=entry.model, family=entry.family, kind=endpoint.kind)
        )
        if model.family != entry.family:
            raise _invalid(path, f"endpoint {endpoint.id}: model {model.slug} belongs to family {model.family}")
        endpoints[endpoint.id] = endpoint
    try:
        return Catalog(
            families=list(families.values()), models=list(models.values()), endpoints=list(endpoints.values())
        )
    except ValidationError as error:
        raise _invalid(path, "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in error.errors())) from None


def _endpoint(entry: _LocalEndpoint) -> Endpoint:
    parameters = [
        Parameter(name=name, **parameter.model_dump(exclude_defaults=True))
        for name, parameter in entry.parameters.items()
    ]
    kind = "video" if entry.mode in VIDEO_MODES else "image"
    tier = f" · {entry.tier}" if entry.tier else ""
    return Endpoint(
        id=entry.id.strip("/"),
        title=entry.title or f"{entry.model}{tier} · {entry.mode}",
        kind=kind,
        family=entry.family,
        model=model_slug(entry.model),
        mode=entry.mode,
        tier=entry.tier,
        doc_url=entry.doc_url,
        parameters=parameters,
        prompt=_prompt(parameters, entry.prompt),
        price=Price(**entry.price.model_dump()) if entry.price else None,
        notes=entry.notes,
        input_schema=_input_schema(entry.parameters),
    )


def _prompt(parameters: list[Parameter], soft: _LocalPrompt | None) -> PromptConstraints | None:
    by_name = {parameter.name: parameter for parameter in parameters}
    prompt = by_name.get("prompt")
    if prompt is None:
        return None
    negative = by_name.get("negative_prompt")
    return PromptConstraints(
        required=prompt.required,
        min_length=prompt.min_length,
        max_length=prompt.max_length,
        truncated_after=soft.truncated_after if soft else None,
        negative_prompt=negative is not None,
        negative_max_length=negative.max_length if negative else None,
        tokens=soft.tokens if soft else [],
    )


_SCHEMA_KEYS = {
    "description": "description",
    "default": "default",
    "minimum": "minimum",
    "maximum": "maximum",
    "multiple_of": "multipleOf",
    "min_length": "minLength",
    "max_length": "maxLength",
    "min_items": "minItems",
    "max_items": "maxItems",
    "format": "format",
}


def _input_schema(parameters: dict[str, _LocalParameter]) -> dict[str, Any]:
    """The JSON schema validation checks a described Endpoint against; unknown keys are rejected."""
    properties: dict[str, Any] = {}
    for name, parameter in parameters.items():
        prop: dict[str, Any] = {
            key: value
            for field, key in _SCHEMA_KEYS.items()
            if (value := getattr(parameter, field)) is not None
        }
        if parameter.type is not None:
            prop["type"] = [parameter.type, "null"] if parameter.nullable else parameter.type
        if parameter.enum is not None:
            prop["enum"] = [*parameter.enum, None] if parameter.nullable else parameter.enum
        if parameter.item_type is not None:
            prop["items"] = {"type": parameter.item_type}
        properties[name] = prop
    required = [name for name, parameter in parameters.items() if parameter.required]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _describe(error: ValidationError) -> str:
    problems = []
    for item in error.errors():
        where = ""
        for part in item["loc"]:
            where += f"[{part}]" if isinstance(part, int) else (f".{part}" if where else str(part))
        message = "unknown key" if item["type"] == "extra_forbidden" else str(item["msg"]).removeprefix("Value error, ")
        problems.append(f"{where}: {message}")
    return "; ".join(problems)


def _invalid(path: Path, problem: str) -> HiggsError:
    return HiggsError(
        f"Invalid {path}: {problem}",
        hint="See the models.local.toml section of the README for the format.",
        code="invalid_models_local",
    )
