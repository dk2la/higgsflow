"""The convenience layer's common flags and how they map onto an Endpoint (DESIGN.md §7).

`higgs video` (and later `higgs image`) take the same flags for every Model; each Endpoint
names the matching parameters its own way. `FLAGS` is the alias table: for each flag, the
parameter names that may carry it, most specific first. `map_flags` picks, per flag, the
first of them the Endpoint has, whose allowed values (if it lists any) accept the value,
and converts the value to the parameter's type. A flag none of whose parameters the
Endpoint has is an `unsupported_flag` error.

To support a new flag (`--video`, `--audio`, `--ref`, ...), add a `Flag` here and an
option to the command; to support a new parameter spelling, add it to that flag's
`params`. `--quality` is special: for a Model with quality tiers in the Mode it picks the
tier (`select_endpoint`), otherwise it maps like any other flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hfcore.catalog import Catalog, Endpoint, Parameter
from hfcore.errors import HiggsError


@dataclass(frozen=True)
class Flag:
    """A common flag: its key (as in `project.toml` `[defaults]`), its option and the parameters that carry it."""

    key: str
    option: str
    params: tuple[str, ...]


FLAGS: dict[str, Flag] = {
    flag.key: flag
    for flag in (
        Flag("prompt", "--prompt", ("prompt",)),
        Flag("image", "--image", ("image_url", "first_frame_url", "start_image_url")),
        Flag("end_image", "--end-image", ("end_image_url", "last_image_url", "last_frame_url", "tail_image_url")),
        Flag("duration", "--duration", ("duration",)),
        Flag("aspect", "--aspect", ("aspect_ratio",)),
        # Kling O3/Omni `mode` (std|pro|4k), else `resolution` (720p, 1080p, ...); image Models also have `quality`.
        Flag("quality", "--quality", ("mode", "resolution", "quality")),
        Flag("seed", "--seed", ("seed",)),
    )
}


def select_endpoint(catalog: Catalog, model: str, mode: str, quality: str | None) -> tuple[Endpoint, bool]:
    """The Endpoint for Model + Mode, and whether `quality` was used as its quality tier.

    For a Model with tiers in the Mode, `quality` picks the tier (`unknown_tier` when it is
    missing or not one of them); otherwise it is left for `map_flags`.
    """
    tiers = catalog.tiers(model, mode)
    if not tiers:
        return catalog.resolve(model, mode), False
    tier = quality.lower() if quality is not None else None
    return catalog.resolve(model, mode, tier), True


def map_flags(endpoint: Endpoint, flags: dict[str, Any]) -> dict[str, Any]:
    """The Endpoint parameters for the given flags (`{"aspect": "16:9"}` → `{"aspect_ratio": "16:9"}`).

    Flags whose value is None are skipped. Raises `unsupported_flag` for a flag the
    Endpoint has no parameter for.
    """
    params: dict[str, Any] = {}
    for key, value in flags.items():
        if value is None:
            continue
        parameter = flag_parameter(endpoint, key, value)
        if parameter is None:
            raise unsupported(endpoint, key)
        params[parameter.name] = convert(parameter, value)
    return params


def flag_parameter(endpoint: Endpoint, key: str, value: Any = None) -> Parameter | None:
    """The Endpoint parameter that carries flag `key` (for `value`, when given), or None."""
    present = [parameter for name in FLAGS[key].params for parameter in endpoint.parameters if parameter.name == name]
    if value is not None:
        for parameter in present:
            if parameter.enum is None or _enum_match(parameter, value) is not None:
                return parameter
    return present[0] if present else None


def convert(parameter: Parameter, value: Any) -> Any:
    """`value` as the parameter's type: `"5"` → 5 for an integer, an allowed value matched case-insensitively.

    A value that does not convert is returned as it is, for validation to report.
    """
    if parameter.enum is not None and (match := _enum_match(parameter, value)) is not None:
        return match
    if isinstance(value, str):
        if parameter.type in ("integer", "number"):
            try:
                number = float(value)
            except ValueError:
                return value
            return int(number) if number.is_integer() else number
    elif parameter.type == "string" and isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if parameter.type == "array" and not isinstance(value, list):
        return [value]
    return value


def _enum_match(parameter: Parameter, value: Any) -> Any:
    wanted = str(value).lower()
    for allowed in parameter.enum or []:
        if str(allowed).lower() == wanted:
            return allowed
    return None


def unsupported(endpoint: Endpoint, key: str) -> HiggsError:
    supported = [flag.option for flag in FLAGS.values() if flag_parameter(endpoint, flag.key) is not None]
    return HiggsError(
        f"{FLAGS[key].option} is not supported by {endpoint.model} {endpoint.mode}",
        hint=(
            f"Its flags: {', '.join(supported) or 'none'}; any other parameter goes in -p key=value "
            f"(see `higgs models show {endpoint.model}`)."
        ),
        code="unsupported_flag",
    )
