"""`higgs status`, `higgs list`, `higgs sync` and `higgs download`."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from hfcore import credentials, requests, tracking
from hfcore.api import ApiClient
from hfcore.errors import HiggsError
from hfcli.output import JsonOpt, console, err_console, print_json
from hfcli.projects import ProjectOpt, current_project


def _spinner(message: str, *, quiet: bool) -> AbstractContextManager[object]:
    """A spinner on stderr while a slow step runs; nothing in JSON mode."""
    return nullcontext() if quiet else err_console.status(message)


def status(
    request_id: Annotated[
        str | None, typer.Argument(help="A Request of the Project (default: every pending Request).")
    ] = None,
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Refresh and show the status of a Request, or of every pending one. Nothing is downloaded."""
    target = current_project(project)
    if request_id is not None:
        record = requests.load_request(target, request_id)
        with ApiClient(credentials.load_credentials()) as client:
            record, _ = tracking.refresh_request(client, target, record)
        path = requests.request_path(target, record.request_id)
        if as_json:
            print_json({"request_id": record.request_id, "status": record.status, "path": str(path), "record": record})
        else:
            typer.echo(f"Request {record.request_id}: {record.status}")
        return

    pending = tracking.pending_requests(target)
    if pending:
        with ApiClient(credentials.load_credentials()) as client:
            pending = [tracking.refresh_request(client, target, record)[0] for record in pending]
    if as_json:
        print_json({"requests": pending})
        return
    if not pending:
        typer.echo("No pending Requests")
    for record in pending:
        typer.echo(f"Request {record.request_id}: {record.status}")


def list_requests(
    status_filter: Annotated[
        list[str] | None,
        typer.Option("--status", help="Only Requests with this status (repeatable); `pending` = not finished yet."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Only this Model (slug or name, as `higgs models list` shows it), or Endpoint ids / model slugs containing this.",
        ),
    ] = None,
    mode: Annotated[str | None, typer.Option("--mode", help="Only this Mode (t2v, i2v, t2i, ...).")] = None,
    since: Annotated[
        datetime | None,
        typer.Option("--since", formats=["%Y-%m-%d"], metavar="YYYY-MM-DD", help="Only Requests submitted on or after this day."),
    ] = None,
    on: Annotated[
        datetime | None,
        typer.Option("--date", formats=["%Y-%m-%d"], metavar="YYYY-MM-DD", help="Only Requests submitted on this day."),
    ] = None,
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Show the Project's Request history from its local files (no API calls)."""
    target = current_project(project)
    found = tracking.find_requests(
        target,
        statuses=status_filter or (),
        model=model,
        mode=mode,
        since=since.date() if since else None,
        on=on.date() if on else None,
    )
    if as_json:
        print_json({"requests": found})
        return
    if not found:
        typer.echo("No Requests match" if (status_filter or model or mode or since or on) else "No Requests yet")
        return
    table = Table()
    for column in ("Submitted", "Request", "Model", "Mode", "Name", "Status", "Price", "Generations"):
        table.add_column(column, no_wrap=column in ("Submitted", "Request"))
    for record in found:
        naming = record.naming
        status_cell = record.status
        if record.deleted_at is not None:
            status_cell += f" (deleted {record.deleted_at.astimezone():%Y-%m-%d})"
        if record.recovered:
            status_cell += " (recovered)"
        table.add_row(
            f"{record.submitted_at.astimezone():%Y-%m-%d %H:%M}",
            record.request_id[:8],
            escape(naming.model_slug if naming else record.endpoint),
            naming.mode if naming else "",
            escape(f"{naming.slug} v{naming.version:02d}") if naming else "",
            escape(status_cell),
            f"${record.estimate_usd}" if record.estimate_usd else "",
            escape("\n".join(name.rsplit("/", 1)[-1] for name in record.generations)),
        )
    console.print(table)


def sync(project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    """Check every Request not downloaded yet once, and download the completed ones."""
    target = current_project(project)
    todo = tracking.requests_to_sync(target)
    synced = []
    if todo:
        with ApiClient(credentials.load_credentials()) as client:
            for record in todo:
                with _spinner(f"Syncing Request {record.request_id}...", quiet=as_json):
                    synced.append(tracking.sync_request(client, target, record))
    failed = [item for item in synced if item.outcome == "error"]
    error = (
        HiggsError(
            f"{len(failed)} of {len(synced)} Requests could not be synced",
            hint="Run `higgs sync` again; Requests already downloaded are skipped.",
            code="sync_incomplete",
        )
        if failed
        else None
    )
    if as_json:
        print_json({"requests": synced, **({"error": error.to_dict()} if error else {})})
        if error:
            raise typer.Exit(1)
        return
    if not synced:
        typer.echo("Nothing to sync")
    for item in synced:
        typer.echo(_synced_line(item))
        for file in item.files if item.outcome == "downloaded" else []:
            typer.echo(f"  {file}")
    if error:
        raise error


def download(
    request_id: Annotated[str, typer.Argument(help="A completed Request of the Project.")],
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Download a completed Request's Generations again (while its links are alive)."""
    target = current_project(project)
    record = requests.load_request(target, request_id)
    with ApiClient(credentials.load_credentials()) as client:
        with _spinner(f"Downloading Request {request_id}...", quiet=as_json):
            generations = tracking.download_again(client, target, record)
    if as_json:
        print_json({"request_id": record.request_id, "generations": generations})
        return
    typer.echo(f"Downloaded Request {record.request_id}")
    for generation in generations:
        typer.echo(f"  {generation.path.relative_to(target.path)}")
        for artifact in generation.artifacts:
            typer.echo(f"    + {artifact.name}")


def _synced_line(item: tracking.Synced) -> str:
    request = f"Request {item.request_id}"
    if item.outcome == "downloaded":
        return f"Downloaded {request}"
    if item.outcome == "pending":
        return f"{request} is still {item.status}"
    if item.outcome == "unsuccessful":
        return f"{request} ended {item.status}{f': {item.error}' if item.error else ''} (not billed)"
    if item.outcome == "expired":
        return f"{request} completed, but its links expired before it was downloaded"
    message = (item.failure or {}).get("message", "unknown error")
    return f"{request} could not be synced: {message}"
