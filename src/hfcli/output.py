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
    err_console.print(f"[bold red]Error:[/] {error.message}", highlight=False)
    if error.hint:
        err_console.print(f"[dim]Hint:[/] {error.hint}", highlight=False)
