"""Generations on disk: copying one with its metadata and Artifacts, and Picks (DESIGN.md §5).

A Generation is a downloaded file with a `<file>.json` (`GenerationMetadata`) next to it,
which lists its Artifacts. `copy_generation` copies all of them to another folder as real,
independent copies (never symlinks or hard links), so the copy stays self-contained; `pick`
uses it for `final/`, and `higgs ref save` can use it for `references/`.
"""

from __future__ import annotations

import glob
import shutil
from pathlib import Path

from pydantic import BaseModel, ValidationError

from hfcore.downloads import GenerationMetadata
from hfcore.errors import HiggsError
from hfcore.projects import Project


class CopiedGeneration(BaseModel):
    """Where a Generation was copied from, and the copies of it, its `.json` and its Artifacts."""

    source: Path
    generation: Path
    metadata: Path
    artifacts: list[Path]


class Pick(CopiedGeneration):
    # The Generation was already in `final/` before this pick (its files were copied again).
    already_picked: bool


def metadata_path(generation: Path) -> Path:
    """The `<file>.json` next to a Generation."""
    return generation.with_name(f"{generation.name}.json")


def copy_generation(generation: Path, folder: Path) -> CopiedGeneration:
    """Copy a Generation, its `.json` and every Artifact it lists into `folder`, under the same names.

    Existing files of the same name in `folder` are overwritten. A file without its
    `<file>.json` is not a Generation (it may be an Artifact, or a file put there by hand)
    and is not copied.
    """
    source_metadata = metadata_path(generation)
    if not source_metadata.is_file():
        raise HiggsError(
            f"{generation.name} has no metadata ({source_metadata.name} is missing), so it is not a Generation",
            hint="Pass the Generation itself (an Artifact is copied along with it), or copy a hand-made file yourself.",
            code="generation_metadata_missing",
        )
    try:
        metadata = GenerationMetadata.model_validate_json(source_metadata.read_text())
    except ValidationError as error:
        raise HiggsError(f"Cannot read {source_metadata}: {error}", code="invalid_generation_metadata") from None
    artifacts = [generation.with_name(entry.file) for entry in metadata.artifacts]
    # Check everything first, so a failure never leaves a partial copy behind.
    missing = [path.name for path in artifacts if not path.is_file()]
    if missing:
        raise HiggsError(
            f"{generation.name} lists Artifacts that are missing: {', '.join(missing)}",
            hint="Download the Request again (higgs download <request-id>) to restore them.",
            code="artifact_missing",
        )
    folder.mkdir(parents=True, exist_ok=True)
    if folder.resolve() == generation.parent.resolve():
        raise HiggsError(f"{generation.name} is already in {folder}", code="same_folder")
    copies = [_copy(path, folder) for path in (generation, source_metadata, *artifacts)]
    return CopiedGeneration(source=generation, generation=copies[0], metadata=copies[1], artifacts=copies[2:])


def find_generation(project: Project, given: Path) -> Path:
    """The Generation file `given` names.

    Either a path (relative to the current directory, or absolute), or a bare file name
    looked up in the Project's `generations/YYYY-MM-DD/` folders. It must be in the
    Project's `generations/` (a file in `final/` is already a Pick).
    """
    if given.is_file():
        generation = given.absolute()
        if not generation.resolve().is_relative_to((project.path / "generations").resolve()):
            raise HiggsError(
                f"{given} is not in the generations/ of Project {project.name}",
                hint="Pick a Generation from generations/YYYY-MM-DD/.",
                code="not_a_generation",
            )
        return generation
    if given.name == str(given):
        found = sorted((project.path / "generations").glob(f"*/{glob.escape(given.name)}"))
        if found:
            return found[0]
    raise HiggsError(
        f"No Generation {given} in Project {project.name}",
        hint="Pass a Generation file under generations/, e.g. generations/YYYY-MM-DD/<file>.",
        code="generation_not_found",
    )


def pick(project: Project, given: Path) -> Pick:
    """Copy a Generation of the Project into its `final/`."""
    generation = find_generation(project, given)
    final = project.path / "final"
    already_picked = (final / generation.name).exists()
    copied = copy_generation(generation, final)
    return Pick(**copied.model_dump(), already_picked=already_picked)


def _copy(path: Path, folder: Path) -> Path:
    """A real copy of the file's content (a symlinked source is followed, not copied as a link)."""
    target = folder / path.name
    # Replace rather than write through: an existing target may be a link to the source.
    target.unlink(missing_ok=True)
    shutil.copyfile(path, target)
    shutil.copystat(path, target)
    return target
