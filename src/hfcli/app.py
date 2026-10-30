"""The `higgs` command: root app, global error handling and command registration."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from hfcore import __version__
from hfcore.errors import HiggsError, HiggsWarning
from hfcli import auth, gen, models, projects, request_actions, tracking
from hfcli.output import JsonOpt, json_mode, print_json, render_error, render_warning


class _HiggsGroup(TyperGroup):
    """Root group that turns `HiggsError` from any command into text/JSON + exit 1,
    and prints each `HiggsWarning` to stderr as it happens."""

    def invoke(self, ctx: typer.Context) -> Any:  # type: ignore[override]
        with warnings.catch_warnings():  # restores the filters and showwarning afterwards
            warnings.simplefilter("always", HiggsWarning)
            warnings.showwarning = _show_warning(warnings.showwarning)
            try:
                return super().invoke(ctx)
            except HiggsError as error:
                render_error(error, as_json=json_mode(ctx))
                raise typer.Exit(1) from None


def _show_warning(fallback: Callable[..., None]) -> Callable[..., None]:
    def show(message: Warning | str, category: type[Warning], *args: Any, **kwargs: Any) -> None:
        if issubclass(category, HiggsWarning):
            render_warning(str(message))
        else:
            fallback(message, category, *args, **kwargs)

    return show


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
app.add_typer(auth.app, name="auth")
app.command()(gen.gen)
app.command()(request_actions.cancel)
app.command()(request_actions.delete)
app.command()(tracking.status)
app.command("list")(tracking.list_requests)
app.command()(tracking.sync)
app.command()(tracking.download)
app.add_typer(models.app)


def main() -> None:
    app()
