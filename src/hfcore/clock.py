"""The core's clock: the one source of "now" and the one way to wait.

Polling, retries and timestamps all go through it, so tests can replace it with an
instant fake that moves time forward and records the requested waits.

Call it as `clock.now()` / `clock.sleep(...)` (not `from hfcore.clock import now`), so
a replacement installed by a test is seen everywhere.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


def now() -> datetime:
    """The current time, timezone-aware in UTC."""
    return datetime.now(UTC)


def sleep(seconds: float) -> None:
    """Wait `seconds` (between polls, before a retry)."""
    time.sleep(seconds)
