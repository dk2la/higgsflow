"""Projects: creating, listing and finding the current one.

A Project is a folder under the projects root with a `project.toml` (DESIGN.md §5).
"""

from __future__ import annotations

import json
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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


class ProjectConfig(BaseModel):
    """The contents of `project.toml`.

    ```toml
    name = "demo"
    budget = 20.0      # optional, USD: Spend over it warns, never blocks

    [defaults]         # values for flags that were not given (`hfcore.flags.DEFAULT_KEYS`)
    model = "kling-3.0"

    [defaults.video]   # only for `higgs video`, over [defaults]
    ```

    Unknown keys are kept, so a file written by a newer version still loads.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    budget: Annotated[Decimal, Field(ge=0)] | None = None
    defaults: dict[str, Any] = {}

    @field_validator("budget", mode="before")
    @classmethod
    def _exact_budget(cls, value: Any) -> Any:
        # TOML floats are binary; their shortest repr is the number the user wrote (20.1, not 20.10000000000000142).
        return str(value) if isinstance(value, float) else value


def load_config(project: Project) -> ProjectConfig:
    """Read the Project's `project.toml`."""
    path = project.path / PROJECT_FILE
    try:
        return ProjectConfig.model_validate(tomllib.loads(path.read_text()))
    except (tomllib.TOMLDecodeError, ValidationError) as error:
        raise HiggsError(
            f"Cannot read {path}: {error}",
            hint="Fix the file; `budget` is a number of USD, e.g. `budget = 20.0`.",
            code="invalid_project_config",
        ) from None


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


def find_project(start: Path) -> Project | None:
    """The Project containing `start`: the nearest folder upwards with a `project.toml`, like git."""
    start = start.resolve()
    for folder in (start, *start.parents):
        if (folder / PROJECT_FILE).is_file():
            return Project(name=folder.name, path=folder)
    return None


def get_project(name: str) -> Project:
    """The Project with this name under the projects root."""
    _check_name(name)
    path = config.projects_root() / name
    if not (path / PROJECT_FILE).is_file():
        raise HiggsError(
            f"No Project {name} in {path.parent}",
            hint="See `higgs projects` for the existing ones, or create it with `higgs init`.",
            code="project_not_found",
        )
    return Project(name=name, path=path)


def current_project(cwd: Path, *, name: str | None = None) -> Project:
    """The Project for a project-scoped command: the one named, else the one around `cwd`."""
    if name is not None:
        return get_project(name)
    found = find_project(cwd)
    if found is None:
        raise HiggsError(
            "Not in a Project",
            hint="Run `higgs init <name>` and cd into it, or pass --project <name>.",
            code="not_in_project",
        )
    return found


def _check_name(name: str) -> None:
    # A Project name is a single folder directly under the projects root.
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        raise HiggsError(
            f"Invalid Project name {name!r}",
            hint="Use a plain folder name without slashes that does not start with a dot.",
            code="invalid_project_name",
        )
