"""Generations on disk: copying one with its metadata and Artifacts, and Picks (DESIGN.md §5).

A Generation is a downloaded file with a `<file>.json` (`GenerationMetadata`) next to it,
which lists its Artifacts. `find_generation` is the one lookup of a Generation a command
names, and `check_generation` the one set of checks (metadata readable, every Artifact
present) run before anything is copied. `copy_generation` copies all of them to another
folder as real, independent copies (never symlinks or hard links), so the copy stays
self-contained; `pick` uses it for `final/`. `higgs ref save` renames the copies, so it
copies them itself (`save_reference`) after the same lookup and checks.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from hfcore import config, references
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


@dataclass
class CheckedGeneration:
    """A Generation whose metadata was read and whose Artifacts are all present."""

    path: Path
    metadata_path: Path
    metadata: GenerationMetadata
    # The Artifact files, in the order the metadata lists them.
    artifacts: list[Path]


def metadata_path(generation: Path) -> Path:
    """The `<file>.json` next to a Generation."""
    return generation.with_name(f"{generation.name}.json")


def check_generation(generation: Path) -> CheckedGeneration:
    """Read a Generation's metadata and check that every Artifact it lists is there.

    A file without its `<file>.json` is not a Generation (it may be an Artifact, or a file
    put there by hand). Callers run this before copying anything, so a failure never leaves
    a partial copy behind.
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
    missing = [path.name for path in artifacts if not path.is_file()]
    if missing:
        raise HiggsError(
            f"{generation.name} lists Artifacts that are missing: {', '.join(missing)}",
            hint="Download the Request again (higgs download <request-id>) to restore them.",
            code="artifact_missing",
        )
    return CheckedGeneration(path=generation, metadata_path=source_metadata, metadata=metadata, artifacts=artifacts)


def copy_generation(generation: Path, folder: Path) -> CopiedGeneration:
    """Copy a Generation, its `.json` and every Artifact it lists into `folder`, under the same names.

    Existing files of the same name in `folder` are overwritten.
    """
    checked = check_generation(generation)
    folder.mkdir(parents=True, exist_ok=True)
    if folder.resolve() == generation.parent.resolve():
        raise HiggsError(f"{generation.name} is already in {folder}", code="same_folder")
    copies = [_copy(path, folder) for path in (generation, checked.metadata_path, *checked.artifacts)]
    return CopiedGeneration(source=generation, generation=copies[0], metadata=copies[1], artifacts=copies[2:])


def find_generation(project: Project, value: str, *, cwd: Path) -> Path:
    """The Generation file `value` names.

    Either a path (relative to `cwd`, `hfcore.config.working_dir()`, or absolute), or a
    bare file name looked up in the Project's `generations/YYYY-MM-DD/` folders. It must be
    in the Project's `generations/` (a file in `final/` is already a Pick, one in `inputs/`
    or `references/` is not a Generation) and must not be a Generation's `.json`; a path
    outside the projects root is `input_outside_projects_root`.
    """
    path = cwd / Path(value).expanduser()
    root = config.projects_root().resolve()
    outside = not path.resolve().is_relative_to(root)
    if path.is_file() and not outside:
        generation = path.absolute()
        if not generation.resolve().is_relative_to((project.path / "generations").resolve()):
            raise HiggsError(
                f"{value} is not in the generations/ of Project {project.name}",
                hint="Pass a Generation from generations/YYYY-MM-DD/.",
                code="not_a_generation",
            )
        if generation.suffix.lower() == ".json" and generation.with_suffix("").is_file():
            raise HiggsError(
                f"{generation.name} is metadata, not a Generation",
                hint=f"Pass the Generation file itself: {generation.with_suffix('').name}.",
                code="not_a_generation",
            )
        return generation
    found: list[Path] = []
    if "/" not in value and "\\" not in value:
        found = sorted(
            path for path in (project.path / "generations").glob(f"*/{glob.escape(value)}") if path.is_file()
        )
    if len(found) == 1:
        return found[0]
    if found:  # the same name on two days (only by hand: names start with the date)
        listed = ", ".join(str(path.relative_to(project.path)) for path in found)
        raise HiggsError(
            f"Generation {value} is ambiguous: {listed}",
            hint="Pass the path of the one you mean.",
            code="ambiguous_generation",
        )
    if outside:  # e.g. a relative path typed in a host folder outside the projects root
        raise HiggsError(
            f"{value} is outside the projects root {root}",
            hint=(
                "Only the projects root is visible to higgs. Run the command from inside the Project, "
                "or pass the Generation's file name alone to look it up in generations/."
            ),
            code="input_outside_projects_root",
        )
    raise HiggsError(
        f"No Generation {value} in Project {project.name}",
        hint="Pass a Generation file under generations/, e.g. generations/YYYY-MM-DD/<file>, or its file name.",
        code="generation_not_found",
    )


def pick(project: Project, value: str, *, cwd: Path) -> Pick:
    """Copy a Generation of the Project into its `final/`."""
    generation = find_generation(project, value, cwd=cwd)
    final = project.path / "final"
    already_picked = (final / generation.name).exists()
    copied = copy_generation(generation, final)
    return Pick(**copied.model_dump(), already_picked=already_picked)


def save_reference(
    project: Project, value: str, name: str, *, cwd: Path, force: bool = False
) -> references.SavedReference:
    """Copy a Generation, its `.json` and its Artifacts to `references/<name>.<ext>`.

    `value` names the Generation as for `higgs pick` (`find_generation`, then
    `check_generation`), so a file that is not a Generation fails. The Artifacts are
    renamed after the Reference (`<stem>-mov.mov` → `<name>-mov.mov`, `<stem>.fbx` →
    `<name>.fbx`) and the copied `.json` lists them under their new names. A taken name
    (with any extension) fails unless `force`, which replaces the old Reference with its
    `.json` and Artifacts. Everything is checked and copied to hidden staging files first
    and moved into place last, so a failed save leaves `references/` as it was.
    """
    if not references.NAME_PATTERN.fullmatch(name):
        raise HiggsError(
            f"Invalid Reference name {name!r}",
            hint="Use a name without spaces or slashes that does not start with a dot or @, e.g. hero.",
            code="invalid_reference_name",
        )
    checked = check_generation(find_generation(project, value, cwd=cwd))
    taken = references.matching_files(project, name)
    if taken and not force:
        listed = ", ".join(references.display(path, project) for path in taken)
        raise HiggsError(
            f"Reference @{name} already exists: {listed}",
            hint="Pick another name, or pass --force to replace it.",
            code="reference_exists",
        )
    folder = references.references_dir(project)
    target = folder / f"{name}{checked.path.suffix}"
    artifact_targets = [folder / _artifact_name(checked.path, artifact, name) for artifact in checked.artifacts]
    replaced = [file for old in taken for file in (old, references.metadata_path(old), *references.artifacts_of(old))]
    _check_free(project, [target, references.metadata_path(target), *artifact_targets], replaced)
    metadata = checked.metadata.model_copy(
        update={
            "artifacts": [
                entry.model_copy(update={"file": path.name})
                for entry, path in zip(checked.metadata.artifacts, artifact_targets, strict=True)
            ]
        }
    )
    folder.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    for source, destination in [(checked.path, target), *zip(checked.artifacts, artifact_targets, strict=True)]:
        tmp = references.staging_path(destination)
        shutil.copyfile(source, tmp)
        staged.append((tmp, destination))
    tmp = references.staging_path(references.metadata_path(target))
    tmp.write_text(metadata.model_dump_json(indent=2))
    staged.append((tmp, references.metadata_path(target)))
    for old in replaced:
        old.unlink(missing_ok=True)
    for tmp, destination in staged:
        os.replace(tmp, destination)
    return references.SavedReference(
        name=name,
        path=target,
        metadata_path=references.metadata_path(target),
        artifacts=artifact_targets,
        replaced=taken,
    )


def _artifact_name(generation: Path, artifact: Path, name: str) -> str:
    """An Artifact's file name in `references/`: its Generation's base name swapped for `name`.

    Artifacts are named after the Generation (`<stem>.fbx`, `<stem>-mov.mov`), or after the
    Request's base name without `-N` when it has several Generations.
    """
    for base in (generation.stem, re.sub(r"-\d+$", "", generation.stem)):
        if artifact.name.startswith(base):
            return name + artifact.name[len(base) :]
    return f"{name}-{artifact.name}"


def _check_free(project: Project, targets: list[Path], replaced: list[Path]) -> None:
    """Fail if a file the save would write already exists and is not being replaced."""
    clashing = [path for path in targets if path.exists() and path not in replaced]
    if clashing:
        listed = ", ".join(references.display(path, project) for path in clashing)
        raise HiggsError(
            f"Saving would overwrite {listed}",
            hint="Pick another Reference name, or move those files out of references/ first.",
            code="reference_exists",
        )


def _copy(path: Path, folder: Path) -> Path:
    """A real copy of the file's content (a symlinked source is followed, not copied as a link)."""
    target = folder / path.name
    # Replace rather than write through: an existing target may be a link to the source.
    target.unlink(missing_ok=True)
    shutil.copyfile(path, target)
    shutil.copystat(path, target)
    return target
