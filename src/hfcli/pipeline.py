"""The Generation lifecycle as one call, shared by `gen`, `video` and `image` (DESIGN.md §8).

`run_requests` takes the bodies of one or more Requests to the same Endpoint (several for
`--count N`) and walks them through the lifecycle together: validate every body → upload
the Inputs → estimate every body → confirm the total price once → submit every body →
(unless `no_wait`) wait for every Request and download the completed ones.

A command then prints the `Batch` and fails if anything did not complete: `gen` and text
output call `Batch.raise_unfinished()` after printing, a convenience command under `--json`
prints `batch_json` and exits 1. So a failed Request still fails the command after the
others were reported. One Request's trouble never hides the others: a Request that did
not complete, or whose wait or download gave up, keeps its error on its own result, and a
submit that gives up partway through `--count N` stops the submits but the Requests already
submitted (and billed once they complete) are still waited for and reported. In `--json`
mode every Request has its own item (`batch_json`), so one JSON document reports them all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from hfcore import credentials, downloads, inputs, lifecycle, requests, validation
from hfcore.api import ApiClient
from hfcore.catalog import load_catalog
from hfcore.config import config_dir, working_dir
from hfcore.errors import HiggsError
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
    # Set when the Request ended `failed`, `nsfw` or `canceled`, or its wait or download gave up.
    error: HiggsError | None = None


@dataclass
class Batch:
    """The Requests of one command and the Budget warnings they were confirmed with."""

    results: list[RequestResult]
    warnings: list[BudgetWarning]
    # A submit that gave up partway through `--count N`, and how many bodies it left unsubmitted.
    submit_error: HiggsError | None = None
    not_submitted: int = 0

    @property
    def unfinished(self) -> list[HiggsError]:
        """Every error of the command: the Requests that did not complete, then a failed submit."""
        errors = [result.error for result in self.results if result.error is not None]
        return errors + ([self.submit_error] if self.submit_error is not None else [])

    def raise_unfinished(self) -> None:
        errors = self.unfinished
        if len(errors) == 1:
            raise errors[0]
        if errors:
            hints = list(dict.fromkeys(error.hint for error in errors if error.hint))
            raise HiggsError(
                f"{len(errors)} problems with the Requests of this command:\n"
                + "\n".join(f"- {error.message}" for error in errors),
                hint=" ".join(hints) or None,
                code="requests_unfinished",
            )


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
    cwd = working_dir()
    if validate:
        catalog = load_catalog(config_dir())
        for body in bodies:
            validation.validate_request(catalog, endpoint, body, cwd=cwd)
    with ApiClient(credentials.load_credentials()) as client:
        resolved = [inputs.resolve_inputs(client, project, body, cwd=cwd) for body in bodies]
        prices = [client.estimate(endpoint, item.params).usd for item in resolved]
        batch = Batch(results=[], warnings=confirm_price(ctx, project, prices, yes=yes).warnings)
        for index, (item, price) in enumerate(zip(resolved, prices, strict=True)):
            try:
                record = lifecycle.submit_request(
                    client, project, endpoint, item.params, inputs=item.inputs, name=name, estimate_usd=price
                )
            except HiggsError as error:
                if not batch.results:
                    if len(bodies) > 1:
                        error.hint = _join(error.hint, _none_submitted(error, len(bodies)))
                    raise
                batch.submit_error = error
                batch.not_submitted = len(bodies) - index
                error.hint = _join(error.hint, _partly_submitted(batch, len(bodies)))
                break
            batch.results.append(RequestResult(record=record, path=requests.request_path(project, record.request_id)))
        if no_wait:
            return batch
        for result in batch.results:
            try:
                with waiting_status(result.record.request_id, quiet=quiet) as on_progress:
                    record, status = lifecycle.wait_for_request(client, project, result.record, on_progress=on_progress)
                result.record = record
                if record.status == "completed":
                    result.generations = downloads.download_request(client, project, record, status)
                else:
                    result.error = lifecycle.RequestNotCompleted(record)
            except HiggsError as error:  # the others are still waited for; this one is reported
                result.error = error
    return batch


def _none_submitted(error: HiggsError, requested: int) -> str:
    """What the first submit of `--count N` giving up means for the whole batch."""
    if not lifecycle.submit_may_have_reached_api(error):
        return f"None of the {requested} Requests was submitted."
    others = requested - 1
    return f"The other {others} {_requests(others)} {_were(others)} not submitted."


def _partly_submitted(batch: Batch, requested: int) -> str:
    """What a submit giving up partway through `--count N` means for the whole batch.

    The Request whose submit gave up counts as not submitted only when the API refused it;
    after a network failure or a 5xx it may exist and be billed (its error says so).
    """
    assert batch.submit_error is not None
    submitted = len(batch.results)
    ids = ", ".join(result.record.request_id for result in batch.results)
    billed = "is billed once it completes" if submitted == 1 else "are billed once they complete"
    done = f"{submitted} of the {requested} Requests {_were(submitted)} submitted before this ({ids}) and {billed}"
    if not lifecycle.submit_may_have_reached_api(batch.submit_error):
        return f"{done}; the other {batch.not_submitted} {_were(batch.not_submitted)} not submitted."
    rest = batch.not_submitted - 1
    tail = f", and the other {rest} {_were(rest)} not submitted." if rest else "."
    return f"{done}; this one may have been submitted too (see above){tail}"


def _requests(count: int) -> str:
    return "Request" if count == 1 else "Requests"


def _were(count: int) -> str:
    return "was" if count == 1 else "were"


def _join(hint: str | None, more: str) -> str:
    return f"{hint} {more}" if hint else more


def batch_json(batch: Batch) -> list[dict[str, Any]]:
    """A convenience command's `--json` output: one item per requested Request, in order.

    A body left unsubmitted by a failed submit is `{"request_id": null, "error": ..., "warnings": ...}`.
    """
    items = [result_json(result, batch.warnings) for result in batch.results]
    if batch.submit_error is not None:
        error = batch.submit_error.to_dict()
        items += [
            {"request_id": None, "error": dict(error), "warnings": batch.warnings} for _ in range(batch.not_submitted)
        ]
    return items


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
        if result.error is not None and result.record.is_terminal:
            return  # failed, nsfw or canceled: reported by `Batch.raise_unfinished`
        typer.echo(f"Submitted Request {result.record.request_id} ({result.record.status})")
        typer.echo(f"Record: {result.path}")
        return
    typer.echo(f"Completed Request {result.record.request_id}")
    for generation in result.generations:
        typer.echo(f"  {generation.path.relative_to(project.path)}")
        for artifact in generation.artifacts:
            typer.echo(f"    + {artifact.name}")
