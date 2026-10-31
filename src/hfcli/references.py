"""`higgs ref ...`: References, the named files in `references/` used as `@name`."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from hfcore import generations, references
from hfcore.config import working_dir
from hfcli.output import JsonOpt, console, print_json
from hfcli.projects import ProjectOpt, current_project

app = typer.Typer(name="ref", help="Manage References (files in references/, used as @name).", no_args_is_help=True)


@app.command("save")
def save(
    generation: Annotated[str, typer.Argument(help="The Generation file to save, e.g. generations/2026-10-30/<file>.png.")],
    name: Annotated[str, typer.Argument(help="The Reference name, used as @name.")],
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing Reference with this name (any extension).")
    ] = False,
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Copy a Generation with its .json and Artifacts into references/ as <name>."""
    target = current_project(project)
    saved = generations.save_reference(target, generation, name, cwd=working_dir(), force=force)
    if as_json:
        print_json(saved)
    else:
        for old in saved.replaced:
            typer.echo(f"Replaced {old.relative_to(target.path)}")
        typer.echo(f"Saved Reference @{saved.name}: {saved.path.relative_to(target.path)}")


@app.command("list")
def list_references(project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    """List the References with their type, size and the Generation they came from."""
    target = current_project(project)
    found = references.list_references(target)
    if as_json:
        print_json({"references": found})
        return
    if not found:
        typer.echo("No References yet. Save one with `higgs ref save <generation> <name>` or put files in references/.")
        return
    table = Table()
    for header in ("Name", "File", "Type", "Size", "From"):
        table.add_column(header, no_wrap=header in ("Name", "Size"))
    for item in found:
        table.add_row(
            escape(f"@{item.name}"),
            escape(item.path.name),
            item.content_type or "—",
            f"{item.size:,} B",
            escape(item.generation or "—"),
        )
    console.print(table)
