"""`higgs gen <endpoint-id> -p key=value ...`: a raw call to any Endpoint."""

from __future__ import annotations

from typing import Annotated

import typer

from hfcli.output import JsonOpt, NoInputOpt, YesOpt, print_json
from hfcli.params import parse_params
from hfcli.pipeline import echo_result, result_json, run_requests
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
    batch = run_requests(
        ctx, target, endpoint, [params], validate=not no_validate, name=name, yes=yes, no_wait=no_wait, quiet=as_json
    )
    if as_json:
        batch.raise_unfinished()
        print_json(result_json(batch.results[0], batch.warnings))
        return
    echo_result(batch.results[0], target)
    batch.raise_unfinished()
