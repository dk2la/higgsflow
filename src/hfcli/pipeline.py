"""The Generation lifecycle as one call, shared by `gen`, `video` and `image` (DESIGN.md §8).

`run_requests` takes the bodies of one or more Requests to the same Endpoint (several for
`--count N`) and walks them through the lifecycle together: validate every body → upload
the Inputs → estimate every body → confirm the total price once → submit every body →
(unless `no_wait`) wait for every Request and download the completed ones.

A command then prints the `Batch` (`result_json` / `echo_result`) and calls
`Batch.raise_unfinished()`, so a failed Request still fails the command after the
others were reported. In `--json` mode a Request that did not complete carries its error
in its own item (`result_json`), so one JSON document reports every Request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from hfcore import credentials, downloads, inputs, lifecycle, requests, validation
from hfcore.api import ApiClient
from hfcore.catalog import load_catalog
from hfcore.config import config_dir
from hfcore.projects import Project
from hfcore.requests import RequestRecord
from hfcore.spend import BudgetWarning
from hfcli.output import waiting_status
from hfcli.price import confirm_price


@dataclass
class RequestResult:
    """One submitted Request: its record, where it is, and its Generations once downloaded."""

    record: RequestRecord
    path: Path
    # None until downloaded: with `no_wait`, or when the Request did not complete.
    generations: list[downloads.DownloadedGeneration] | None = None
    # Set when the Request ended `failed`, `nsfw` or `canceled`.
    error: lifecycle.RequestNotCompleted | None = None


@dataclass
class Batch:
    """The Requests of one command and the Budget warnings they were confirmed with."""

    results: list[RequestResult]
    warnings: list[BudgetWarning]

    @property
    def unfinished(self) -> list[lifecycle.RequestNotCompleted]:
        """The errors of the Requests that ended `failed`, `nsfw` or `canceled`."""
        return [result.error for result in self.results if result.error is not None]

    def raise_unfinished(self) -> None:
        if self.unfinished:
            raise self.unfinished[0]


def run_requests(
    ctx: typer.Context,
    project: Project,
    endpoint: str,
    bodies: list[dict[str, Any]],
    *,
    validate: bool = True,
    name: str | None = None,
    yes: bool = False,
    no_wait: bool = False,
    quiet: bool = False,
) -> Batch:
    """Submit one Request per body (and, unless `no_wait`, wait for and download them all).

    Every body is validated before anything is uploaded, and every price is confirmed,
    as one total, before anything is submitted. The Requests share `name`, so they get
    consecutive `vNN`. `quiet` hides the waiting spinner (JSON mode).
    """
    cwd = Path.cwd()
    if validate:
        catalog = load_catalog(config_dir())
        for body in bodies:
            validation.validate_request(catalog, endpoint, body, cwd=cwd)
    with ApiClient(credentials.load_credentials()) as client:
        resolved = [inputs.resolve_inputs(client, project, body, cwd=cwd) for body in bodies]
        prices = [client.estimate(endpoint, item.params).usd for item in resolved]
        batch = Batch(results=[], warnings=confirm_price(ctx, project, prices, yes=yes).warnings)
        for item, price in zip(resolved, prices, strict=True):
            record = lifecycle.submit_request(
                client, project, endpoint, item.params, inputs=item.inputs, name=name, estimate_usd=price
            )
            batch.results.append(RequestResult(record=record, path=requests.request_path(project, record.request_id)))
        if no_wait:
            return batch
        for result in batch.results:
            with waiting_status(result.record.request_id, quiet=quiet) as on_progress:
                record, status = lifecycle.wait_for_request(client, project, result.record, on_progress=on_progress)
            result.record = record
            if record.status == "completed":
                result.generations = downloads.download_request(client, project, record, status)
            else:
                result.error = lifecycle.RequestNotCompleted(record)
    return batch


def result_json(result: RequestResult, warnings: list[BudgetWarning]) -> dict[str, Any]:
    """One Request in `--json` output: `request_id`, `path`, `record`, `generations` (once downloaded),
    `error` (when it did not complete), `warnings`."""
    data: dict[str, Any] = {"request_id": result.record.request_id, "path": str(result.path), "record": result.record}
    if result.generations is not None:
        data["generations"] = result.generations
    if result.error is not None:
        data["error"] = result.error.to_dict()
    data["warnings"] = warnings
    return data


def echo_result(result: RequestResult, project: Project) -> None:
    """One Request as text: where its record is, or the files it was downloaded to."""
    if result.generations is None:
        if result.record.is_terminal:
            return  # failed, nsfw or canceled: reported by `Batch.raise_unfinished`
        typer.echo(f"Submitted Request {result.record.request_id} ({result.record.status})")
        typer.echo(f"Record: {result.path}")
        return
    typer.echo(f"Completed Request {result.record.request_id}")
    for generation in result.generations:
        typer.echo(f"  {generation.path.relative_to(project.path)}")
        for artifact in generation.artifacts:
            typer.echo(f"    + {artifact.name}")
