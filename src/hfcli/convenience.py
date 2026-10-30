"""The convenience layer behind `higgs video ...` and `higgs image ...`: common flags → one Endpoint's Requests.

A convenience command declares its options with the `...Opt` aliases below, collects the
common flags into a dict keyed like `hfcore.flags.FLAGS` (only the flags it offers; a
flag left out of the dict is never asked for) and calls `generate` with its kind
(`video`, `image`: the `[defaults.<kind>]` table) and Mode. `generate`:

1. reads `--prompt-file` into the `prompt` flag;
2. resolves Model + Mode (+ tier) to one Endpoint: `--model`, else the Project's default,
   else asked for; for a Model with quality tiers in the Mode, `--quality` is the tier;
3. fills the flags not given from `project.toml` defaults the Endpoint accepts, then asks
   for those still required (`hfcore.flags.missing_flags`);
4. maps the flags onto the Endpoint's parameters, puts `-p` parameters on top, makes one
   body per Request (`--count`, `hfcore.flags.vary_seeds`) and hands them to
   `hfcli.pipeline.run_requests`.

A new flag is an entry in `hfcore.flags.FLAGS`, an `...Opt` alias here and an option on
the commands that offer it. A flag that names something to look up (`--soul <name>`:
`souls.toml` → `custom_reference_id`, checked against the SOUL Model; `--preset <name>`)
is resolved between steps 2 and 3, once the Endpoint is known, by replacing the name in
`flags` with the looked-up value; from there on it maps like any other flag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from hfcore import flags as common_flags
from hfcore import prompts
from hfcore.catalog import Catalog, Endpoint, load_catalog
from hfcore.config import config_dir
from hfcore.errors import HiggsError
from hfcore.projects import load_config
from hfcli.output import ask, print_json
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
ImagesOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--image",
        "-i",
        help="Input/reference image, repeatable where the Model takes several: a file in the Project or a Reference (@name).",
    ),
]
PromptOpt = Annotated[str | None, typer.Option("--prompt", help="The prompt.")]
PromptFileOpt = Annotated[
    str | None,
    typer.Option("--prompt-file", help="Read the prompt from a file in the Project, e.g. prompts/scene.md."),
]
NameOpt = Annotated[
    str | None, typer.Option("--name", help="Slug of the Generation file names (default: first words of the prompt).")
]
ParamOpt = Annotated[
    list[str] | None,
    typer.Option("--param", "-p", help="Any other Endpoint parameter as key=value (applied last, like in higgs gen)."),
]
SeedOpt = Annotated[
    int | None, typer.Option("--seed", help="Seed; with --count N the Requests get this seed, +1, +2, ...")
]
CountOpt = Annotated[
    int,
    typer.Option(
        "--count",
        "-n",
        min=1,
        help="Submit N separate Requests (a different seed each, where the Model has seeds); the price is confirmed once.",
    ),
]
NoWaitOpt = Annotated[bool, typer.Option("--no-wait", help="Return the request ids right after submitting.")]


def generate(
    ctx: typer.Context,
    *,
    kind: str,
    mode: str,
    model: str | None,
    flags: dict[str, Any],
    prompt_file: str | None,
    params: list[str] | None,
    name: str | None,
    count: int,
    project: str | None,
    yes: bool,
    no_wait: bool,
    as_json: bool,
) -> None:
    """Resolve the Endpoint, build the body from the flags and submit it; print the Requests."""
    target = current_project(project)
    extra = parse_params(params or [])
    if prompt_file is not None:
        if flags.get("prompt") is not None:
            raise HiggsError(
                "--prompt and --prompt-file cannot be used together", hint="Pass one of them.", code="conflicting_flags"
            )
        flags = {**flags, "prompt": prompts.read_prompt_file(target, prompt_file, cwd=Path.cwd())}
    defaults = common_flags.project_defaults(load_config(target).defaults, kind)
    endpoint, flags = _endpoint(ctx, load_catalog(config_dir()), mode, model, flags, defaults)
    flags = common_flags.fill_defaults(endpoint, flags, defaults)
    for key in common_flags.missing_flags(endpoint, flags, extra):
        flag = common_flags.FLAGS[key]
        options = common_flags.choices(endpoint, key)
        question = flag.option.removeprefix("--").replace("-", " ").capitalize()
        flags[key] = ask(ctx, question, flag=flag.option, choices=options)
    body = {**common_flags.map_flags(endpoint, flags), **extra}
    bodies = common_flags.vary_seeds(endpoint, body, count)
    batch = run_requests(ctx, target, endpoint.id, bodies, name=name, yes=yes, no_wait=no_wait, quiet=as_json)
    if as_json:
        batch.raise_unfinished()
        print_json([result_json(result, batch.warnings) for result in batch.results])
        return
    for result in batch.results:
        echo_result(result, target)
    batch.raise_unfinished()


def _endpoint(
    ctx: typer.Context,
    catalog: Catalog,
    mode: str,
    model: str | None,
    flags: dict[str, Any],
    defaults: dict[str, Any],
) -> tuple[Endpoint, dict[str, Any]]:
    """The Endpoint for the Model and Mode, and the flags left to map.

    The Model is `--model`, else the Project's default Model if it has the Mode, else asked
    for. For a Model with quality tiers in the Mode, `--quality` (else the default quality
    if it is one of the tiers, else asked for) is the tier, so it is taken out of the flags.
    """
    default_model = defaults.get("model")
    if model is None and isinstance(default_model, str) and mode in catalog.modes(default_model):
        model = default_model
    if model is None:
        offered = [summary.slug for summary in catalog.summaries(mode=mode)]
        model = ask(ctx, f"Model for {mode}", flag="--model", choices=offered)
    slug = catalog.model(model).slug
    flags = dict(flags)
    tiers = catalog.tiers(slug, mode)
    if not tiers:
        return catalog.resolve(slug, mode), flags
    tier = flags.pop("quality", None)
    default_tier = str(defaults.get("quality", "")).lower()
    if tier is None and default_tier in tiers:
        tier = default_tier
    if tier is None:
        tier = ask(ctx, f"Quality tier of {slug} {mode}", flag="--quality", choices=tiers)
    return catalog.resolve(slug, mode, tier.lower()), flags
