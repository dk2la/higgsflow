"""The single entry point to the effective catalog.

Layers, from the bottom up (DESIGN.md §6):
1. the baked-in `catalog.json` shipped as package data (built from the docs snapshot);
2. `<config dir>/catalog.json` written by `higgs models sync`, replacing layer 1 when present (#15);
3. `<config dir>/models.local.toml`, adding Models missing from the docs (#14).
Layers 1–2 make the base catalog (`load_base_catalog`), the one `models sync` compares with.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from hfcore.catalog.model import Catalog

BAKED_IN = "catalog.json"
USER_CATALOG = "catalog.json"


def load_baked_in() -> Catalog:
    text = files("hfcore.catalog").joinpath(BAKED_IN).read_text(encoding="utf-8")
    return Catalog.model_validate_json(text)


def user_catalog_path(config_dir: Path) -> Path:
    return config_dir / USER_CATALOG


def load_base_catalog(config_dir: Path | None = None) -> Catalog:
    """The user catalog written by `higgs models sync` when present, else the baked-in one."""
    if config_dir is not None:
        path = user_catalog_path(config_dir)
        if path.is_file():
            return Catalog.model_validate_json(path.read_text(encoding="utf-8"))
    return load_baked_in()


def load_catalog(config_dir: Path | None = None) -> Catalog:
    """The effective catalog for this user."""
    return load_base_catalog(config_dir)
