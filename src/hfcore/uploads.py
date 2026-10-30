"""The upload cache `.hf/uploads.json` (DESIGN.md §5): file sha256 → `public_url` + expiry.

The API deletes uploaded files after 7 days, so an entry is reused only while it has
more than `REUSE_MARGIN` of life left (the Request may wait in a queue before the model
fetches its Inputs); otherwise the file is uploaded again. `upload` is the only code
that touches the file. It is only a cache: an unreadable one counts as empty.

```json
{
  "<sha256>": {
    "public_url": "https://...",
    "content_type": "image/jpeg",
    "uploaded_at": "2026-10-30T12:00:00Z",
    "expires_at": "2026-11-06T12:00:00Z"
  }
}
```
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, RootModel, ValidationError

from hfcore import clock
from hfcore.api import ApiClient
from hfcore.projects import Project

UPLOAD_LIFETIME = timedelta(days=7)
REUSE_MARGIN = timedelta(hours=1)


class CachedUpload(BaseModel):
    public_url: str
    content_type: str
    uploaded_at: datetime
    expires_at: datetime


class _Cache(RootModel[dict[str, CachedUpload]]):
    pass


def upload(client: ApiClient, project: Project, path: Path, *, sha256: str, content_type: str) -> CachedUpload:
    """The upload of this content: a fresh cached one, else a new one (then cached)."""
    cache = _load(project)
    now = clock.now()
    cached = cache.get(sha256)
    if cached is not None and cached.expires_at - REUSE_MARGIN > now:
        return cached
    target = client.create_upload(content_type)
    client.put_file(target, path, content_type=content_type)
    entry = CachedUpload(
        public_url=target.public_url,
        content_type=content_type,
        uploaded_at=now,
        expires_at=now + UPLOAD_LIFETIME,
    )
    # Expired entries are of no use to anyone; drop them while writing.
    fresh = {key: value for key, value in cache.items() if value.expires_at > now}
    _save(project, {**fresh, sha256: entry})
    return entry


def _path(project: Project) -> Path:
    return project.path / ".hf" / "uploads.json"


def _load(project: Project) -> dict[str, CachedUpload]:
    path = _path(project)
    if not path.is_file():
        return {}
    try:
        return _Cache.model_validate_json(path.read_text()).root
    except (ValidationError, ValueError):
        return {}


def _save(project: Project, entries: dict[str, CachedUpload]) -> None:
    """Write atomically (a crash never leaves a half-written file)."""
    path = _path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(_Cache(entries).model_dump_json(indent=2) + "\n")
    os.replace(tmp, path)
