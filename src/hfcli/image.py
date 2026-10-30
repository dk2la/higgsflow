"""`higgs image <mode>`: image Requests with the common flags (DESIGN.md §7).

Each Mode is a thin command, like `higgs video <mode>`: it declares the options that make
sense for the Mode and passes them to `hfcli.convenience.generate` as flags keyed like
`hfcore.flags.FLAGS`.
"""

from __future__ import annotations

import typer

from hfcli.convenience import (
    AspectOpt,
    CountOpt,
    ModelOpt,
    NameOpt,
    NoWaitOpt,
    ParamOpt,
    PromptFileOpt,
    PromptOpt,
    QualityOpt,
    SeedOpt,
    generate,
)
from hfcli.output import JsonOpt, NoInputOpt, YesOpt
from hfcli.projects import ProjectOpt

app = typer.Typer(name="image", help="Generate images with the common flags.", no_args_is_help=True)


@app.command("t2i")
def t2i(
    ctx: typer.Context,
    model: ModelOpt = None,
    quality: QualityOpt = None,
    aspect: AspectOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    seed: SeedOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Text to image."""
    generate(
        ctx,
        kind="image",
        mode="t2i",
        model=model,
        flags={"prompt": prompt, "quality": quality, "aspect": aspect, "seed": seed},
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )
