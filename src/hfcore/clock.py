"""The core's clock: the one source of "now", so tests can move time forward.

Call it as `clock.now()` (not `from hfcore.clock import now`), so a replacement
installed by a test is seen everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """The current time, timezone-aware in UTC."""
    return datetime.now(UTC)
