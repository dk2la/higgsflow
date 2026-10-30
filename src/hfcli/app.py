"""The `higgs` command: root app, global error handling and command registration."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from hfcore import __version__
from hfcore.errors import HiggsError
from hfcli import projects
from hfcli.output import JsonOpt, json_mode, print_json, render_error


class _HiggsGroup(TyperGroup):
    """Root group that turns `HiggsError` from any command into text/JSON + exit 1."""

    def invoke(self, ctx: typer.Context) -> Any:  # type: ignore[override]
        try:
            return super().invoke(ctx)
        except HiggsError as error:
            render_error(error, as_json=json_mode(ctx))
            raise typer.Exit(1) from None


app = typer.Typer(
    cls=_HiggsGroup,
    name="higgs",
    help="Unofficial CLI for the Higgsfield API, organised around Projects.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def root(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
    as_json: JsonOpt = False,
) -> None:
    if version:
        if as_json:
            print_json({"version": __version__})
        else:
            typer.echo(f"higgs {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


app.command()(projects.init)
app.command("projects")(projects.list_projects)


def main() -> None:
    app()
