"""`higgs init` and `higgs projects`."""

from __future__ import annotations

from typing import Annotated

import typer

from hfcore import config, projects
from hfcli.output import JsonOpt, print_json


def init(
    name: Annotated[str, typer.Argument(help="Name of the new Project (a folder under the projects root).")],
    as_json: JsonOpt = False,
) -> None:
    """Create a Project with the standard layout."""
    project = projects.init_project(name)
    if as_json:
        print_json(project)
    else:
        typer.echo(f"Created Project {project.name} at {project.path}")


def list_projects(as_json: JsonOpt = False) -> None:
    """List the Projects under the projects root."""
    root = config.projects_root()
    found = projects.list_projects()
    if as_json:
        print_json({"projects_root": str(root), "projects": found})
        return
    if not found:
        typer.echo(f"No Projects in {root}. Create one with `higgs init <name>`.")
        return
    for project in found:
        typer.echo(f"{project.name}\t{project.path}")
