"""References: named files in a Project's `references/`, addressed as `@name` (DESIGN.md §5).

Any file in `references/` is a Reference, whether put there by hand or by `save_reference`
(`hfcore.generations.save_reference`, which copies a Generation together with its
`<file>.json` and its Artifacts). The name is
the file name without its extension, and it is unique regardless of extension. The `.json`
metadata files, hidden files and the Artifacts a Reference's `.json` lists are not References.
"""

from __future__ import annotations

import difflib
import glob
import json
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hfcore.errors import HiggsError
from hfcore.projects import Project

METADATA_SUFFIX = ".json"
# A name `@name` can address: no whitespace or slashes, not starting with a dot or `@`.
NAME_PATTERN = re.compile(r"(?![.@])[^\s/\\]+")


class Reference(BaseModel):
    name: str
    path: Path
    # Guessed from the extension.
    content_type: str | None
    size: int
    # The Generation it was saved from and its Endpoint, from its `.json` (None when put by hand).
    generation: str | None
    endpoint: str | None


class SavedReference(BaseModel):
    name: str
    path: Path
    # The copied `<file>.json`.
    metadata_path: Path
    # The copied Artifacts, renamed after the Reference.
    artifacts: list[Path]
    # Reference files replaced by `--force` (their `.json` went with them).
    replaced: list[Path]


def references_dir(project: Project) -> Path:
    return project.path / "references"


def reference_files(project: Project) -> list[Path]:
    """Every Reference file in the Project, by file name."""
    folder = references_dir(project)
    if not folder.is_dir():
        return []
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() != METADATA_SUFFIX
    ]
    # An Artifact saved with a Reference travels with it and is not a Reference of its own.
    artifacts = {artifact for path in files for artifact in artifacts_of(path)}
    return sorted(path for path in files if path not in artifacts)


def list_references(project: Project) -> list[Reference]:
    """Every Reference in the Project, by file name, with what its `.json` says about it."""
    found = []
    for path in reference_files(project):
        metadata = _read_metadata(path)
        generation, endpoint = metadata.get("file"), metadata.get("endpoint")
        found.append(
            Reference(
                name=path.stem,
                path=path,
                content_type=mimetypes.guess_type(path.name)[0],
                size=path.stat().st_size,
                generation=generation if isinstance(generation, str) else None,
                endpoint=endpoint if isinstance(endpoint, str) else None,
            )
        )
    return found


def _read_metadata(path: Path) -> dict[str, Any]:
    """The Reference's `.json` (a copied Generation metadata), or {} if none or unreadable."""
    try:
        data = json.loads(metadata_path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def matching_files(project: Project, name: str) -> list[Path]:
    """The Reference files named `name`, whatever their extension (more than one is ambiguous)."""
    return [path for path in reference_files(project) if path.stem == name]


def reference_name(value: str) -> str | None:
    """The Reference a param value addresses (`@hero` → `hero`), or None for any other string."""
    if value.startswith("@") and NAME_PATTERN.fullmatch(value[1:]):
        return value[1:]
    return None


def unescape(value: str) -> str | None:
    """A value starting with `@@` is a literal string starting with `@` (`@@hero` → `@hero`);
    None for any other value."""
    return value[1:] if value.startswith("@@") else None


def resolve_reference(project: Project, name: str) -> Path:
    """The one file of Reference `name`; ambiguous and unknown names fail, listing what exists."""
    found = matching_files(project, name)
    if len(found) == 1:
        return found[0]
    if found:
        listed = ", ".join(display(path, project) for path in found)
        raise HiggsError(
            f"Reference @{name} is ambiguous: {listed}",
            hint="A Reference name must be unique regardless of extension; rename or remove all but one.",
            code="ambiguous_reference",
        )
    names = sorted({path.stem for path in reference_files(project)})
    close = difflib.get_close_matches(name, names, n=max(len(names), 1), cutoff=0.6)
    ordered = [*close, *(other for other in names if other not in close)]
    hint = (
        f"References in this Project: {', '.join(f'@{other}' for other in ordered)}."
        if ordered
        else "This Project has no References yet."
    )
    raise HiggsError(
        f"No Reference @{name} in {display(references_dir(project), project)}/",
        hint=f"{hint} Save one with `higgs ref save <generation> {name}` or put a file in references/; "
        "start a value with @@ to send a literal @.",
        code="reference_not_found",
    )


def artifacts_of(reference: Path) -> list[Path]:
    """The Artifact files a Reference's `.json` lists (none for a Reference put there by hand)."""
    entries = _read_metadata(reference).get("artifacts")
    if not isinstance(entries, list):
        return []
    names = [entry.get("file") for entry in entries if isinstance(entry, dict)]
    return [reference.with_name(file) for file in names if isinstance(file, str) and "/" not in file]


def metadata_path(path: Path) -> Path:
    return path.with_name(path.name + METADATA_SUFFIX)


def staging_path(path: Path) -> Path:
    # Hidden, so it is never taken for a Reference.
    return path.with_name(f".{path.name}.tmp")


def display(path: Path, project: Project) -> str:
    return str(path.relative_to(project.path))
