"""References: named files in a Project's `references/`, addressed as `@name` (DESIGN.md §5).

Any file in `references/` is a Reference, whether put there by hand or by `save_reference`
(which copies a Generation together with its `<file>.json`). The name is the file name
without its extension, and it is unique regardless of extension. The `.json` metadata
files and hidden files are not References.
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
    # The copied `<file>.json`, if the Generation had one.
    metadata_path: Path | None
    # Reference files replaced by `--force` (their `.json` went with them).
    replaced: list[Path]


def references_dir(project: Project) -> Path:
    return project.path / "references"


def reference_files(project: Project) -> list[Path]:
    """Every Reference file in the Project, by file name."""
    folder = references_dir(project)
    if not folder.is_dir():
        return []
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() != METADATA_SUFFIX
    )


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
        data = json.loads(_metadata_path(path).read_text())
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
        listed = ", ".join(_display(path, project) for path in found)
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
        f"No Reference @{name} in {_display(references_dir(project), project)}/",
        hint=f"{hint} Save one with `higgs ref save <generation> {name}` or put a file in references/; "
        "start a value with @@ to send a literal @.",
        code="reference_not_found",
    )


def find_generation(project: Project, value: str, *, cwd: Path) -> Path:
    """The Generation file `value` names: a path relative to `cwd`, or absolute, or a bare
    file name found in the Project's `generations/<day>/`."""
    path = cwd / Path(value).expanduser()
    if path.is_file():
        return path
    found = []
    if "/" not in value and "\\" not in value:
        found = [path for path in (project.path / "generations").glob(f"*/{glob.escape(value)}") if path.is_file()]
    if len(found) == 1:
        return found[0]
    if found:  # the same name on two days (only by hand: names start with the date)
        listed = ", ".join(_display(path, project) for path in sorted(found))
        raise HiggsError(
            f"Generation {value} is ambiguous: {listed}",
            hint="Pass the path of the one you mean.",
            code="ambiguous_generation",
        )
    raise HiggsError(
        f"Generation {value} not found",
        hint="Pass the path of a Generation file (e.g. generations/YYYY-MM-DD/<file>.png) or its file name.",
        code="generation_not_found",
    )


def save_reference(project: Project, generation: Path, name: str, *, force: bool = False) -> SavedReference:
    """Copy a Generation file (and its `.json`) to `references/<name>.<ext>`.

    A taken name (with any extension) fails unless `force`, which replaces the old
    Reference and its `.json`. The copies are written first and moved into place last,
    so a failed copy leaves the old Reference as it was.
    """
    if not NAME_PATTERN.fullmatch(name):
        raise HiggsError(
            f"Invalid Reference name {name!r}",
            hint="Use a name without spaces or slashes that does not start with a dot or @, e.g. hero.",
            code="invalid_reference_name",
        )
    if generation.suffix.lower() == METADATA_SUFFIX:
        raise HiggsError(
            f"{generation.name} is metadata, not a Generation",
            hint=f"Pass the Generation file itself: {generation.with_suffix('').name}.",
            code="not_a_generation",
        )
    taken = matching_files(project, name)
    if taken and not force:
        listed = ", ".join(_display(path, project) for path in taken)
        raise HiggsError(
            f"Reference @{name} already exists: {listed}",
            hint="Pick another name, or pass --force to replace it.",
            code="reference_exists",
        )
    target = references_dir(project) / f"{name}{generation.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = _metadata_path(generation)
    copies = [(generation, target)]
    if metadata.is_file():
        copies.append((metadata, _metadata_path(target)))
    staged = [(_staging_path(destination), destination) for _, destination in copies]
    for (source, _), (tmp, _) in zip(copies, staged, strict=True):
        shutil.copyfile(source, tmp)
    for old in taken:
        old.unlink()
        _metadata_path(old).unlink(missing_ok=True)
    for tmp, destination in staged:
        os.replace(tmp, destination)
    return SavedReference(
        name=name,
        path=target,
        metadata_path=_metadata_path(target) if len(copies) > 1 else None,
        replaced=taken,
    )


def _metadata_path(path: Path) -> Path:
    return path.with_name(path.name + METADATA_SUFFIX)


def _staging_path(path: Path) -> Path:
    # Hidden, so it is never taken for a Reference.
    return path.with_name(f".{path.name}.tmp")


def _display(path: Path, project: Project) -> str:
    return str(path.relative_to(project.path))
