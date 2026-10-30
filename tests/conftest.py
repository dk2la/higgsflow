"""Shared fixtures for the CLI seam.

Every test gets a temporary projects root and config dir (via env vars the
core reads), and a `higgs(*args)` helper that invokes the CLI in-process.
HTTP is replaced at the transport level with respx in the tests that need it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

import hfcore.clock
from hfcli.app import app


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "hf-projects"
    root.mkdir()
    return root


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    path = tmp_path / "config" / "higgs"
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, projects_root: Path, config_dir: Path):
    for var in ("HF_KEY", "HF_API_KEY", "HF_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HIGGS_PROJECTS_ROOT", str(projects_root))
    monkeypatch.setenv("HIGGS_CONFIG_DIR", str(config_dir))


@pytest.fixture
def higgs():
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None):
        return runner.invoke(app, list(args), input=input, catch_exceptions=False)

    return invoke


@pytest.fixture
def project(higgs, projects_root: Path, monkeypatch) -> Path:
    """A Project named `demo`, with the current directory inside it."""
    higgs("init", "demo")
    path = projects_root / "demo"
    monkeypatch.chdir(path)
    return path


class FakeClock:
    """The core's clock (`hfcore.clock.now`), frozen until a test moves it forward."""

    def __init__(self, start: datetime):
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    """Freeze the core's clock at 2026-10-30 12:00 UTC."""
    fake = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=UTC))
    monkeypatch.setattr(hfcore.clock, "now", fake.now)
    return fake


@pytest.fixture
def api_key(higgs) -> str:
    """A key saved with `higgs auth login`; returns the expected `Authorization` header."""
    higgs("auth", "login", "--key", "key-id:key-secret")
    return "Key key-id:key-secret"
