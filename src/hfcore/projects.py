"""Projects: creating, listing and finding the current one.

A Project is a folder under the projects root with a `project.toml` (DESIGN.md §5).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from hfcore import config
from hfcore.errors import HiggsError

PROJECT_FILE = "project.toml"
LAYOUT = ("inputs", "references", "prompts", "generations", "final", ".hf/requests")


class Project(BaseModel):
    name: str
    path: Path

    @property
    def requests_dir(self) -> Path:
        """Where the Request records live (`.hf/requests/<request_id>.json`)."""
        return self.path / ".hf" / "requests"


def init_project(name: str) -> Project:
    """Create a Project with the standard layout under the projects root."""
    _check_name(name)
    path = config.projects_root() / name
    if path.exists():
        raise HiggsError(
            f"Project {name} already exists at {path}",
            hint="Pick another name, or work in the existing Project.",
            code="project_exists",
        )
    path.mkdir(parents=True)
    for folder in LAYOUT:
        (path / folder).mkdir(parents=True)
    # Strings are written as JSON literals, which are valid TOML basic strings.
    (path / PROJECT_FILE).write_text(f"name = {json.dumps(name)}\n\n[defaults]\n")
    return Project(name=name, path=path)


def list_projects() -> list[Project]:
    """Every Project directly under the projects root, by name."""
    root = config.projects_root()
    if not root.is_dir():
        return []
    return [
        Project(name=path.name, path=path)
        for path in sorted(root.iterdir())
        if (path / PROJECT_FILE).is_file()
    ]


def _check_name(name: str) -> None:
    # A Project name is a single folder directly under the projects root.
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        raise HiggsError(
            f"Invalid Project name {name!r}",
            hint="Use a plain folder name without slashes that does not start with a dot.",
            code="invalid_project_name",
        )
