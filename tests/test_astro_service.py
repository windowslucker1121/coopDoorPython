"""Tests for services.astro_service."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import pytz

from services.astro_service import AstroService


class TestAstroServiceGetSunriseSunset:
    def test_returns_two_datetimes(self, gv) -> None:
        gv.set_value(
            "location",
            {
                "city": "Boulder",
                "region": "USA",
                "timezone": "America/Denver",
                "latitude": 40.01499,
                "longitude": -105.27055,
            },
        )
        svc = AstroService(gv)
        svc.reload_location()
        sunrise, sunset = svc.get_sunrise_and_sunset()
        assert isinstance(sunrise, datetime)
        assert isinstance(sunset, datetime)

    def test_sunrise_before_sunset(self, gv) -> None:
        gv.set_value(
            "location",
            {
                "city": "Boulder",
                "region": "USA",
                "timezone": "America/Denver",
                "latitude": 40.01499,
                "longitude": -105.27055,
            },
        )
        svc = AstroService(gv)
        svc.reload_location()
        sunrise, sunset = svc.get_sunrise_and_sunset()
        assert sunrise < sunset

    def test_datetimes_are_timezone_aware(self, gv) -> None:
        gv.set_value(
            "location",
            {
                "city": "Boulder",
                "region": "USA",
                "timezone": "America/Denver",
                "latitude": 40.01499,
                "longitude": -105.27055,
            },
        )
        svc = AstroService(gv)
        svc.reload_location()
        sunrise, _ = svc.get_sunrise_and_sunset()
        assert sunrise.tzinfo is not None


class TestAstroServiceReloadLocation:
    def test_reload_updates_location(self, gv) -> None:
        gv.set_value(
            "location",
            {
                "city": "Lagos",
                "region": "Nigeria",
                "timezone": "Africa/Lagos",
                "latitude": 6.45,
                "longitude": 3.39,
            },
        )
        svc = AstroService(gv)
        svc.reload_location()
        assert svc._location.name == "Lagos"
        assert str(svc._timezone) == "Africa/Lagos"

    def test_reload_with_missing_location_logs_warning(self, gv, caplog) -> None:
        import logging

        svc = AstroService(gv)
        with caplog.at_level(logging.WARNING, logger="services.astro_service"):
            svc.reload_location()  # gv has no "location" key
        assert "no location" in caplog.text.lower() or "default" in caplog.text.lower()

    def test_reload_with_invalid_timezone_logs_error(self, gv, caplog) -> None:
        import logging

        gv.set_value(
            "location",
            {
                "city": "X",
                "region": "Y",
                "timezone": "Invalid/Tz",
                "latitude": 0.0,
                "longitude": 0.0,
            },
        )
        svc = AstroService(gv)
        with caplog.at_level(logging.ERROR, logger="services.astro_service"):
            svc.reload_location()
        assert "invalid" in caplog.text.lower() or "error" in caplog.text.lower()


class TestAstroServiceGetCurrentTime:
    def test_returns_timezone_aware_datetime(self, gv) -> None:
        gv.set_value(
            "location",
            {
                "city": "Boulder",
                "region": "USA",
                "timezone": "America/Denver",
                "latitude": 40.01499,
                "longitude": -105.27055,
            },
        )
        svc = AstroService(gv)
        svc.reload_location()
        now = svc.get_current_time()
        assert now.tzinfo is not None

    def test_current_time_timezone_matches_location(self, gv) -> None:
        gv.set_value(
            "location",
            {
                "city": "Berlin",
                "region": "Germany",
                "timezone": "Europe/Berlin",
                "latitude": 52.5,
                "longitude": 13.4,
            },
        )
        svc = AstroService(gv)
        svc.reload_location()
        now = svc.get_current_time()
        assert "Europe/Berlin" in str(now.tzinfo)


class TestGetValidLocations:
    def test_returns_list(self) -> None:
        locations = AstroService.get_valid_locations()
        assert isinstance(locations, list)
        assert len(locations) > 0

    def test_entries_have_required_keys(self) -> None:
        locations = AstroService.get_valid_locations()
        for loc in locations[:5]:
            for key in ("name", "region", "timezone", "latitude", "longitude"):
                assert key in loc

    def test_list_is_sorted(self) -> None:
        locations = AstroService.get_valid_locations()
        names = [str(loc["name"]) for loc in locations]
        assert names == sorted(names)
