"""`higgs pick <generation>`: copy a Generation into the Project's `final/`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hfcore import generations
from hfcli.output import JsonOpt, print_json
from hfcli.projects import ProjectOpt, current_project


def pick(
    generation: Annotated[
        str,
        typer.Argument(help="A Generation file (a path, or a file name under generations/)."),
    ],
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Pick a Generation: copy it with its .json and Artifacts into final/."""
    target = current_project(project)
    picked = generations.pick(target, generation, cwd=Path.cwd())
    if as_json:
        print_json(picked)
        return
    extra = f" with {len(picked.artifacts)} Artifact(s)" if picked.artifacts else ""
    again = " (picked again; files refreshed)" if picked.already_picked else ""
    typer.echo(f"Picked {picked.generation.name}{extra} into {picked.generation.parent}{again}")
