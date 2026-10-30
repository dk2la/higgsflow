"""`higgs soul train|list|status`: Soul IDs, registered by name in `<config dir>/souls.toml` (DESIGN.md §7).

A Soul ID belongs to the account, not to a Project: `train` uploads its images from the
Project like Inputs, but the training is not a Request (nothing in `.hf/requests/`, no
Spend). `list` reads the registry only; `status` asks the API and updates it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from hfcore import credentials, souls
from hfcore.api import ApiClient
from hfcli.output import JsonOpt, err_console, print_json
from hfcli.projects import ProjectOpt, current_project

app = typer.Typer(
    name="soul", help="Train and list Soul IDs (account-scoped characters, used as --soul <name>).", no_args_is_help=True
)


@app.command("train")
def train(
    name: Annotated[str, typer.Argument(help="The name to register the Soul ID under, used as --soul <name>.")],
    images: Annotated[
        list[str] | None, typer.Argument(help="1–100 training images: files in the Project or References (@name).")
    ] = None,
    version: Annotated[
        str,
        typer.Option("--version", "-v", help="The SOUL Model it is for: v1 (soul), v2 (soul-v2) or cinema (soul-cinema)."),
    ] = souls.DEFAULT_VERSION,
    force: Annotated[
        bool, typer.Option("--force", help="Register under a name that is taken (the old Soul ID stays on the account).")
    ] = False,
    no_wait: Annotated[bool, typer.Option("--no-wait", help="Return right after the training starts.")] = False,
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Train a Soul ID from Project images and register it by name."""
    target = current_project(project)
    with ApiClient(credentials.load_credentials()) as client:
        soul = souls.train_soul(client, target, name, images or [], version=version, cwd=Path.cwd(), force=force)
        if not no_wait:
            with _waiting(soul, quiet=as_json) as on_progress:
                soul = souls.wait_for_soul(client, soul, on_progress=on_progress)
    if as_json:
        print_json(_soul_json(soul))
        return
    typer.echo(f"Soul ID {soul.name} ({soul.version}): {soul.status}")


@app.command("status")
def status(
    name: Annotated[str, typer.Argument(help="A registered Soul ID (see `higgs soul list`).")],
    as_json: JsonOpt = False,
) -> None:
    """Ask the API how a Soul ID's training is going and update souls.toml."""
    with ApiClient(credentials.load_credentials()) as client:
        soul = souls.refresh_soul(client, name)
    if as_json:
        print_json(_soul_json(soul))
        return
    typer.echo(f"Soul ID {soul.name} ({soul.version}): {soul.status}")


def _soul_json(soul: souls.Soul) -> dict[str, Any]:
    return {"soul": soul, "path": str(souls.souls_path())}


@contextmanager
def _waiting(soul: souls.Soul, *, quiet: bool) -> Iterator[souls.SoulProgress]:
    """A spinner with the training's latest status (on stderr); nothing when `quiet` (JSON mode)."""
    if quiet:
        yield lambda status: None
        return
    name = escape(soul.name)
    with err_console.status(f"Training Soul ID {name}...") as spinner:
        yield lambda status: spinner.update(f"Soul ID {name}: {escape(status.status)}")
