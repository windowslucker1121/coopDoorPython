"""Time abstraction.

All time-dependent logic receives a :class:`Clock` so it can be tested
deterministically with :class:`FakeClock`.

* ``monotonic()`` — for measuring durations (motor run time, retries).
  Never affected by the wall clock being changed (NTP, ``/api/system/time``).
* ``now()`` — timezone-aware wall-clock time in the *location's* timezone,
  used for schedules.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, tzinfo
from typing import Callable

import pytz


class Clock:
    def __init__(self, tz_provider: Callable[[], tzinfo] | None = None):
        self._tz_provider = tz_provider or (lambda: pytz.utc)

    def now(self) -> datetime:
        return datetime.now(self._tz_provider())

    def tz(self) -> tzinfo:
        return self._tz_provider()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock(Clock):
    """Manually advanced clock for tests.  ``sleep`` advances time."""

    def __init__(self, start: datetime | None = None, tz: tzinfo | None = None):
        tz = tz or pytz.utc
        super().__init__(lambda: tz)
        if start is None:
            start = datetime(2025, 6, 1, 12, 0)
        if start.tzinfo is None:
            start = tz.localize(start) if hasattr(tz, "localize") else start.replace(tzinfo=tz)
        self._now = start
        self._mono = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._now = self._now + timedelta(seconds=seconds)

    def set_now(self, value: datetime) -> None:
        """Jump the wall clock (monotonic time is unaffected)."""
        self._now = value
