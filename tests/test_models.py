"""Tests for models.door_model and models.config_model."""

from __future__ import annotations

import pytest

from models.door_model import CloseAttemptResult, DoorState
from models.config_model import AppConfig, LocationConfig


# ---------------------------------------------------------------------------
# DoorState
# ---------------------------------------------------------------------------


class TestDoorState:
    def test_values_are_correct_strings(self) -> None:
        assert DoorState.STOPPED == "stopped"
        assert DoorState.OPENING == "opening"
        assert DoorState.OPEN == "open"
        assert DoorState.CLOSING == "closing"
        assert DoorState.CLOSED == "closed"

    def test_is_str_subclass(self) -> None:
        assert isinstance(DoorState.OPEN, str)

    def test_equality_with_plain_string(self) -> None:
        assert DoorState.CLOSED == "closed"
        assert DoorState.OPEN != "closed"

    def test_all_states_covered(self) -> None:
        names = {m.value for m in DoorState}
        assert names == {"stopped", "opening", "open", "closing", "closed"}


# ---------------------------------------------------------------------------
# CloseAttemptResult
# ---------------------------------------------------------------------------


class TestCloseAttemptResult:
    def _make(self, **kwargs) -> CloseAttemptResult:
        defaults = dict(
            accepted=True,
            elapsed_ms=5000.0,
            reference_ms=6000.0,
            attempt_number=1,
            obstruction_detected=False,
        )
        defaults.update(kwargs)
        return CloseAttemptResult(**defaults)

    def test_genuine_close(self) -> None:
        result = self._make(accepted=True, obstruction_detected=False)
        assert result.accepted is True
        assert result.obstruction_detected is False

    def test_obstruction_close(self) -> None:
        result = self._make(
            accepted=False,
            elapsed_ms=2000.0,
            reference_ms=6000.0,
            obstruction_detected=True,
            attempt_number=2,
        )
        assert result.accepted is False
        assert result.obstruction_detected is True
        assert result.attempt_number == 2
        assert result.reference_ms == 6000.0

    def test_is_frozen(self) -> None:
        """CloseAttemptResult is immutable (frozen=True)."""
        result = self._make()
        with pytest.raises((AttributeError, TypeError)):
            result.accepted = False  # type: ignore[misc]

    def test_elapsed_and_reference_stored(self) -> None:
        result = self._make(elapsed_ms=3500.5, reference_ms=7200.0)
        assert result.elapsed_ms == pytest.approx(3500.5)
        assert result.reference_ms == pytest.approx(7200.0)


# ---------------------------------------------------------------------------
# LocationConfig
# ---------------------------------------------------------------------------


class TestLocationConfig:
    def test_defaults(self) -> None:
        loc = LocationConfig()
        assert loc.city == "Boulder"
        assert loc.region == "USA"
        assert loc.timezone == "America/Denver"
        assert loc.latitude == pytest.approx(40.01499)
        assert loc.longitude == pytest.approx(-105.27055)

    def test_to_dict(self) -> None:
        loc = LocationConfig(city="Lagos", region="Nigeria", timezone="Africa/Lagos",
                             latitude=6.45, longitude=3.39)
        d = loc.to_dict()
        assert d["city"] == "Lagos"
        assert d["latitude"] == pytest.approx(6.45)

    def test_custom_values(self) -> None:
        loc = LocationConfig(city="Berlin", region="Germany",
                             timezone="Europe/Berlin", latitude=52.5, longitude=13.4)
        assert loc.city == "Berlin"
        assert loc.timezone == "Europe/Berlin"


# ---------------------------------------------------------------------------
# AppConfig
# ---------------------------------------------------------------------------


class TestAppConfig:
    def test_defaults(self) -> None:
        cfg = AppConfig()
        assert cfg.auto_mode == "True"
        assert cfg.sunrise_offset == 0
        assert cfg.sunset_offset == 0
        assert cfg.csvLog is True
        assert cfg.enable_camera is False
        assert cfg.reference_door_endstops_ms is None

    def test_location_default_is_locationconfig(self) -> None:
        cfg = AppConfig()
        assert isinstance(cfg.location, LocationConfig)

    def test_location_instances_are_independent(self) -> None:
        """Each AppConfig gets its own LocationConfig instance."""
        a = AppConfig()
        b = AppConfig()
        a.location.city = "X"
        assert b.location.city == "Boulder"

    def test_reference_ms_can_be_set(self) -> None:
        cfg = AppConfig(reference_door_endstops_ms=4500.0)
        assert cfg.reference_door_endstops_ms == pytest.approx(4500.0)

    def test_to_dict_contains_all_keys(self) -> None:
        cfg = AppConfig()
        d = cfg.to_dict()
        for key in (
            "auto_mode", "sunrise_offset", "sunset_offset", "location",
            "consoleLogToFile", "csvLog", "enable_camera", "camera_index",
            "reference_door_endstops_ms",
        ):
            assert key in d
