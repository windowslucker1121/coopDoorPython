"""Open / close schedules.

A schedule yields a :class:`DayWindow` (open and close time) for a given day.
The control loop asks two questions:

* :func:`desired_at` — where should the door be *right now*?  Used when
  (re)synchronising: at boot, when a mode is switched on, after an error.
* :func:`crossed_boundary` — was an open/close time passed since the last
  evaluation?  Edge detection (instead of "within one minute after") means
  a slow loop iteration can never skip a transition, and manual commands
  given between two transitions are respected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Protocol

from .model import DesiredState


@dataclass(frozen=True)
class DayWindow:
    open_time: datetime
    close_time: datetime


class Schedule(Protocol):
    def window(self, day: date, tz: tzinfo) -> DayWindow | None: ...


def localize(tz: tzinfo, naive: datetime) -> datetime:
    return tz.localize(naive) if hasattr(tz, "localize") else naive.replace(tzinfo=tz)


class TimerSchedule:
    """Fixed daily times ("HH:MM").  ``close`` may be earlier than ``open``
    (door open overnight)."""

    def __init__(self, open_hhmm: str, close_hhmm: str):
        self.open_at = _parse(open_hhmm)
        self.close_at = _parse(close_hhmm)

    def window(self, day: date, tz: tzinfo) -> DayWindow:
        return DayWindow(localize(tz, datetime.combine(day, self.open_at)),
                         localize(tz, datetime.combine(day, self.close_at)))


class SunSchedule:
    """Sunrise + offset / sunset + offset."""

    def __init__(self, sun_calculator, sunrise_offset_min: int = 0, sunset_offset_min: int = 0):
        self._sun = sun_calculator
        self._sunrise_offset = timedelta(minutes=sunrise_offset_min)
        self._sunset_offset = timedelta(minutes=sunset_offset_min)

    def window(self, day: date, tz: tzinfo) -> DayWindow | None:
        times = self._sun.sun_times(day)
        if times is None:
            return None
        sunrise, sunset = times
        return DayWindow(sunrise + self._sunrise_offset, sunset + self._sunset_offset)


def _parse(hhmm: str) -> time:
    return datetime.strptime(hhmm, "%H:%M").time()


def in_open_period(now: datetime, window: DayWindow) -> bool:
    """True if the door should be open at *now* (same-day window).

    Handles windows that wrap past midnight; identical times mean "never
    open"."""
    o, c = window.open_time, window.close_time
    if o < c:
        return o <= now < c
    if o > c:
        return now >= o or now < c
    return False


def desired_at(schedule: Schedule, now: datetime) -> DesiredState | None:
    window = schedule.window(now.date(), now.tzinfo)
    if window is None:
        return None
    return DesiredState.OPEN if in_open_period(now, window) else DesiredState.CLOSED


def crossed_boundary(schedule: Schedule, since: datetime, now: datetime) -> DesiredState | None:
    """Latest open/close boundary in ``(since, now]``, if any."""
    if now <= since:
        return None
    events: list[tuple[datetime, DesiredState]] = []
    day = since.date()
    while day <= now.date():
        window = schedule.window(day, now.tzinfo)
        if window is not None:
            events.append((window.open_time, DesiredState.OPEN))
            events.append((window.close_time, DesiredState.CLOSED))
        day += timedelta(days=1)
    crossed = [e for e in events if since < e[0] <= now]
    if not crossed:
        return None
    return max(crossed, key=lambda e: e[0])[1]
