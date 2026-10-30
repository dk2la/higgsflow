"""The convenience layer behind `higgs video ...` (and `higgs image ...`): common flags → one Endpoint's Requests.

A convenience command declares its options with the `...Opt` aliases below, collects the
common flags into a dict keyed like `hfcore.flags.FLAGS` and calls `generate` with its
Mode. `generate` resolves Model + Mode (+ tier) to one Endpoint, maps the flags onto the
Endpoint's parameters, adds `-p` parameters on top, and hands the body to
`hfcli.pipeline.run_requests`.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from hfcore import flags as common_flags
from hfcore.catalog import load_catalog
from hfcore.config import config_dir
from hfcli.output import print_json
from hfcli.params import parse_params
from hfcli.pipeline import echo_result, result_json, run_requests
from hfcli.projects import current_project

ModelOpt = Annotated[str | None, typer.Option("--model", "-m", help="Model slug or name, e.g. kling-3.0.")]
QualityOpt = Annotated[
    str | None,
    typer.Option("--quality", "-q", help="Quality tier (std, pro, 4k, fast) or the Model's own resolution/mode."),
]
DurationOpt = Annotated[str | None, typer.Option("--duration", "-d", help="Duration in seconds.")]
AspectOpt = Annotated[str | None, typer.Option("--aspect", "-a", help="Aspect ratio, e.g. 16:9.")]
ImageOpt = Annotated[
    str | None, typer.Option("--image", "-i", help="Start image: a file in the Project or a Reference (@name).")
]
EndImageOpt = Annotated[
    str | None, typer.Option("--end-image", help="End image: a file in the Project or a Reference (@name).")
]
PromptOpt = Annotated[str | None, typer.Option("--prompt", help="The prompt.")]
NameOpt = Annotated[
    str | None, typer.Option("--name", help="Slug of the Generation file names (default: first words of the prompt).")
]
ParamOpt = Annotated[
    list[str] | None,
    typer.Option("--param", "-p", help="Any other Endpoint parameter as key=value (applied last, like in higgs gen)."),
]
NoWaitOpt = Annotated[bool, typer.Option("--no-wait", help="Return the request ids right after submitting.")]


def generate(
    ctx: typer.Context,
    *,
    mode: str,
    model: str | None,
    flags: dict[str, Any],
    params: list[str] | None,
    name: str | None,
    project: str | None,
    yes: bool,
    no_wait: bool,
    as_json: bool,
) -> None:
    """Resolve the Endpoint, build the body from the flags and submit it; print the Requests."""
    target = current_project(project)
    catalog = load_catalog(config_dir())
    assert model is not None
    endpoint, quality_is_tier = common_flags.select_endpoint(catalog, model, mode, flags.get("quality"))
    if quality_is_tier:
        flags = {**flags, "quality": None}
    body = {**common_flags.map_flags(endpoint, flags), **parse_params(params or [])}
    batch = run_requests(ctx, target, endpoint.id, [body], name=name, yes=yes, no_wait=no_wait, quiet=as_json)
    if as_json:
        batch.raise_unfinished()
        print_json([result_json(result, batch.warnings) for result in batch.results])
        return
    for result in batch.results:
        echo_result(result, target)
    batch.raise_unfinished()
