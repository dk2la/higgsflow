"""Soul IDs: character identities trained on the Higgsfield side, owned by the account (GLOSSARY.md).

Training one is not a Request: it is `POST /v1/custom-references` with 1–100 images, polled
via `GET /v1/custom-references/{id}`, and never written to a Project's `.hf/requests/` or
counted in its Spend. The API cannot list Soul IDs, so every one trained here is
registered by name in the global `<config dir>/souls.toml` the moment the API accepts
it (like a Request record, so the id survives a dropped connection), and its status is
updated as training goes on. `load_souls` and `save_soul` are the only code that touches
the file:

```toml
[souls.alice]
id = "3eb1d5a2-7f3c-4c1e-9f0a-2b6d8c4e1a90"
version = "v2"
status = "completed"
images = 12
project = "demo"
created_at = 2026-10-30T12:00:00Z
updated_at = 2026-10-30T12:04:10Z
thumbnail_url = "https://..."
```
"""

from __future__ import annotations

import difflib
import json
import os
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from hfcore import clock, config, inputs
from hfcore.api import ApiClient, CustomReference
from hfcore.errors import HiggsError
from hfcore.lifecycle import POLL_ERROR_ATTEMPTS, Backoff, retry_wait
from hfcore.projects import Project

SOULS_FILE = "souls.toml"
# SOUL V2 is the current SOUL Model; the API's own default is v1.
DEFAULT_VERSION = "v2"
TERMINAL_STATUSES = frozenset({"completed", "failed"})
# `input_images` of `POST /v1/custom-references`.
MIN_IMAGES = 1
MAX_IMAGES = 100

# Called with each status seen while waiting for a training.
SoulProgress = Callable[[CustomReference], None]


class Soul(BaseModel):
    """One registered Soul ID. Unknown fields are kept, like on a Request record."""

    model_config = ConfigDict(extra="allow")

    name: str
    id: str
    # The SOUL family it was trained for: `v1`, `v2` or `cinema`.
    version: str
    status: str
    # How many images it was trained on, and the Project they came from.
    images: int | None = None
    project: str | None = None
    created_at: datetime
    # When higgs last saw its status change.
    updated_at: datetime
    thumbnail_url: str | None = None
    fail_reason: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def souls_path() -> Path:
    return config.config_dir() / SOULS_FILE


def train_soul(
    client: ApiClient,
    project: Project,
    name: str,
    images: list[str],
    *,
    version: str,
    cwd: Path,
) -> Soul:
    """Upload the images, start the training and register the Soul ID; returns at once (see `wait_for_soul`).

    Everything that can be checked locally is checked before the first upload.
    """
    if not MIN_IMAGES <= len(images) <= MAX_IMAGES:
        raise HiggsError(
            f"A Soul ID is trained on {MIN_IMAGES}–{MAX_IMAGES} images, got {len(images)}",
            hint="Pass the training images after the name: higgs soul train <name> inputs/a.jpg inputs/b.jpg ...",
            code="soul_image_count",
        )
    resolved = inputs.resolve_inputs(client, project, {"input_images": list(images)}, cwd=cwd)
    urls: list[str] = resolved.params["input_images"]
    body = {
        "name": name,
        "model_version": version,
        "input_images": [{"type": "image_url", "image_url": url} for url in urls],
    }
    created = client.create_custom_reference(body)
    now = clock.now()
    soul = Soul(
        name=name,
        id=created.id,
        version=created.model_version or version,
        status=created.status,
        images=len(urls),
        project=project.name,
        created_at=created.created_at or now,
        updated_at=now,
        thumbnail_url=created.thumbnail_url,
        fail_reason=created.fail_reason,
    )
    save_soul(soul)
    return soul


def wait_for_soul(client: ApiClient, soul: Soul, *, on_progress: SoulProgress | None = None) -> Soul:
    """Poll until the training is `completed` or `failed`; `souls.toml` follows every change."""
    backoff = Backoff()
    failures = 0
    while True:
        try:
            status = client.custom_reference(soul.id)
        except HiggsError as error:
            failures += 1
            wait = retry_wait(error, backoff)
            if wait is None or failures >= POLL_ERROR_ATTEMPTS:
                error.hint = (
                    f"{error.hint + ' ' if error.hint else ''}Soul ID {soul.name} ({soul.id}) is registered in "
                    f"{souls_path()}; check on its training with `higgs soul status {soul.name}`."
                )
                raise
            clock.sleep(wait)
            continue
        failures = 0
        if on_progress is not None:
            on_progress(status)
        soul = record_status(soul, status)
        if soul.is_terminal:
            return soul
        clock.sleep(backoff.next())


def refresh_soul(client: ApiClient, name: str) -> Soul:
    """Ask the API for a registered Soul ID's training status once; `souls.toml` follows."""
    soul = get_soul(name)
    return record_status(soul, client.custom_reference(soul.id))


def get_soul(name: str) -> Soul:
    """The registered Soul ID with this name; an unknown name lists the registered ones."""
    registered = load_souls()
    if name in registered:
        return registered[name]
    close = difflib.get_close_matches(name, list(registered), n=len(registered), cutoff=0.6)
    others = close + [other for other in registered if other not in close]
    hint = (
        f"Registered: {', '.join(others)}." if others else "None registered yet; train one with `higgs soul train`."
    )
    raise HiggsError(f"No Soul ID named {name} in {souls_path()}", hint=hint, code="soul_not_found")


def record_status(soul: Soul, status: CustomReference) -> Soul:
    """The Soul ID updated with a status response, saved when anything changed."""
    update: dict[str, Any] = {
        "status": status.status,
        "thumbnail_url": status.thumbnail_url or soul.thumbnail_url,
        "fail_reason": status.fail_reason or soul.fail_reason,
    }
    if all(getattr(soul, key) == value for key, value in update.items()):
        return soul
    soul = soul.model_copy(update={**update, "updated_at": clock.now()})
    save_soul(soul)
    return soul


def load_souls() -> dict[str, Soul]:
    """Every registered Soul ID by name; an empty registry when there is no `souls.toml` yet."""
    path = souls_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text())
        table = data.get("souls", {})
        if not isinstance(table, dict):
            raise ValueError("[souls] is not a table")
        return {name: Soul.model_validate({**entry, "name": name}) for name, entry in table.items()}
    except (tomllib.TOMLDecodeError, ValidationError, ValueError, TypeError) as error:
        # A registry, not a cache: never treat it as empty, or the next save would drop every Soul ID.
        raise HiggsError(
            f"Cannot read {path}: {error}",
            hint="Fix the file by hand; it is the only record of the Soul IDs' ids.",
            code="invalid_souls_file",
        ) from None


def save_soul(soul: Soul) -> Path:
    """Add or replace one Soul ID in `souls.toml`, atomically."""
    souls = {**load_souls(), soul.name: soul}
    path = souls_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(_dump(souls))
    os.replace(tmp, path)
    return path


_HEADER = (
    "# Soul IDs registered by `higgs soul train` (account-scoped; the API cannot list them).\n"
    "# Written by higgs; `higgs soul list` shows them, `--soul <name>` uses them.\n"
)


def _dump(souls: dict[str, Soul]) -> str:
    parts = [_HEADER]
    for name, soul in souls.items():
        lines = [f"\n[souls.{_key(name)}]"]
        for key, value in soul.model_dump(exclude={"name"}).items():
            if value is not None:
                lines.append(f"{_key(key)} = {_value(value)}")
        parts.append("\n".join(lines) + "\n")
    return "".join(parts)


def _key(key: str) -> str:
    bare = key and all(char.isascii() and (char.isalnum() or char in "-_") for char in key)
    return key if bare else _string(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value if item is not None) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{_key(str(key))} = {_value(item)}" for key, item in value.items() if item is not None)
        return "{ " + items + " }" if items else "{}"
    return _string(str(value))


def _string(text: str) -> str:
    # A JSON string is a valid TOML basic string, except that TOML also wants DEL escaped.
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")
