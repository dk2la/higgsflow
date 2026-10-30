"""Downloading a completed Request's Generations and Artifacts (DESIGN.md §5, §8).

`download_request` is shared by `gen` (after waiting), `sync` and `download`: given a
completed Request record and its status response, it saves every Generation under
`generations/YYYY-MM-DD/` with a `<file>.json` metadata file next to it, saves every
Artifact named after its Generation, and records the files on the Request record.
"""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

from hfcore import clock, naming
from hfcore.api import ApiClient, RequestStatus
from hfcore.inputs import Input
from hfcore.projects import Project
from hfcore.requests import RequestRecord, new_naming, save_request

# Output fields that hold Generations, and the extension used when a URL has none.
GENERATION_KEYS = {"video": "mp4", "images": "png", "audio": "mp3", "audios": "mp3"}
# Output fields that hold Artifacts; the key doubles as the default extension.
ARTIFACT_KEYS = ("zip", "mov", "jsx", "fbx", "ply")


class Output(BaseModel):
    """One file in a status response: the field it came from and its URL."""

    key: str
    url: str
    content_type: str | None = None


class ArtifactEntry(BaseModel):
    key: str
    file: str
    url: str


class GenerationMetadata(BaseModel):
    """The `<file>.json` written next to each Generation: how it was made."""

    file: str
    request_id: str
    endpoint: str
    model_slug: str
    mode: str
    slug: str
    version: int
    params: dict[str, Any]
    prompt: str | None
    # Inputs/References uploaded for the Request, as recorded on it (if any).
    inputs: list[Input]
    # Exact price from `/estimate`, if it was asked for.
    price_usd: str | None
    output_key: str
    url: str
    status_url: str | None
    submitted_at: datetime
    completed_at: datetime
    downloaded_at: datetime
    expires_at: datetime | None
    artifacts: list[ArtifactEntry]


class DownloadedGeneration(BaseModel):
    path: Path
    metadata_path: Path
    url: str
    artifacts: list[Path]


def download_request(
    client: ApiClient, project: Project, record: RequestRecord, status: RequestStatus
) -> list[DownloadedGeneration]:
    """Download every Generation and Artifact of a completed Request; update its record."""
    if record.naming is None:  # a record written before names were fixed at submit
        record.naming = new_naming(project, record.endpoint, prompt=record.prompt, name=None)
    generations, artifacts = _outputs(status)
    folder = project.path / "generations" / naming.day_folder(record.submitted_at)
    base = naming.base_name(record.naming, record.submitted_at)

    names = [
        f"{base}{f'-{index}' if len(generations) > 1 else ''}.{_extension(output)}"
        for index, output in enumerate(generations, start=1)
    ]
    # Artifacts are named after the Generation (the Request's base name when there are
    # several); on an extension clash the output field key is appended.
    taken = set(names)
    artifact_names = []
    for artifact in artifacts:
        extension = _extension(artifact)
        candidate = f"{base}.{extension}"
        if candidate in taken:
            candidate = f"{base}-{artifact.key}.{extension}"
        taken.add(candidate)
        artifact_names.append(candidate)

    for output, name in zip([*generations, *artifacts], [*names, *artifact_names], strict=True):
        client.download(output.url, folder / name)

    now = clock.now()
    entries = [
        ArtifactEntry(key=artifact.key, file=name, url=artifact.url)
        for artifact, name in zip(artifacts, artifact_names, strict=True)
    ]
    downloaded = []
    for output, name in zip(generations, names, strict=True):
        metadata = GenerationMetadata(
            file=name,
            request_id=record.request_id,
            endpoint=record.endpoint,
            **record.naming.model_dump(),
            params=record.params,
            prompt=record.prompt,
            inputs=record.inputs,
            price_usd=record.estimate_usd,
            output_key=output.key,
            url=output.url,
            status_url=record.status_url,
            submitted_at=record.submitted_at,
            completed_at=record.updated_at,
            downloaded_at=now,
            expires_at=record.expires_at,
            artifacts=entries,
        )
        metadata_path = folder / f"{name}.json"
        metadata_path.write_text(metadata.model_dump_json(indent=2) + "\n")
        downloaded.append(
            DownloadedGeneration(
                path=folder / name,
                metadata_path=metadata_path,
                url=output.url,
                artifacts=[folder / entry.file for entry in entries],
            )
        )

    record.downloaded_at = now
    record.files = [str((folder / name).relative_to(project.path)) for name in [*names, *artifact_names]]
    save_request(project, record)
    return downloaded


def _outputs(status: RequestStatus) -> tuple[list[Output], list[Output]]:
    """The Generations and Artifacts of a status response, each URL once.

    `audio` and `audios` may repeat the same file (as in the docs' example). A response
    with Artifacts only (e.g. a 3D operation) has them as its Generations.
    """
    fields = status.outputs()
    seen: set[str] = set()

    def collect(keys: tuple[str, ...]) -> list[Output]:
        found = []
        for key in keys:
            for item in _items(fields.get(key)):
                if item["url"] not in seen:
                    seen.add(item["url"])
                    content_type = item.get("content_type")
                    found.append(
                        Output(key=key, url=item["url"], content_type=content_type if isinstance(content_type, str) else None)
                    )
        return found

    generations = collect(tuple(GENERATION_KEYS))
    artifacts = collect(ARTIFACT_KEYS)
    if not generations:
        return artifacts, []
    return generations, artifacts


def _items(value: Any) -> list[dict[str, Any]]:
    """`{"url": ...}`, a list of them, or a bare URL string → a list of `{"url": ...}`."""
    values = value if isinstance(value, list) else [value]
    items = []
    for item in values:
        if isinstance(item, str):
            item = {"url": item}
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]:
            items.append(item)
    return items


def _extension(output: Output) -> str:
    """From the URL path, else the content type, else a default for the output field."""
    suffix = PurePosixPath(urlparse(output.url).path).suffix.lstrip(".").lower()
    if not suffix and output.content_type:
        guessed = mimetypes.guess_extension(output.content_type.split(";")[0].strip())
        suffix = (guessed or "").lstrip(".")
    if re.fullmatch(r"[a-z0-9]{1,8}", suffix):
        return suffix
    return GENERATION_KEYS.get(output.key, output.key)
