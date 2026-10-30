"""Request records: `.hf/requests/<request_id>.json` (ADR 0002).

The record is the source of truth for a Request: it is written the moment the API
accepts the submit, and later updated as the status changes. `save_request` and
`load_request` are the only code that touches these files.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from hfcore.errors import HiggsError
from hfcore.projects import Project

TERMINAL_STATUSES = frozenset({"completed", "failed", "nsfw", "canceled"})


class RequestRecord(BaseModel):
    """One Request as stored in the Project.

    Unknown fields are kept, so a record written by a newer version survives a
    load/save round-trip through an older one.
    """

    model_config = ConfigDict(extra="allow")

    request_id: str
    endpoint: str
    params: dict[str, Any]
    prompt: str | None = None
    status: str
    submitted_at: datetime
    updated_at: datetime
    idempotency_key: str
    status_url: str | None = None
    cancel_url: str | None = None
    # Seconds the API keeps the data after the Request finishes (from the submit response).
    expires_after: int | None = None
    # When the API deletes the outputs (from a finished status response).
    expires_at: datetime | None = None
    # Exact price from `POST /estimate/{endpoint}`, as the decimal string the API returns.
    estimate_usd: str | None = None
    error: str | None = None
    # When `higgs delete` removed the Request's data on the API side (local files stay).
    deleted_at: datetime | None = None
    # `X-Correlation-ID` of the submit response, for support.
    correlation_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def request_path(project: Project, request_id: str) -> Path:
    return project.requests_dir / f"{request_id}.json"


def save_request(project: Project, record: RequestRecord) -> Path:
    """Write the record atomically (a crash never leaves a half-written file)."""
    path = request_path(project, record.request_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(record.model_dump_json(indent=2) + "\n")
    os.replace(tmp, path)
    return path


def load_request(project: Project, request_id: str) -> RequestRecord:
    path = request_path(project, request_id)
    if not path.is_file():
        raise HiggsError(
            f"No Request {request_id} in Project {project.name}",
            code="request_not_found",
        )
    try:
        return RequestRecord.model_validate_json(path.read_text())
    except ValidationError as error:
        raise HiggsError(f"Cannot read {path}: {error}", code="invalid_request_record") from None
