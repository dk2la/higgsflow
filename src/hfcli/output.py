"""Output conventions shared by every command.

Every command takes `--json` (`JsonOpt`). In JSON mode a command prints exactly one
JSON document to stdout; on failure it prints `{"error": {...}}` and exits non-zero.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.markup import escape

from hfcore.errors import HiggsError

_JSON_KEY = "higgs.json"

console = Console()
err_console = Console(stderr=True)


def _remember_json(ctx: typer.Context, value: bool) -> bool:
    # ctx.meta is shared by the whole context chain, so the root error handler sees it.
    if value:
        ctx.meta[_JSON_KEY] = True
    return value


JsonOpt = Annotated[
    bool,
    typer.Option("--json", help="Print machine-readable JSON.", callback=_remember_json),
]


def json_mode(ctx: typer.Context) -> bool:
    return bool(ctx.meta.get(_JSON_KEY))


def ask(ctx: typer.Context, question: str, *, flag: str, hide_input: bool = False) -> str:
    """Prompt for a missing value; in JSON mode (which implies no input) fail instead."""
    if json_mode(ctx):
        raise HiggsError(f"Missing {flag}", hint=f"Pass {flag}; --json never prompts.", code="missing_param")
    return str(typer.prompt(question, hide_input=hide_input, err=True))


def _jsonable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    if isinstance(data, (list, tuple)):
        return [_jsonable(item) for item in data]
    if isinstance(data, dict):
        return {key: _jsonable(value) for key, value in data.items()}
    return data


def print_json(data: Any) -> None:
    typer.echo(json.dumps(_jsonable(data), indent=2, ensure_ascii=False))


def render_error(error: HiggsError, *, as_json: bool) -> None:
    if as_json:
        print_json({"error": error.to_dict()})
        return
    # Messages may quote API responses, so escape them rather than read them as Rich markup.
    err_console.print(f"[bold red]Error:[/] {escape(error.message)}", highlight=False)
    if error.hint:
        err_console.print(f"[dim]Hint:[/] {escape(error.hint)}", highlight=False)
    # Context fields such as the HTTP status and X-Correlation-ID (for support). Lists and
    # objects (validation problems) are for --json; the message already spells them out.
    for key, value in error.to_dict().items():
        if key not in ("code", "message", "hint") and value is not None and not isinstance(value, (list, dict)):
            label = key.replace("_", " ").capitalize()
            err_console.print(f"[dim]{label}:[/] {escape(str(value))}", highlight=False)
