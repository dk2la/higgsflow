"""`higgs auth login`."""

from __future__ import annotations

from typing import Annotated

import typer

from hfcore import credentials
from hfcli.output import JsonOpt, ask, print_json

app = typer.Typer(help="Manage the Higgsfield API key.", no_args_is_help=True)


@app.command()
def login(
    ctx: typer.Context,
    key: Annotated[
        str | None,
        typer.Option("--key", help="KEY_ID:KEY_SECRET. Prompted for (hidden) when omitted."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Save the API key to the credentials file (mode 600)."""
    if key is None:
        key = ask(ctx, "API key (KEY_ID:KEY_SECRET)", flag="--key", hide_input=True)
    saved = credentials.parse_key(key)
    path = credentials.save_credentials(saved)
    if as_json:
        print_json({"key_id": saved.key_id, "credentials_file": str(path)})
    else:
        typer.echo(f"Saved key {saved.key_id} to {path}")
