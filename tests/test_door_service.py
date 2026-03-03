"""Tests for services.door_service — especially the obstruction-detection logic."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from models.door_model import CloseAttemptResult
from services.door_service import (
    OBSTRUCTION_TOLERANCE_MS,
    MAX_CLOSE_RETRIES,
    DoorService,
)


# ---------------------------------------------------------------------------
# Obstruction-detection: validate_close_endstop
# ---------------------------------------------------------------------------


class TestValidateCloseEndstop:
    """Unit tests for the core obstruction-detection algorithm."""

    def _svc(self) -> DoorService:
        return DoorService(MagicMock())

    def test_genuine_close_exact_reference(self) -> None:
        """Elapsed == reference → accepted, no obstruction."""
        svc = self._svc()
        result = svc.validate_close_endstop(6000.0, 6000.0, 1)
        assert result.accepted is True
        assert result.obstruction_detected is False

    def test_genuine_close_within_tolerance(self) -> None:
        """Elapsed only slightly below reference — still accepted."""
        svc = self._svc()
        # reference - elapsed = 999 ms < 1000 ms tolerance
        result = svc.validate_close_endstop(5001.0, 6000.0, 1)
        assert result.accepted is True
        assert result.obstruction_detected is False

    def test_genuine_close_exceeds_reference(self) -> None:
        """Elapsed > reference (door slower than expected) → still accepted."""
        svc = self._svc()
        result = svc.validate_close_endstop(7500.0, 6000.0, 1)
        assert result.accepted is True
        assert result.obstruction_detected is False

    def test_obstruction_exactly_at_tolerance_boundary(self) -> None:
        """reference - elapsed == 1000 ms is NOT an obstruction (strict >)."""
        svc = self._svc()
        result = svc.validate_close_endstop(5000.0, 6000.0, 1)
        # 6000 - 5000 = 1000, NOT > 1000 → not obstruction
        assert result.accepted is True
        assert result.obstruction_detected is False

    def test_obstruction_one_ms_over_tolerance(self) -> None:
        """reference - elapsed == 1001 ms → obstruction detected."""
        svc = self._svc()
        result = svc.validate_close_endstop(4999.0, 6000.0, 1)
        assert result.accepted is False
        assert result.obstruction_detected is True

    def test_obstruction_fires_very_early(self) -> None:
        """Endstop fires at 1 s when reference is 6 s → clear obstruction."""
        svc = self._svc()
        result = svc.validate_close_endstop(1000.0, 6000.0, 1)
        assert result.obstruction_detected is True
        assert result.elapsed_ms == pytest.approx(1000.0)
        assert result.reference_ms == pytest.approx(6000.0)

    def test_result_fields_populated(self) -> None:
        svc = self._svc()
        result = svc.validate_close_endstop(
            elapsed_ms=2000.0,
            reference_ms=7000.0,
            attempt_number=2,
        )
        assert result.attempt_number == 2
        assert result.elapsed_ms == pytest.approx(2000.0)
        assert result.reference_ms == pytest.approx(7000.0)

    def test_obstruction_tolerance_constant_is_1000(self) -> None:
        assert OBSTRUCTION_TOLERANCE_MS == pytest.approx(1000.0)

    def test_max_retries_constant_is_3(self) -> None:
        assert MAX_CLOSE_RETRIES == 3

    @pytest.mark.parametrize(
        "elapsed_ms, reference_ms, expected",
        [
            (5500.0, 6000.0, False),   # delta 500 → no obstruction
            (4999.0, 6000.0, True),    # delta 1001 → obstruction
            (0.0, 6000.0, True),       # door barely moved → obstruction
            (6001.0, 6000.0, False),   # elapsed > reference → no obstruction
        ],
    )
    def test_parametrized_cases(
        self,
        elapsed_ms: float,
        reference_ms: float,
        expected: bool,
    ) -> None:
        svc = self._svc()
        result = svc.validate_close_endstop(elapsed_ms, reference_ms, 1)
        assert result.obstruction_detected is expected


# ---------------------------------------------------------------------------
# get_elapsed_closing_ms
# ---------------------------------------------------------------------------


class TestGetElapsedClosingMs:
    def test_returns_none_when_not_started(self) -> None:
        door = MagicMock()
        door.get_started_moving_time.return_value = None
        svc = DoorService(door)
        assert svc.get_elapsed_closing_ms() is None

    def test_returns_elapsed_ms(self) -> None:
        door = MagicMock()
        start = time.time() - 3.5  # started 3.5 seconds ago
        door.get_started_moving_time.return_value = start
        svc = DoorService(door)
        elapsed = svc.get_elapsed_closing_ms()
        assert elapsed is not None
        assert 3400 < elapsed < 3700  # generous window for CI latency

    def test_elapsed_increases_over_time(self) -> None:
        door = MagicMock()
        start = time.time()
        door.get_started_moving_time.return_value = start
        svc = DoorService(door)
        e1 = svc.get_elapsed_closing_ms()
        time.sleep(0.05)
        e2 = svc.get_elapsed_closing_ms()
        assert e2 > e1  # type: ignore[operator]


# ---------------------------------------------------------------------------
# resume_after_obstruction
# ---------------------------------------------------------------------------


class TestResumeAfterObstruction:
    def test_calls_stop_with_stopped_state(self) -> None:
        door = MagicMock()
        svc = DoorService(door)
        svc.resume_after_obstruction()
        door.stop.assert_called_once_with(state="stopped")


# ---------------------------------------------------------------------------
# Delegation methods
# ---------------------------------------------------------------------------


class TestDoorServiceDelegation:
    """Quick-fire delegation tests ensuring DoorService calls the right DOOR methods."""

    def setup_method(self) -> None:
        self.door = MagicMock()
        self.door.get_state.return_value = "open"
        self.door.get_override.return_value = True
        self.door.errorState = None
        self.door.reference_door_endstops_ms = 5500.0
        self.svc = DoorService(self.door)

    def test_get_state(self) -> None:
        assert self.svc.get_state() == "open"
        self.door.get_state.assert_called_once()

    def test_get_override(self) -> None:
        assert self.svc.get_override() is True

    def test_open_delegates(self) -> None:
        self.svc.open()
        self.door.open.assert_called_once()

    def test_close_delegates(self) -> None:
        self.svc.close()
        self.door.close.assert_called_once()

    def test_stop_default_state(self) -> None:
        self.svc.stop()
        self.door.stop.assert_called_once_with(state="stopped")

    def test_stop_custom_state(self) -> None:
        self.svc.stop("closed")
        self.door.stop.assert_called_once_with(state="closed")

    def test_check_endstops_delegates(self) -> None:
        self.door.check_endstops.return_value = True
        assert self.svc.check_endstops() is True

    def test_check_if_switch_neutral_delegates(self) -> None:
        self.svc.check_if_switch_neutral("open")
        self.door.check_if_switch_neutral.assert_called_once_with(nuetral_state="open")

    def test_reference_endstops_delegates(self) -> None:
        self.door.reference_endstops.return_value = True
        assert self.svc.reference_endstops() is True

    def test_set_auto_mode_delegates(self) -> None:
        self.svc.set_auto_mode(True)
        self.door.set_auto_mode.assert_called_once_with(True)

    def test_set_error(self) -> None:
        self.svc.set_error("test error")
        self.door.ErrorState.assert_called_once_with(state="test error", stopDoor=True)

    def test_clear_error(self) -> None:
        self.svc.clear_error()
        self.door.clear_errorState.assert_called_once()

    def test_is_error_false_when_no_error(self) -> None:
        self.door.ErrorState.return_value = False
        assert self.svc.is_error() is False

    def test_is_error_true_when_error(self) -> None:
        self.door.ErrorState.return_value = True
        assert self.svc.is_error() is True

    def test_error_state_returns_door_errorstate(self) -> None:
        self.door.errorState = "something went wrong"
        assert self.svc.error_state() == "something went wrong"

    def test_get_reference_ms(self) -> None:
        assert self.svc.get_reference_ms() == pytest.approx(5500.0)
