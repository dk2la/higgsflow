"""The clock the core waits on (polling, retries) and reads the time from.

Everything that sleeps or stamps a time goes through `current()`, so a test (or the
stage 2 server) can swap in its own clock with `use(...)`: tests use an instant fake
that records the requested waits.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware."""
        ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


_current: Clock = SystemClock()


def current() -> Clock:
    return _current


def use(clock: Clock) -> Clock:
    """Make `clock` the one the core uses; returns the previous one (to restore it)."""
    global _current
    previous, _current = _current, clock
    return previous
