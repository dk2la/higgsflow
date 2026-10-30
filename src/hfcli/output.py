"""Output conventions shared by every command.

Every command takes `--json` (`JsonOpt`). In JSON mode a command prints exactly one
JSON document to stdout; on failure it prints `{"error": {...}}` and exits non-zero.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.markup import escape

from hfcore.api import RequestStatus
from hfcore.errors import HiggsError

_JSON_KEY = "higgs.json"
_NO_INPUT_KEY = "higgs.no_input"

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


def _remember_no_input(ctx: typer.Context, value: bool) -> bool:
    if value:
        ctx.meta[_NO_INPUT_KEY] = True
    return value


# For commands that may prompt (a missing parameter, the price). `--json` implies `--no-input`.
NoInputOpt = Annotated[
    bool,
    typer.Option("--no-input", help="Never prompt; fail instead (for scripts).", callback=_remember_no_input),
]

# For commands that spend money (`hfcli.price.confirm_price`).
YesOpt = Annotated[
    bool,
    typer.Option("--yes", "-y", help="Confirm the price without asking (required with --json or --no-input)."),
]


def json_mode(ctx: typer.Context) -> bool:
    return bool(ctx.meta.get(_JSON_KEY))


def no_input(ctx: typer.Context) -> bool:
    """True when the command must not prompt: `--no-input`, or `--json` (which implies it)."""
    return json_mode(ctx) or bool(ctx.meta.get(_NO_INPUT_KEY))


def ask(
    ctx: typer.Context,
    question: str,
    *,
    flag: str,
    hide_input: bool = False,
    choices: list[str] | None = None,
) -> str:
    """Prompt for a missing value (one of `choices`, when given); with no input (`--no-input`, `--json`) fail instead."""
    if no_input(ctx):
        hint = f"Pass {flag}" + (f" ({', '.join(choices)})" if choices else "") + "; --json and --no-input never prompt."
        raise HiggsError(f"Missing {flag}", hint=hint, code="missing_param")
    if not choices:
        return str(typer.prompt(question, hide_input=hide_input, err=True))
    while True:  # ends with the answer, or with typer.Abort at the end of input
        answer = str(typer.prompt(f"{question} ({', '.join(choices)})", hide_input=hide_input, err=True)).strip()
        for choice in choices:
            if choice.lower() == answer.lower():
                return choice
        err_console.print(f"{escape(answer)!r} is not one of the choices.", highlight=False)


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


def render_warning(message: str) -> None:
    """A `HiggsWarning` from the core; always on stderr, so `--json` stdout stays clean."""
    # Not wrapped: warnings quote file paths the user may copy.
    err_console.print(f"[bold yellow]Warning:[/] {escape(message)}", highlight=False, soft_wrap=True)


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


@contextmanager
def waiting_status(request_id: str, *, quiet: bool) -> Iterator[Callable[[RequestStatus], None]]:
    """A spinner with the Request's latest status (on stderr); nothing when `quiet` (JSON mode)."""
    if quiet:
        yield lambda status: None
        return
    with err_console.status(f"Waiting for Request {request_id}...") as spinner:
        yield lambda status: spinner.update(f"Request {request_id}: {escape(status.status)}")
