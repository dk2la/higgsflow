"""`higgs image <mode>`: image Requests with the common flags (DESIGN.md §7).

Each Mode is a thin command, like `higgs video <mode>`: it declares the options that make
sense for the Mode and passes them to `hfcli.convenience.generate` as flags keyed like
`hfcore.flags.FLAGS`.

`t2i` covers the 14 text-to-image Endpoints, including those that also take reference
images (`--image`, repeatable where the Model takes several); `i2i` is Qwen Image 3 edit.
Ads Studio (Mode `ads`) is reachable only via `higgs gen`. A Model's own `batch_size` is a
`-p` parameter: one Request, several Generations (`..._v01-1.png`, `-2`, ...).

`--soul` and `--preset` (both Modes) are added here as options passed in the `flags` dict
like the others; see `hfcli.convenience` for where their names are looked up.
"""

from __future__ import annotations

import typer

from hfcli.convenience import (
    AspectOpt,
    CountOpt,
    ImagesOpt,
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
    image: ImagesOpt = None,
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
    """Text to image, optionally guided by reference images (--image, where the Model takes them)."""
    generate(
        ctx,
        kind="image",
        mode="t2i",
        model=model,
        flags={"prompt": prompt, "images": image, "quality": quality, "aspect": aspect, "seed": seed},
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("i2i")
def i2i(
    ctx: typer.Context,
    model: ModelOpt = None,
    image: ImagesOpt = None,
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
    """Image to image: edit the --image(s) as the prompt says."""
    generate(
        ctx,
        kind="image",
        mode="i2i",
        model=model,
        flags={"prompt": prompt, "images": image, "quality": quality, "aspect": aspect, "seed": seed},
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )
