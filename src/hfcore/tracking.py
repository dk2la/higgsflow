"""Keeping track of a Project's Requests after the submit: `status`, `list`, `sync`, `download`.

Everything here starts from the Request records in `.hf/requests/` (ADR 0002). Listing
reads only those files; refreshing asks the API for one status per Request (no waiting);
downloading goes through `hfcore.downloads.download_request`, as `gen` does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from pydantic import BaseModel

from hfcore import clock, lifecycle
from hfcore.api import ApiClient, RequestStatus
from hfcore.catalog import Catalog
from hfcore.downloads import DownloadedGeneration, download_request
from hfcore.errors import HiggsError
from hfcore.projects import Project
from hfcore.requests import RequestRecord, list_requests, quiet_catalog, request_model

# `--status pending` stands for every status that is not terminal yet.
PENDING = "pending"


def find_requests(
    project: Project,
    *,
    statuses: Iterable[str] = (),
    model: str | None = None,
    mode: str | None = None,
    since: date | None = None,
    on: date | None = None,
) -> list[RequestRecord]:
    """The Project's Requests matching every given filter, oldest first; reads local files only.

    `statuses` match any of them (`pending` = not terminal yet); `model` is matched by
    `model_matcher`; `mode` is the Mode code; `since`/`on` compare the local date of the
    submit.
    """
    wanted = {status.lower() for status in statuses}
    of_model = model_matcher(model, quiet_catalog()) if model else None
    found = []
    for record in list_requests(project):
        day = record.submitted_at.astimezone().date()
        naming = record.naming
        if wanted and record.status not in wanted and not (PENDING in wanted and not record.is_terminal):
            continue
        if of_model and not of_model(record):
            continue
        if mode and (naming is None or naming.mode != mode.lower()):
            continue
        if (since and day < since) or (on and day != on):
            continue
        found.append(record)
    return found


def model_matcher(model: str, catalog: Catalog | None) -> Callable[[RequestRecord], bool]:
    """Whether a Request belongs to `model`, as the user typed it after `--model`.

    A catalog Model's slug or display name (`kling-3.0`, `Kling 3.0`, as `higgs models list`
    shows them) matches every Request sent to one of its Endpoints, whatever the tier or
    Mode. Any value also matches a Request whose Endpoint id or model slug in its names
    contains it (`kling-video`, `soul`), so a Family-ish word or an Endpoint the catalog
    lacks still works.
    """
    text = model.lower()
    slug: str | None = None
    if catalog is not None:
        try:
            slug = catalog.model(model).slug
        except HiggsError:
            slug = None

    def matches(record: RequestRecord) -> bool:
        if slug is not None and request_model(record, catalog) == slug:
            return True
        return text in record.endpoint.lower() or (record.naming is not None and text in record.naming.model_slug)

    return matches


def refresh_request(client: ApiClient, project: Project, record: RequestRecord) -> tuple[RequestRecord, RequestStatus]:
    """Ask the API for the Request's current status once and record it.

    Transient failures (429, 5xx, network) are retried as while waiting, but a status
    that is not terminal yet is returned as it is.
    """
    backoff = lifecycle.Backoff()
    for attempt in range(1, lifecycle.POLL_ERROR_ATTEMPTS + 1):
        try:
            status = client.status(record.request_id)
        except HiggsError as error:
            wait = lifecycle.retry_wait(error, backoff)
            if wait is None or attempt == lifecycle.POLL_ERROR_ATTEMPTS:
                raise
            clock.sleep(wait)
            continue
        return lifecycle.record_status(project, record, status), status
    raise AssertionError("unreachable")


def pending_requests(project: Project) -> list[RequestRecord]:
    """The Project's Requests that have not reached a terminal status yet, oldest first."""
    return [record for record in list_requests(project) if not record.is_terminal]


def is_expired(record: RequestRecord) -> bool:
    """Whether the API has already deleted the Request's outputs (`expires_at` has passed)."""
    return record.expires_at is not None and record.expires_at <= clock.now()


class Synced(BaseModel):
    """What `sync` did with one Request.

    `outcome`: `downloaded`; `pending` (still queued or in progress); `unsuccessful`
    (ended `failed`, `nsfw` or `canceled`: recorded, nothing to download); `expired`
    (completed, but its links stopped working before it was downloaded); `error` (checking
    or downloading it failed, see `failure`; the next `sync` tries again).
    """

    request_id: str
    status: str
    outcome: str
    files: list[str] = []
    # The API's error message of an unsuccessful Request.
    error: str | None = None
    # What went wrong while syncing it (`HiggsError.to_dict()`).
    failure: dict[str, Any] | None = None


def requests_to_sync(project: Project) -> list[RequestRecord]:
    """The Requests `sync` has work for: not downloaded, not deleted, not known to have failed."""
    return [
        record
        for record in list_requests(project)
        if record.downloaded_at is None
        and record.deleted_at is None
        and (record.status == "completed" or not record.is_terminal)
    ]


def sync_request(client: ApiClient, project: Project, record: RequestRecord) -> Synced:
    """Check a Request once (no waiting); download its Generations if it has completed.

    A completed Request whose links have expired is reported without any API call. A
    failure is reported as the `error` outcome rather than raised, so one Request never
    keeps `sync` from the others.
    """

    def synced(outcome: str, failure: HiggsError | None = None) -> Synced:
        return Synced(
            request_id=record.request_id,
            status=record.status,
            outcome=outcome,
            files=record.files,
            error=record.error,
            failure=failure.to_dict() if failure else None,
        )

    if record.status == "completed" and is_expired(record):
        return synced("expired")
    try:
        record, status = refresh_request(client, project, record)
        if not record.is_terminal:
            return synced("pending")
        if record.status != "completed":
            return synced("unsuccessful")
        if is_expired(record):
            return synced("expired")
        download_request(client, project, record, status)
    except HiggsError as error:
        return synced("error", error)
    return synced("downloaded")


def download_again(client: ApiClient, project: Project, record: RequestRecord) -> list[DownloadedGeneration]:
    """Download a completed Request's Generations (again), with fresh links from its status.

    What the record already rules out (links expired, data deleted on the API side, an
    unsuccessful end) fails before any API call; a Request still running fails after its
    status check.
    """
    _ensure_downloadable(record)
    record, status = refresh_request(client, project, record)
    _ensure_downloadable(record)
    if not record.is_terminal:
        raise HiggsError(
            f"Request {record.request_id} has not completed yet ({record.status})",
            hint="Check it with `higgs status`; `higgs sync` downloads it once it completes.",
            code="request_pending",
        )
    return download_request(client, project, record, status)


def _ensure_downloadable(record: RequestRecord) -> None:
    if record.deleted_at is not None:
        raise HiggsError(
            f"Request {record.request_id} was deleted on the API side; its outputs are gone",
            hint="Files downloaded before the deletion are still in the Project.",
            code="request_deleted",
        )
    if record.is_terminal and record.status != "completed":
        raise lifecycle.RequestNotCompleted(record)
    if is_expired(record):
        assert record.expires_at is not None
        raise HiggsError(
            f"The links of Request {record.request_id} expired at {record.expires_at.isoformat()}",
            hint="The API no longer has its outputs; files downloaded earlier are still in the Project.",
            code="request_expired",
        )
