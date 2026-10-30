"""`higgs gen <endpoint-id> -p key=value ...`: a raw call to any Endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hfcore import credentials, inputs, lifecycle, requests
from hfcore.api import ApiClient
from hfcore.errors import HiggsError
from hfcli.output import JsonOpt, print_json
from hfcli.params import parse_params
from hfcli.projects import ProjectOpt, current_project


def gen(
    endpoint: Annotated[str, typer.Argument(help="Endpoint id, e.g. higgsfield-ai/soul/v2/standard.")],
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            "-p",
            help=(
                "A parameter as key=value; JSON values are parsed (5, true, [1,2]). "
                "A local file path (inputs/a.jpg) is uploaded and replaced by its URL."
            ),
        ),
    ] = None,
    no_validate: Annotated[bool, typer.Option("--no-validate", help="Do not validate against the catalog.")] = False,
    no_wait: Annotated[bool, typer.Option("--no-wait", help="Return the request id right after submitting.")] = False,
    project: ProjectOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Submit a Request to any Endpoint."""
    # Until the catalog (#14) and polling (#8) exist, both must be skipped explicitly,
    # so adding them later never silently changes what an existing command does.
    if not no_validate:
        raise HiggsError(
            "Validation against the catalog is not implemented yet",
            hint="Pass --no-validate to submit the parameters as they are.",
            code="not_implemented",
        )
    if not no_wait:
        raise HiggsError(
            "Waiting for the result is not implemented yet",
            hint="Pass --no-wait to return right after the submit.",
            code="not_implemented",
        )
    params = parse_params(param or [])
    target = current_project(project)
    with ApiClient(credentials.load_credentials()) as client:
        resolved = inputs.resolve_inputs(client, target, params, cwd=Path.cwd())
        record = lifecycle.submit_request(client, target, endpoint, resolved.params, inputs=resolved.inputs)
    path = requests.request_path(target, record.request_id)
    if as_json:
        print_json({"request_id": record.request_id, "path": str(path), "record": record})
    else:
        typer.echo(f"Submitted Request {record.request_id} ({record.status})")
        typer.echo(f"Record: {path}")
