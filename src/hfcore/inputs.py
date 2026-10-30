"""Inputs: local files passed in Request parameters, uploaded before the submit (DESIGN.md §8 step 2).

`resolve_inputs` is the one entry point: it finds every file in a params dict, checks all
of them locally (inside the projects root, an allowed type) before anything is uploaded,
uploads what the cache does not already hold and returns the params with each file
replaced by its `public_url`.

Which values are files: a string value is an Input when it names an existing file
(relative to `cwd`, or absolute), or when it looks like a path to a media file (ends in
an allowed extension such as `.jpg`), in which case a missing file is an error rather
than a string silently sent to the API. URLs (`scheme://...`) are never Inputs.

A value `@name` (no spaces) is a Reference: the one file `references/<name>.<ext>`
(`hfcore.references`), uploaded and recorded like any Input with `source="@name"`.
A value starting with `@@` is sent with one `@` removed and is never a file (`@@hero` →
`@hero`); other strings starting with `@` but containing whitespace are plain strings.
Convenience flags (`--image @hero`) pass their values through here and get the same rules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hfcore import config, references, uploads
from hfcore.api import ApiClient
from hfcore.errors import HiggsError
from hfcore.projects import Project

_CHUNK = 1 << 20

# Extensions that make a string look like a path even when no such file exists.
MEDIA_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".wav", ".mp4"})
ALLOWED_TYPES = "jpeg, png, webp, gif, wav or mp4"  # DESIGN.md §9
_NOT_MP4_BRANDS = frozenset(
    {b"qt  ", b"M4A ", b"M4B ", b"M4P "}
    | {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
)


class Input(BaseModel):
    """One local file used by a Request, as recorded on the Request record."""

    # Where the value sat in the params: `image_url`, `images[1]`.
    param: str
    # The value as the user gave it (`inputs/a.jpg`).
    source: str
    # Relative to the Project when the file is inside it, else absolute.
    path: str
    sha256: str
    content_type: str
    public_url: str


class ResolvedParams(BaseModel):
    """The params as they will be sent, and the Inputs that went into them."""

    params: dict[str, Any]
    inputs: list[Input]


@dataclass(frozen=True)
class _Found:
    """A checked local file, not uploaded yet."""

    param: str
    source: str
    path: Path
    sha256: str
    content_type: str


def resolve_inputs(client: ApiClient, project: Project, params: dict[str, Any], *, cwd: Path) -> ResolvedParams:
    """Upload every local file in `params` and replace it by its `public_url`.

    Every file is checked before the first upload, so one bad file uploads nothing.
    A value starting with `@@` is sent with one `@` removed and is never a file.
    """
    # The string at each param (as `_strings` walks) that replaces the given value.
    replacements: dict[str, str] = {}
    found: list[_Found] = []
    for key, top in params.items():
        for param, value in _strings(top, key):
            if (literal := references.unescape(value)) is not None:
                replacements[param] = literal
            elif (path := _locate(value, cwd, project)) is not None:
                found.append(_check(param, value, path, project))

    inputs: list[Input] = []
    for item in found:
        uploaded = uploads.upload(client, project, item.path, sha256=item.sha256, content_type=item.content_type)
        replacements[item.param] = uploaded.public_url
        inputs.append(
            Input(
                param=item.param,
                source=item.source,
                path=_display_path(item.path, project),
                sha256=item.sha256,
                content_type=item.content_type,
                public_url=uploaded.public_url,
            )
        )
    resolved = {key: _replace(value, key, replacements) for key, value in params.items()}
    return ResolvedParams(params=resolved, inputs=inputs)


def _strings(value: Any, param: str) -> Iterator[tuple[str, str]]:
    """Every string in a param value with where it sits: `image`, `images[1]`, `frames.start`."""
    if isinstance(value, str):
        yield param, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{param}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{param}.{key}")


def _replace(value: Any, param: str, replacements: dict[str, str]) -> Any:
    """`value` with the string at each param in `replacements` replaced (as `_strings` walks)."""
    if isinstance(value, str):
        return replacements.get(param, value)
    if isinstance(value, list):
        return [_replace(item, f"{param}[{index}]", replacements) for index, item in enumerate(value)]
    if isinstance(value, dict):
        return {key: _replace(item, f"{param}.{key}", replacements) for key, item in value.items()}
    return value


def _locate(value: str, cwd: Path, project: Project) -> Path | None:
    """The local file a param value names, or None when it is a plain string."""
    if (name := references.reference_name(value)) is not None:
        return references.resolve_reference(project, name)
    if "://" in value or "\n" in value:
        return None
    try:
        path = cwd / Path(value).expanduser()  # an absolute value stays absolute
        if path.is_file() or path.suffix.lower() in MEDIA_EXTENSIONS:
            return path
    except (OSError, ValueError):  # e.g. a long prompt is "File name too long"
        pass
    return None


def _check(param: str, source: str, path: Path, project: Project) -> _Found:
    """Everything that can be checked locally, before any upload."""
    root = config.projects_root().resolve()
    real = path.resolve()
    if not real.is_relative_to(root):
        raise HiggsError(
            f"Input {source} is outside the projects root {root}",
            hint=(
                f"Only the projects root is visible to higgs. Copy the file into the Project's inputs/ "
                f"(e.g. cp {source} {project.path / 'inputs'}/) and pass that path."
            ),
            code="input_outside_projects_root",
        )
    if not real.is_file():
        raise HiggsError(
            f"Input {source} not found ({real})",
            hint="Check the path; relative paths are relative to the current directory.",
            code="input_not_found",
        )
    content_type = sniff_content_type(_head(real))
    if content_type is None:
        raise HiggsError(
            f"Input {source} is not a supported file type",
            hint=f"Inputs must be {ALLOWED_TYPES}; convert the file first.",
            code="unsupported_input_type",
        )
    return _Found(param=param, source=source, path=real, sha256=_sha256(real), content_type=content_type)


def sniff_content_type(head: bytes) -> str | None:
    """The upload content type of a file from its first bytes, or None if it is not allowed.

    The bytes decide, not the extension: the presigned upload must carry the real type,
    and a renamed file (`photo.heic` → `photo.jpg`) is caught here instead of by the model.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    # ISO base media files all start with an `ftyp` box; its major brand tells an MP4
    # from a QuickTime movie, an audio-only M4A or a HEIF/AVIF image.
    if head[4:8] == b"ftyp" and head[8:12] not in _NOT_MP4_BRANDS:
        return "video/mp4"
    return None


def _head(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(16)


def _display_path(path: Path, project: Project) -> str:
    root = project.path.resolve()
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
