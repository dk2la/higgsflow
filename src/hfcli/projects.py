"""`higgs init`, `higgs projects`, and the current Project for project-scoped commands.

A project-scoped command takes `project: ProjectOpt = None` and calls
`current_project(project)` to get the Project it works in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hfcore import config, projects
from hfcore.projects import Project
from hfcli.output import JsonOpt, print_json

ProjectOpt = Annotated[
    str | None,
    typer.Option("--project", help="Work in this Project instead of the one around the current directory."),
]


def current_project(name: str | None) -> Project:
    """The Project a command works in: `--project <name>`, else found from the current directory."""
    return projects.current_project(Path.cwd(), name=name)


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
