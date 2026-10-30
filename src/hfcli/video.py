"""`higgs video <mode>`: video Requests with the common flags (DESIGN.md §7)."""

from __future__ import annotations

import typer

from hfcli.convenience import (
    AspectOpt,
    DurationOpt,
    EndImageOpt,
    ImageOpt,
    ModelOpt,
    NameOpt,
    NoWaitOpt,
    ParamOpt,
    PromptFileOpt,
    PromptOpt,
    QualityOpt,
    generate,
)
from hfcli.output import JsonOpt, NoInputOpt, YesOpt
from hfcli.projects import ProjectOpt

app = typer.Typer(name="video", help="Generate video with the common flags.", no_args_is_help=True)


@app.command("t2v")
def t2v(
    ctx: typer.Context,
    model: ModelOpt = None,
    quality: QualityOpt = None,
    duration: DurationOpt = None,
    aspect: AspectOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    name: NameOpt = None,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Text to video."""
    generate(
        ctx,
        kind="video",
        mode="t2v",
        model=model,
        flags={"prompt": prompt, "quality": quality, "duration": duration, "aspect": aspect},
        prompt_file=prompt_file,
        params=param,
        name=name,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("i2v")
def i2v(
    ctx: typer.Context,
    model: ModelOpt = None,
    image: ImageOpt = None,
    end_image: EndImageOpt = None,
    quality: QualityOpt = None,
    duration: DurationOpt = None,
    aspect: AspectOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    name: NameOpt = None,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Image to video: animate --image (optionally towards --end-image)."""
    generate(
        ctx,
        kind="video",
        mode="i2v",
        model=model,
        flags={
            "prompt": prompt,
            "image": image,
            "end_image": end_image,
            "quality": quality,
            "duration": duration,
            "aspect": aspect,
        },
        prompt_file=prompt_file,
        params=param,
        name=name,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )
