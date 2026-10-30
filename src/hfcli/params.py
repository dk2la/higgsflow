"""Parsing `-p key=value` into JSON-typed Request parameters."""

from __future__ import annotations

import json
from typing import Any

from hfcore.errors import HiggsError


def parse_params(pairs: list[str]) -> dict[str, Any]:
    """`["a=1", "b=x"]` → `{"a": 1, "b": "x"}`; a repeated key keeps its last value."""
    return dict(_parse_pair(pair) for pair in pairs)


def _parse_pair(pair: str) -> tuple[str, Any]:
    key, sep, raw = pair.partition("=")
    key = key.strip()
    if not sep or not key:
        raise HiggsError(
            f"Invalid parameter {pair!r}",
            hint="Pass parameters as -p key=value.",
            code="invalid_param",
        )
    return key, parse_value(raw)


def parse_value(raw: str) -> Any:
    """A JSON number, boolean, null, array or object if `raw` is one, else the string itself.

    `5` → 5, `0.5` → 0.5, `true` → True, `null` → None, `[1,2]` → [1, 2],
    `16:9` → "16:9", `007` → "007" (not valid JSON).
    """
    try:
        return json.loads(raw, parse_constant=_reject_constant)
    except ValueError:
        return raw


def _reject_constant(name: str) -> Any:
    # json.loads accepts NaN/Infinity, which are not JSON; keep them as strings.
    raise ValueError(name)
