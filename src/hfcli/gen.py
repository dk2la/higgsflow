"""`higgs gen <endpoint-id> -p key=value ...`: a raw call to any Endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hfcore import credentials, downloads, inputs, lifecycle, requests, validation
from hfcore.api import ApiClient
from hfcore.catalog import load_catalog
from hfcore.config import config_dir
from hfcli.output import JsonOpt, NoInputOpt, YesOpt, print_json, waiting_status
from hfcli.params import parse_params
from hfcli.price import confirm_price
from hfcli.projects import ProjectOpt, current_project


def gen(
    ctx: typer.Context,
    endpoint: Annotated[str, typer.Argument(help="Endpoint id, e.g. higgsfield-ai/soul/v2/standard.")],
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            "-p",
            help=(
                "A parameter as key=value; JSON values are parsed (5, true, [1,2]). "
                "A local file path (inputs/a.jpg) or Reference (@hero) is uploaded and replaced by its URL; "
                "@@ sends a literal @."
            ),
        ),
    ] = None,
    no_validate: Annotated[bool, typer.Option("--no-validate", help="Do not validate against the catalog.")] = False,
    no_wait: Annotated[bool, typer.Option("--no-wait", help="Return the request id right after submitting.")] = False,
    name: Annotated[
        str | None, typer.Option("--name", help="Slug of the Generation file names (default: first words of the prompt).")
    ] = None,
    project: ProjectOpt = None,
    yes: YesOpt = False,
    no_input: NoInputOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Submit a Request to any Endpoint."""
    params = parse_params(param or [])
    target = current_project(project)
    if not no_validate:
        validation.validate_request(load_catalog(config_dir()), endpoint, params, cwd=Path.cwd())
    with ApiClient(credentials.load_credentials()) as client:
        resolved = inputs.resolve_inputs(client, target, params, cwd=Path.cwd())
        price = client.estimate(endpoint, resolved.params).usd
        warnings = confirm_price(ctx, target, [price], yes=yes).warnings
        record = lifecycle.submit_request(
            client, target, endpoint, resolved.params, inputs=resolved.inputs, name=name, estimate_usd=price
        )
        path = requests.request_path(target, record.request_id)
        if no_wait:
            if as_json:
                print_json({"request_id": record.request_id, "path": str(path), "record": record, "warnings": warnings})
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
        print_json(
            {
                "request_id": record.request_id,
                "path": str(path),
                "record": record,
                "generations": generations,
                "warnings": warnings,
            }
        )
    else:
        typer.echo(f"Completed Request {record.request_id}")
        for generation in generations:
            typer.echo(f"  {generation.path.relative_to(target.path)}")
            for artifact in generation.artifacts:
                typer.echo(f"    + {artifact.name}")
