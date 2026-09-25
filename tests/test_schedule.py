"""Schedules: open periods, wrap-around timers, boundary crossings, sun times."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from conftest import DENVER, at
from coop.config import LocationConfig
from coop.door.model import DesiredState
from coop.door.schedule import (DayWindow, SunSchedule, TimerSchedule, crossed_boundary, desired_at,
                                in_open_period, localize)
from coop.services.sun import SunCalculator, list_locations


def window(open_h, close_h):
    return DayWindow(at(open_h), at(close_h))


@pytest.mark.parametrize("hour, expected", [(7, False), (8, True), (19, True), (20, False)])
def test_in_open_period_same_day(hour, expected):
    assert in_open_period(at(hour), window(8, 20)) is expected


@pytest.mark.parametrize("hour, expected", [(21, True), (2, True), (6, False), (12, False)])
def test_in_open_period_wraps_midnight(hour, expected):
    assert in_open_period(at(hour), window(20, 6)) is expected


def test_identical_times_mean_never_open():
    assert in_open_period(at(8), window(8, 8)) is False


def test_timer_window_localized():
    w = TimerSchedule("06:30", "21:15").window(date(2025, 6, 1), DENVER)
    assert w.open_time == DENVER.localize(datetime(2025, 6, 1, 6, 30))
    assert w.close_time.utcoffset() == timedelta(hours=-6)


def test_localize_with_stdlib_timezone():
    from datetime import timezone
    assert localize(timezone.utc, datetime(2025, 1, 1)).tzinfo is timezone.utc


def test_desired_at():
    s = TimerSchedule("08:00", "20:00")
    assert desired_at(s, at(12)) is DesiredState.OPEN
    assert desired_at(s, at(21)) is DesiredState.CLOSED


class TestCrossings:
    s = TimerSchedule("08:00", "20:00")

    def test_none(self):
        assert crossed_boundary(self.s, at(9), at(10)) is None

    def test_open(self):
        assert crossed_boundary(self.s, at(7, 59), at(8, 0)) is DesiredState.OPEN

    def test_boundary_is_inclusive_only_at_the_end(self):
        assert crossed_boundary(self.s, at(8, 0), at(8, 1)) is None

    def test_close(self):
        assert crossed_boundary(self.s, at(19, 59), at(20, 30)) is DesiredState.CLOSED

    def test_latest_wins(self):
        assert crossed_boundary(self.s, at(7), at(21)) is DesiredState.CLOSED

    def test_across_midnight(self):
        s = TimerSchedule("20:00", "06:00")
        assert crossed_boundary(s, at(5, 59, day=1), at(6, 1, day=1)) is DesiredState.CLOSED
        assert crossed_boundary(s, at(23, day=1), at(0, 30, day=2)) is None

    def test_backwards_time(self):
        assert crossed_boundary(self.s, at(10), at(9)) is None


class TestSun:

    def test_sun_schedule_offsets(self):
        sun = SunCalculator(LocationConfig())
        rise, set_ = sun.sun_times(date(2025, 6, 1))
        w = SunSchedule(sun, 30, -15).window(date(2025, 6, 1), DENVER)
        assert w.open_time == rise + timedelta(minutes=30)
        assert w.close_time == set_ - timedelta(minutes=15)
        assert 5 <= rise.hour <= 6 and 20 <= set_.hour <= 21  # Boulder in June
        assert rise.tzinfo.zone == "America/Denver"

    def test_cached(self):
        sun = SunCalculator(LocationConfig())
        assert sun.sun_times(date(2025, 6, 1)) is sun.sun_times(date(2025, 6, 1))

    def test_polar_night_returns_none(self):
        sun = SunCalculator(LocationConfig("Alert", "CA", "America/Toronto", 82.5, -62.3))
        assert sun.sun_times(date(2025, 12, 21)) is None
        assert SunSchedule(sun).window(date(2025, 12, 21), DENVER) is None

    def test_locations_sorted_and_cached(self):
        locs = list_locations()
        assert len(locs) > 100
        assert list(locs) == sorted(locs, key=lambda x: (x["name"], x["region"]))
        assert any(l["name"] == "europe - berlin" for l in locs)
        assert list_locations() is locs
