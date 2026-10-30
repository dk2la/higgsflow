"""`higgs presets list <catalog>`: the Preset catalogs, fetched live from the API (ADR 0004)."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from hfcore import presets as core
from hfcore.api import ApiClient
from hfcore.catalog import load_catalog
from hfcore.config import config_dir
from hfcore.credentials import load_credentials
from hfcli.output import JsonOpt, console, print_json

app = typer.Typer(name="presets", help="Browse the Preset catalogs (needs the API key).", no_args_is_help=True)


@app.command("list")
def list_presets(
    catalog: Annotated[str, typer.Argument(help=f"The catalog: {', '.join(core.CATALOGS)}.")],
    as_json: JsonOpt = False,
) -> None:
    """List a Preset catalog: name, id and what it applies to; pass the name or id as --preset."""
    core.catalog_sources(catalog)  # an unknown catalog fails before the key is needed
    with ApiClient(load_credentials()) as client:
        found = core.list_presets(client, catalog, load_catalog(config_dir()))
    if as_json:
        print_json({"catalog": catalog, "presets": found})
        return
    if not found:
        typer.echo(f"No Presets in {catalog}")
        return
    table = Table()
    # Names and ids are typed back as --preset, so they are never wrapped or cut.
    table.add_column("Name", no_wrap=True)
    table.add_column("ID", no_wrap=True)
    extra = [
        (header, field)
        for header, field in (("Group", "group"), ("Aspect", "aspect_ratio"), ("Description", "description"))
        if any(getattr(preset, field) for preset in found)
    ]
    for header, _ in extra:
        table.add_column(header)
    table.add_column("Models")
    for preset in found:
        cells = [getattr(preset, field) or "" for _, field in extra]
        table.add_row(escape(preset.name), preset.id, *(escape(cell) for cell in cells), ", ".join(preset.models))
    console.print(table)
