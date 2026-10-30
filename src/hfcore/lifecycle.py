"""The Generation lifecycle shared by `video`, `image` and `gen` (DESIGN.md §8).

So far: submit → write the Request record. Validation, uploads, estimate, polling
and download plug in around `submit_request`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from hfcore.api import ApiClient
from hfcore.projects import Project
from hfcore.requests import RequestRecord, save_request


def submit_request(client: ApiClient, project: Project, endpoint: str, params: dict[str, Any]) -> RequestRecord:
    """Submit a Request and write its record before doing anything else."""
    endpoint = endpoint.strip("/")
    idempotency_key = str(uuid.uuid4())
    submitted = client.submit(endpoint, params, idempotency_key=idempotency_key)
    now = datetime.now(UTC)
    prompt = params.get("prompt")
    record = RequestRecord(
        request_id=submitted.request_id,
        endpoint=endpoint,
        params=params,
        prompt=prompt if isinstance(prompt, str) else None,
        status=submitted.status,
        submitted_at=now,
        updated_at=now,
        idempotency_key=idempotency_key,
        status_url=submitted.status_url,
        cancel_url=submitted.cancel_url,
        expires_after=submitted.expires_after,
        correlation_id=submitted.correlation_id,
    )
    save_request(project, record)
    return record
