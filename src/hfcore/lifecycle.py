"""The Generation lifecycle shared by `video`, `image` and `gen` (DESIGN.md §8).

So far: submit → write the Request record → wait for a terminal status. Validation,
uploads and estimate plug in before `submit_request`; downloading a completed Request
is `hfcore.downloads.download_request`.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from typing import Any

from hfcore import clock
from hfcore.api import ApiClient, ApiError, RequestStatus, Submitted
from hfcore.errors import HiggsError
from hfcore.inputs import Input
from hfcore.projects import Project
from hfcore.requests import TERMINAL_STATUSES, RequestRecord, new_naming, save_request

# Polling (docs "Polling"): start at 2 s, grow ×1.5 up to 10 s; the jitter is part of the
# 10 s, so many workers polling together never line up and no wait exceeds the cap.
POLL_FIRST = 2.0
POLL_MAX = 10.0
POLL_FACTOR = 1.5
JITTER = 0.5
# Consecutive failed status checks (429, 5xx, network) before giving up on waiting.
POLL_ERROR_ATTEMPTS = 8
# Attempts at a submit that fails ambiguously or finds the queue full (same Idempotency-Key).
SUBMIT_ATTEMPTS = 5

# Called with each status seen while waiting (`queued`, `in_progress`, ...).
ProgressCallback = Callable[[RequestStatus], None]


def submit_request(
    client: ApiClient,
    project: Project,
    endpoint: str,
    params: dict[str, Any],
    *,
    inputs: list[Input] | None = None,
    name: str | None = None,
) -> RequestRecord:
    """Submit a Request and write its record before doing anything else.

    `params` must already be resolved (`hfcore.inputs.resolve_inputs`); `inputs` are the
    local files behind them, kept on the record. `name` is the slug of its Generations
    (`--name`); without it the slug comes from the prompt. The naming, including `vNN`,
    is fixed here and stored on the record.
    """
    endpoint = endpoint.strip("/")
    prompt = params.get("prompt")
    prompt = prompt if isinstance(prompt, str) else None
    naming = new_naming(project, endpoint, prompt=prompt, name=name)
    idempotency_key = str(uuid.uuid4())
    submitted = _submit_with_retries(client, endpoint, params, idempotency_key)
    now = clock.now()
    record = RequestRecord(
        request_id=submitted.request_id,
        endpoint=endpoint,
        params=params,
        inputs=inputs or [],
        prompt=prompt,
        status=submitted.status,
        submitted_at=now,
        updated_at=now,
        idempotency_key=idempotency_key,
        status_url=submitted.status_url,
        cancel_url=submitted.cancel_url,
        expires_after=submitted.expires_after,
        correlation_id=submitted.correlation_id,
        naming=naming,
    )
    save_request(project, record)
    return record


def _submit_with_retries(
    client: ApiClient, endpoint: str, params: dict[str, Any], idempotency_key: str
) -> Submitted:
    """Submit, retrying ambiguous failures (network, 5xx) and a full queue (429).

    Every attempt carries the same Idempotency-Key, so the API creates (and bills) the
    Request at most once even when an earlier attempt did reach it.
    """
    backoff = Backoff()
    for attempt in range(1, SUBMIT_ATTEMPTS + 1):
        try:
            return client.submit(endpoint, params, idempotency_key=idempotency_key)
        except HiggsError as error:
            wait = retry_wait(error, backoff, submit=True)
            if wait is None or attempt == SUBMIT_ATTEMPTS:
                raise
            clock.sleep(wait)
    raise AssertionError("unreachable")


def wait_for_request(
    client: ApiClient,
    project: Project,
    record: RequestRecord,
    *,
    on_progress: ProgressCallback | None = None,
) -> tuple[RequestRecord, RequestStatus]:
    """Poll until the Request reaches a terminal status; the record follows every change."""
    backoff = Backoff()
    failures = 0
    while True:
        try:
            status = client.status(record.request_id)
        except HiggsError as error:
            failures += 1
            wait = retry_wait(error, backoff)
            if wait is None or failures >= POLL_ERROR_ATTEMPTS:
                error.hint = (
                    f"{error.hint + ' ' if error.hint else ''}Request {record.request_id} is still on the "
                    f"API side; its record is in {project.requests_dir}, so it can be checked again later."
                )
                raise
            clock.sleep(wait)
            continue
        failures = 0
        if on_progress is not None:
            on_progress(status)
        if status.status != record.status or status.status in TERMINAL_STATUSES:
            record = record.model_copy(
                update={
                    "status": status.status,
                    "updated_at": clock.now(),
                    "expires_at": status.expires_at or record.expires_at,
                    "error": status.error or record.error,
                }
            )
            save_request(project, record)
        if record.is_terminal:
            return record, status
        clock.sleep(backoff.next())


def retry_wait(error: HiggsError, backoff: Backoff, *, submit: bool = False) -> float | None:
    """How long to wait before retrying after `error`, or None if retrying cannot help.

    429 → its `Retry-After`; 5xx and network failures → the next backoff. On submit a
    503 that means the model is disabled is final, not transient.
    """
    if isinstance(error, ApiError):
        if error.status == 429:
            return error.retry_after if error.retry_after is not None else backoff.next()
        if error.status >= 500 and not (submit and error.code == "model_unavailable"):
            return backoff.next()
        return None
    return backoff.next() if error.code == "network_error" else None


class Backoff:
    """Waits of 2 s growing ×1.5 up to 10 s, each with up to 0.5 s of random jitter."""

    def __init__(self) -> None:
        self._delay = POLL_FIRST

    def next(self) -> float:
        wait = self._delay + random.uniform(0, JITTER)
        self._delay = min(self._delay * POLL_FACTOR, POLL_MAX - JITTER)
        return wait


class RequestNotCompleted(HiggsError):
    """A Request that ended `failed`, `nsfw` or `canceled`: nothing to download, nothing billed."""

    def __init__(self, record: RequestRecord):
        reasons = {
            "failed": "failed",
            "nsfw": "was rejected by content moderation",
            "canceled": "was canceled",
        }
        reason = reasons.get(record.status, f"ended with status {record.status}")
        detail = f": {record.error}" if record.error else ""
        super().__init__(
            f"Request {record.request_id} {reason}{detail}",
            hint="Nothing was downloaded; failed, nsfw and canceled Requests are not billed.",
            code=f"request_{record.status}",
        )
        self.request_id = record.request_id
        self.status = record.status

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "request_id": self.request_id, "status": self.status}
