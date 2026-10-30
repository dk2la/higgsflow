"""Global configuration: the config dir and the projects root.

Config dir: `HIGGS_CONFIG_DIR`, else `~/.config/higgs`. Projects root: `HIGGS_PROJECTS_ROOT`,
else `projects_root = "..."` in `<config dir>/config.toml`, else `~/hf-projects`. The host
wrapper (`bin/higgs`) reads the same key with a regex, so keep it a plain top-level string.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from hfcore.errors import HiggsError


def config_dir() -> Path:
    override = os.environ.get("HIGGS_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".config" / "higgs"


def projects_root() -> Path:
    override = os.environ.get("HIGGS_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser()
    configured = _read_config().get("projects_root")
    if isinstance(configured, str) and configured:
        return Path(configured).expanduser()
    return Path.home() / "hf-projects"


def _read_config() -> dict[str, object]:
    path = config_dir() / "config.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise HiggsError(f"Cannot parse {path}: {error}", code="invalid_config") from None
