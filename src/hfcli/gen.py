"""`higgs gen <endpoint-id> -p key=value ...`: a raw call to any Endpoint."""

from __future__ import annotations

from typing import Annotated

import typer

from hfcore import credentials, downloads, lifecycle, requests
from hfcore.api import ApiClient
from hfcore.errors import HiggsError
from hfcli.output import JsonOpt, print_json, waiting_status
from hfcli.params import parse_params
from hfcli.projects import ProjectOpt, current_project


def gen(
    endpoint: Annotated[str, typer.Argument(help="Endpoint id, e.g. higgsfield-ai/soul/v2/standard.")],
    param: Annotated[
        list[str] | None,
        typer.Option("--param", "-p", help="A parameter as key=value; JSON values are parsed (5, true, [1,2])."),
    ] = None,
    no_validate: Annotated[bool, typer.Option("--no-validate", help="Do not validate against the catalog.")] = False,
    no_wait: Annotated[bool, typer.Option("--no-wait", help="Return the request id right after submitting.")] = False,
    name: Annotated[
        str | None, typer.Option("--name", help="Slug of the Generation file names (default: first words of the prompt).")
    ] = None,
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Submit a Request to any Endpoint."""
    # Until the catalog (#14) exists, validation must be skipped explicitly, so adding
    # it later never silently changes what an existing command does.
    if not no_validate:
        raise HiggsError(
            "Validation against the catalog is not implemented yet",
            hint="Pass --no-validate to submit the parameters as they are.",
            code="not_implemented",
        )
    params = parse_params(param or [])
    target = current_project(project)
    with ApiClient(credentials.load_credentials()) as client:
        record = lifecycle.submit_request(client, target, endpoint, params, name=name)
        path = requests.request_path(target, record.request_id)
        if no_wait:
            if as_json:
                print_json({"request_id": record.request_id, "path": str(path), "record": record})
            else:
                typer.echo(f"Submitted Request {record.request_id} ({record.status})")
                typer.echo(f"Record: {path}")
            return
        with waiting_status(record.request_id, quiet=as_json) as on_progress:
            record, status = lifecycle.wait_for_request(client, target, record, on_progress=on_progress)
        if record.status != "completed":
            raise lifecycle.RequestNotCompleted(record)
        generations = downloads.download_request(client, target, record, status)
    if as_json:
        print_json({"request_id": record.request_id, "path": str(path), "record": record, "generations": generations})
    else:
        typer.echo(f"Completed Request {record.request_id}")
        for generation in generations:
            typer.echo(f"  {generation.path.relative_to(target.path)}")
            for artifact in generation.artifacts:
                typer.echo(f"    + {artifact.name}")
