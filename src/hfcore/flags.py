"""The convenience layer's common flags and how they map onto an Endpoint (DESIGN.md §7).

`higgs video` and `higgs image` take the same flags for every Model; each Endpoint
names the matching parameters its own way. `FLAGS` is the alias table: for each flag, the
parameter names that may carry it, most specific first. `map_flags` picks, per flag, the
first of them the Endpoint has, whose allowed values (if it lists any) accept the value,
and converts the value to the parameter's type. A flag none of whose parameters the
Endpoint has is an `unsupported_flag` error.

To support a new flag, add a `Flag` here and an option to the command; to support a new
parameter spelling, add it to that flag's `params` (or to `mode_params` when the spelling
means the flag in one Mode only: `--image` is Genjutsu motion transfer's `image_urls`, but
in video `ref` `image_urls` are the `--ref` images). `--quality` is special: for a Model
with quality tiers in the Mode (`Catalog.tiers`) it picks the tier and is not mapped;
otherwise it maps like any other flag. A repeatable flag (`--ref a.jpg --ref b.png`,
`higgs image --image a.jpg --image b.png`) has a list value: an array parameter takes it
as it is, a single-value parameter only a one-item list.

A flag whose value is a name to look up (`--soul <name>` → the Soul ID's
`custom_reference_id`, `--preset <name>`) is still a `Flag` here, with the parameters it
may land in; the command layer swaps the name for the looked-up value before `map_flags`.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from hfcore.catalog import Endpoint, Parameter
from hfcore.errors import HiggsError


@dataclass(frozen=True)
class Flag:
    """A common flag: its key (as in `project.toml` `[defaults]`), its option and the parameters that carry it."""

    key: str
    option: str
    params: tuple[str, ...]
    # Parameters that carry the flag in one Mode only, tried after `params`.
    mode_params: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def params_in(self, mode: str) -> tuple[str, ...]:
        """The parameters that may carry the flag on an Endpoint of `mode`, most specific first."""
        return self.params + self.mode_params.get(mode, ())


FLAGS: dict[str, Flag] = {
    flag.key: flag
    for flag in (
        Flag("prompt", "--prompt", ("prompt",)),
        # In motion, the character to animate: Genjutsu motion transfer takes it as a list.
        Flag("image", "--image", ("image_url", "first_frame_url", "start_image_url"), {"motion": ("image_urls",)}),
        Flag("end_image", "--end-image", ("end_image_url", "last_image_url", "last_frame_url", "tail_image_url")),
        Flag("duration", "--duration", ("duration",)),
        Flag("aspect", "--aspect", ("aspect_ratio",)),
        # Kling O3/Omni `mode` (std|pro|4k), else `resolution` (720p, 1080p, ...); image Models also have
        # `quality` (Grok, Marketing Studio: low|medium|high...) and Ideogram's `rendering_speed` (turbo|quality).
        Flag("quality", "--quality", ("mode", "resolution", "quality", "rendering_speed")),
        Flag("seed", "--seed", ("seed",)),
        # The video to edit, extend, restyle or take the motion from; a list of one where the
        # Endpoint takes several (Kling O3/Omni `video_urls`).
        Flag("video", "--video", ("video_url", "video_urls")),
        # Reference images (repeatable `--ref`), referred to in the prompt as <<<image_1>>>, ...
        Flag("ref", "--ref", ("image_urls",)),
        # Audio to lip-sync or score to; a list of one where the Endpoint takes several.
        Flag("audio", "--audio", ("audio_url", "audio_urls")),
        # `higgs image`'s repeatable `--image`: the input/reference images (a list). Kept apart from
        # video's `image` (the start frame); a single-image parameter takes a one-item list.
        Flag("images", "--image", ("image_urls", "image_url", "image_reference_url")),
    )
}


# Flags a Mode cannot do without, whatever the Endpoint's schema says (Kling 3.0 t2v has an
# optional prompt, but a text-to-video Request without one makes no sense). `ref` has none:
# its references may be images, videos or audio, and the schema says which it needs.
MODE_REQUIRED: dict[str, tuple[str, ...]] = {
    "t2v": ("prompt",),
    "i2v": ("image",),
    "vref": ("video",),
    "edit": ("video",),
    "extend": ("video",),
    "motion": ("video", "image"),
    "restyle": ("video",),
    "t2i": ("prompt",),
}

# The keys of `project.toml` `[defaults]` (and `[defaults.video]`, `[defaults.image]`) that
# fill flags that were not given; `model` is the `--model` default.
DEFAULT_KEYS = ("model", "quality", "duration", "aspect")


def map_flags(endpoint: Endpoint, flags: dict[str, Any]) -> dict[str, Any]:
    """The Endpoint parameters for the given flags (`{"aspect": "16:9"}` → `{"aspect_ratio": "16:9"}`).

    Flags whose value is None are skipped. Raises `unsupported_flag` for a flag the
    Endpoint has no parameter for, and `too_many_values` for a repeatable flag given
    several times whose parameter takes one value (`--image` twice on Ideogram's `image_url`).
    """
    params: dict[str, Any] = {}
    for key, value in flags.items():
        if value is None:
            continue
        parameter = flag_parameter(endpoint, key, value)
        if parameter is None:
            raise unsupported(endpoint, key, offered=flags)
        if isinstance(value, list) and parameter.type != "array" and len(value) > 1:
            raise HiggsError(
                f"{FLAGS[key].option} takes one value on {endpoint.model} {endpoint.mode}, got {len(value)}",
                hint=f"Its {parameter.name} is a single value; pass {FLAGS[key].option} once.",
                code="too_many_values",
            )
        params[parameter.name] = convert(parameter, value)
    return params


def missing_flags(endpoint: Endpoint, flags: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """The keys of `flags` that are None but needed: required by the Mode or by the Endpoint's schema.

    Only flags present in `flags` (the ones the command offers) count, and a parameter
    already given in `params` (`-p`) satisfies its flag. In `FLAGS` order.
    """
    found = []
    for key in FLAGS:
        if key not in flags or flags[key] is not None:
            continue
        parameter = flag_parameter(endpoint, key)
        if parameter is not None and parameter.name in params:
            continue
        if key in MODE_REQUIRED.get(endpoint.mode, ()) or (parameter is not None and parameter.required):
            found.append(key)
    return found


def choices(endpoint: Endpoint, key: str) -> list[str] | None:
    """The values flag `key` accepts on the Endpoint, when its parameter lists them."""
    parameter = flag_parameter(endpoint, key)
    if parameter is None or parameter.enum is None:
        return None
    return [str(value) for value in parameter.enum]


def vary_seeds(endpoint: Endpoint, body: dict[str, Any], count: int) -> list[dict[str, Any]]:
    """The bodies of `count` Requests (`--count N`) made from one body.

    Where the Endpoint has a `seed`, each Request gets its own: counting up from the seed
    in the body (`--seed 7` → 7, 8, 9), or else distinct random seeds within the seed's
    range (a single Request without a seed is left to the Endpoint). Without a seed the
    Requests share the body.
    """
    parameter = flag_parameter(endpoint, "seed")
    if parameter is None or (count == 1 and parameter.name not in body):
        return [dict(body) for _ in range(count)]
    start = body.get(parameter.name)
    if isinstance(start, int) and not isinstance(start, bool):
        seeds = [start + index for index in range(count)]
    else:
        low = max(int(parameter.minimum or 0), 0)
        high = int(parameter.maximum) if parameter.maximum is not None else 2**31 - 1
        seeds = random.sample(range(low, high + 1), count)
    return [{**body, parameter.name: seed} for seed in seeds]


def project_defaults(defaults: dict[str, Any], kind: str) -> dict[str, Any]:
    """The `DEFAULT_KEYS` of `project.toml` `[defaults]`, with `[defaults.<kind>]` (`video`, `image`) on top."""
    found = {key: value for key, value in defaults.items() if key in DEFAULT_KEYS}
    own = defaults.get(kind)
    if isinstance(own, dict):
        found.update({key: value for key, value in own.items() if key in DEFAULT_KEYS})
    return found


def fill_defaults(endpoint: Endpoint, flags: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """`flags` with each one that is None set to its default, where the Endpoint accepts that default.

    Defaults are Project-wide, so one the Endpoint has no parameter for, or whose parameter
    does not allow it (`duration = 5` for a Model of 6 or 10 seconds), is left out.
    """
    filled = dict(flags)
    for key, value in defaults.items():
        if key in filled and filled[key] is None and accepts(endpoint, key, value):
            filled[key] = value
    return filled


def accepts(endpoint: Endpoint, key: str, value: Any) -> bool:
    """Whether some parameter carrying flag `key` takes `value` (any value, when it lists none)."""
    return any(parameter.enum is None or _enum_match(parameter, value) is not None for parameter in _present(endpoint, key))


def _present(endpoint: Endpoint, key: str) -> list[Parameter]:
    """The Endpoint's parameters that may carry flag `key`, in the flag's order."""
    names = FLAGS[key].params_in(endpoint.mode)
    return [parameter for name in names for parameter in endpoint.parameters if parameter.name == name]


def flag_parameter(endpoint: Endpoint, key: str, value: Any = None) -> Parameter | None:
    """The Endpoint parameter that carries flag `key` (for `value`, when given), or None."""
    present = _present(endpoint, key)
    if value is not None:
        for parameter in present:
            if parameter.enum is None or _enum_match(parameter, value) is not None:
                return parameter
    return present[0] if present else None


def convert(parameter: Parameter, value: Any) -> Any:
    """`value` as the parameter's type: `"5"` → 5 for an integer, an allowed value matched case-insensitively.

    A value that does not convert is returned as it is, for validation to report.
    A one-item list (a repeatable flag given once) is that item for a parameter that is not an array.
    """
    if isinstance(value, list) and parameter.type != "array" and len(value) == 1:
        value = value[0]
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


def unsupported(endpoint: Endpoint, key: str, *, offered: Iterable[str] = FLAGS) -> HiggsError:
    """The `unsupported_flag` error, hinting at the flags of `offered` (the command's, in its order) the Endpoint has."""
    # `--image` is two flags (video's start frame, image's input images), so options are listed once.
    supported = list(
        dict.fromkeys(FLAGS[name].option for name in offered if flag_parameter(endpoint, name) is not None)
    )
    return HiggsError(
        f"{FLAGS[key].option} is not supported by {endpoint.model} {endpoint.mode}",
        hint=(
            f"Its flags: {', '.join(supported) or 'none'}; any other parameter goes in -p key=value "
            f"(see `higgs models show {endpoint.model}`)."
        ),
        code="unsupported_flag",
    )
