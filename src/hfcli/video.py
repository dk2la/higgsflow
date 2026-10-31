"""`higgs video <mode>`: video Requests with the common flags (DESIGN.md §7).

Each Mode is a thin command: it declares the options that make sense for the Mode and
passes them to `hfcli.convenience.generate` as flags keyed like `hfcore.flags.FLAGS`.

Every Mode takes `--preset`, so a Model without Presets fails with the project's own
`unsupported_flag` error (a JSON error under `--json`) rather than Typer's "No such option";
only `restyle` has Video Models with a Preset catalog today.
"""

from __future__ import annotations

import typer

from hfcli.convenience import (
    AspectOpt,
    AudioOpt,
    CountOpt,
    DurationOpt,
    EndImageOpt,
    ImageOpt,
    ModelOpt,
    NameOpt,
    NoWaitOpt,
    ParamOpt,
    PresetOpt,
    PromptFileOpt,
    PromptOpt,
    QualityOpt,
    RefOpt,
    SeedOpt,
    VideoOpt,
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
    seed: SeedOpt = None,
    preset: PresetOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
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
        flags={
            "prompt": prompt,
            "quality": quality,
            "duration": duration,
            "aspect": aspect,
            "seed": seed,
            "preset": preset,
        },
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
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
    seed: SeedOpt = None,
    preset: PresetOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
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
            "seed": seed,
            "preset": preset,
        },
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("ref")
def reference(
    ctx: typer.Context,
    model: ModelOpt = None,
    ref: RefOpt = None,
    video: VideoOpt = None,
    audio: AudioOpt = None,
    image: ImageOpt = None,
    end_image: EndImageOpt = None,
    quality: QualityOpt = None,
    duration: DurationOpt = None,
    aspect: AspectOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    seed: SeedOpt = None,
    preset: PresetOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Reference to video: a new video with the people and things of --ref (and --video, --audio)."""
    generate(
        ctx,
        kind="video",
        mode="ref",
        model=model,
        flags={
            "prompt": prompt,
            "image": image,
            "end_image": end_image,
            "ref": ref or None,
            "video": video,
            "audio": audio,
            "quality": quality,
            "duration": duration,
            "aspect": aspect,
            "seed": seed,
            "preset": preset,
        },
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("edit")
def edit(
    ctx: typer.Context,
    model: ModelOpt = None,
    video: VideoOpt = None,
    ref: RefOpt = None,
    audio: AudioOpt = None,
    quality: QualityOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    preset: PresetOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Edit --video as the prompt says (Genjutsu object swap: put the --ref object in)."""
    generate(
        ctx,
        kind="video",
        mode="edit",
        model=model,
        flags={
            "prompt": prompt,
            "video": video,
            "ref": ref or None,
            "audio": audio,
            "quality": quality,
            "preset": preset,
        },
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("vref")
def vref(
    ctx: typer.Context,
    model: ModelOpt = None,
    video: VideoOpt = None,
    ref: RefOpt = None,
    quality: QualityOpt = None,
    duration: DurationOpt = None,
    aspect: AspectOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    preset: PresetOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Video reference: a new video in the style or camera work of --video."""
    generate(
        ctx,
        kind="video",
        mode="vref",
        model=model,
        flags={
            "prompt": prompt,
            "video": video,
            "ref": ref or None,
            "quality": quality,
            "duration": duration,
            "aspect": aspect,
            "preset": preset,
        },
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("extend")
def extend(
    ctx: typer.Context,
    model: ModelOpt = None,
    video: VideoOpt = None,
    ref: RefOpt = None,
    audio: AudioOpt = None,
    quality: QualityOpt = None,
    duration: DurationOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    preset: PresetOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Continue --video as the prompt says."""
    generate(
        ctx,
        kind="video",
        mode="extend",
        model=model,
        flags={
            "prompt": prompt,
            "video": video,
            "ref": ref or None,
            "audio": audio,
            "quality": quality,
            "duration": duration,
            "preset": preset,
        },
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("motion")
def motion(
    ctx: typer.Context,
    model: ModelOpt = None,
    video: VideoOpt = None,
    image: ImageOpt = None,
    quality: QualityOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    preset: PresetOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Motion control: the character of --image moves like the one in --video."""
    generate(
        ctx,
        kind="video",
        mode="motion",
        model=model,
        flags={"prompt": prompt, "video": video, "image": image, "quality": quality, "preset": preset},
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )


@app.command("restyle")
def restyle(
    ctx: typer.Context,
    model: ModelOpt = None,
    video: VideoOpt = None,
    ref: RefOpt = None,
    quality: QualityOpt = None,
    preset: PresetOpt = None,
    prompt: PromptOpt = None,
    prompt_file: PromptFileOpt = None,
    name: NameOpt = None,
    count: CountOpt = 1,
    param: ParamOpt = None,
    no_wait: NoWaitOpt = False,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Restyle --video with a Preset (--preset, see `higgs presets list restyle`)."""
    generate(
        ctx,
        kind="video",
        mode="restyle",
        model=model,
        flags={"prompt": prompt, "video": video, "ref": ref or None, "quality": quality, "preset": preset},
        prompt_file=prompt_file,
        params=param,
        name=name,
        count=count,
        project=project,
        yes=yes,
        no_wait=no_wait,
        as_json=as_json,
    )
