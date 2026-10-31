"""Prompts kept as files in the Project (`--prompt-file prompts/x.md`)."""

from __future__ import annotations

from pathlib import Path

from hfcore.errors import HiggsError
from hfcore.projects import Project, check_inside_projects_root


def read_prompt_file(project: Project, value: str, *, cwd: Path) -> str:
    """The prompt in the file `value` (relative to `cwd`), without trailing whitespace.

    The file must be inside the Project, so the prompt travels with it. A file outside the
    projects root is `input_outside_projects_root`, like any Input there (#44).
    """
    path = cwd / Path(value).expanduser()
    check_inside_projects_root(path, value, project, what="Prompt file", folder="prompts")
    path = path.resolve()
    if not path.is_relative_to(project.path.resolve()):
        raise HiggsError(
            f"Prompt file {value} is outside Project {project.name}",
            hint=f"Keep prompts in the Project, e.g. {project.path / 'prompts'}/.",
            code="prompt_file_outside_project",
        )
    try:
        return path.read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError) as error:
        raise HiggsError(
            f"Cannot read prompt file {value} ({path}): {error}",
            hint="Check the path; it is relative to the folder higgs was run in.",
            code="prompt_file_not_found",
        ) from None
