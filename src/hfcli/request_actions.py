"""`higgs cancel <request-id>` and `higgs delete <request-id>`."""

from __future__ import annotations

from typing import Annotated

import typer

from hfcore import credentials, request_actions, requests
from hfcore.api import ApiClient
from hfcli.output import JsonOpt, print_json
from hfcli.projects import ProjectOpt, current_project

RequestIdArg = Annotated[str, typer.Argument(help="The request id of a Request in the Project.")]


def cancel(request_id: RequestIdArg, project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    """Cancel a queued Request (only before processing starts)."""
    target = current_project(project)
    with ApiClient(credentials.load_credentials()) as client:
        record = request_actions.cancel_request(client, target, request_id)
    path = requests.request_path(target, record.request_id)
    if as_json:
        print_json({"request_id": record.request_id, "status": record.status, "path": str(path), "record": record})
    else:
        typer.echo(f"Canceled Request {record.request_id}")


def delete(request_id: RequestIdArg, project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    """Delete a finished Request's data on the API side; local files stay."""
    target = current_project(project)
    with ApiClient(credentials.load_credentials()) as client:
        record = request_actions.delete_request(client, target, request_id)
    path = requests.request_path(target, record.request_id)
    if as_json:
        dumped = record.model_dump(mode="json")
        print_json({"request_id": record.request_id, "deleted_at": dumped["deleted_at"], "path": str(path), "record": dumped})
    else:
        typer.echo(f"Deleted Request {record.request_id} on the API side; local files are kept")
