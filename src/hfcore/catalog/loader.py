"""The single entry point to the effective catalog.

Layers, from the bottom up (DESIGN.md §6):
1. the baked-in `catalog.json` shipped as package data (built from the docs snapshot);
2. `<config dir>/catalog.json` written by `higgs models sync`, replacing layer 1 when present (#15);
3. `<config dir>/models.local.toml`, adding Models missing from the docs (#14).
Only layer 1 exists so far; `config_dir` is accepted so callers already pass it.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from hfcore.catalog.model import Catalog

BAKED_IN = "catalog.json"


def load_baked_in() -> Catalog:
    text = files("hfcore.catalog").joinpath(BAKED_IN).read_text(encoding="utf-8")
    return Catalog.model_validate_json(text)


def load_catalog(config_dir: Path | None = None) -> Catalog:
    """The effective catalog for this user."""
    return load_baked_in()
