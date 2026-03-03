"""Tests for tasks.door_task._single_iteration() and door_task_main()."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from models.door_model import CloseAttemptResult
from services.door_service import MAX_CLOSE_RETRIES
from tasks.door_task import _single_iteration, door_task_main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    *,
    prev_door_state: str = "stopped",
    close_attempt_count: int = 0,
    door_move_count: float = 0.0,
    first_iter: bool = False,
    thread_sleep_time: float = 0.5,
    last_d_door_state: str | None = None,
    sent_error_notification: bool = False,
    sunrise=None,
    sunset=None,
) -> dict:
    return {
        "door_move_count": door_move_count,
        "first_iter": first_iter,
        "sunrise": sunrise,
        "sunset": sunset,
        "sent_error_notification": sent_error_notification,
        "thread_sleep_time": thread_sleep_time,
        "last_d_door_state": last_d_door_state,
        "prev_door_state": prev_door_state,
        "close_attempt_count": close_attempt_count,
    }


def _make_services(gv, door_state: str = "closed"):
    """Return (door_service, notification_service, astro_service) mocks."""
    ds = MagicMock()
    ds.get_state.return_value = door_state
    ds.get_override.return_value = False
    ds.is_error.return_value = False
    ds.error_state.return_value = ""
    ds.check_endstops.return_value = False
    ds.check_if_switch_neutral.return_value = None

    ns = MagicMock()

    now = datetime.now(tz=timezone.utc)
    ast = MagicMock()
    ast.get_sunrise_and_sunset.return_value = (now, now)
    ast.get_current_time.return_value = now

    # Set required global_vars keys for _single_iteration
    gv.set_values(
        {
            "toggle_reference_of_endstops": False,
            "clear_error_state": False,
            "debug_error": False,
            "desired_door_state": door_state,
            "auto_mode": "False",
            "reference_door_endstops_ms": 5000.0,
            "sunrise_offset": 0,
            "sunset_offset": 0,
        }
    )

    return ds, ns, ast


# ---------------------------------------------------------------------------
# Obstruction detection
# ---------------------------------------------------------------------------


class TestObstructionDetected:
    def test_resume_called_on_first_obstruction(self, gv) -> None:
        """prev_state==closing, door==closed, elapsed much less than reference → resume."""
        ds, ns, ast = _make_services(gv, door_state="closed")

        # Door was closing and just hit the endstop early
        state = _make_state(prev_door_state="closing", close_attempt_count=0)

        obstruction_result = CloseAttemptResult(
            accepted=False,
            elapsed_ms=1000.0,
            reference_ms=5000.0,
            attempt_number=1,
            obstruction_detected=True,
        )
        ds.get_elapsed_closing_ms.return_value = 1000.0
        ds.validate_close_endstop.return_value = obstruction_result
        ds.resume_after_obstruction.return_value = None
        ds.get_state.side_effect = ["closed", "stopped", "stopped"]  # before/after resume + final

        _single_iteration(
            door_service=ds,
            notification_service=ns,
            astro_service=ast,
            state=state,
        )

        ds.resume_after_obstruction.assert_called_once()
        assert state["close_attempt_count"] == 1

    def test_close_attempt_count_incremented(self, gv) -> None:
        ds, ns, ast = _make_services(gv, door_state="closed")
        state = _make_state(prev_door_state="closing", close_attempt_count=1)

        obstruction_result = CloseAttemptResult(
            accepted=False,
            elapsed_ms=1500.0,
            reference_ms=5000.0,
            attempt_number=2,
            obstruction_detected=True,
        )
        ds.get_elapsed_closing_ms.return_value = 1500.0
        ds.validate_close_endstop.return_value = obstruction_result
        ds.get_state.side_effect = ["closed", "stopped", "stopped"]  # initial, after-resume, final

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        assert state["close_attempt_count"] == 2

    def test_error_state_and_notification_after_max_retries(self, gv) -> None:
        """After MAX_CLOSE_RETRIES consecutive obstructions, error + notification."""
        ds, ns, ast = _make_services(gv, door_state="closed")
        state = _make_state(
            prev_door_state="closing", close_attempt_count=MAX_CLOSE_RETRIES - 1
        )

        obstruction_result = CloseAttemptResult(
            accepted=False,
            elapsed_ms=500.0,
            reference_ms=5000.0,
            attempt_number=MAX_CLOSE_RETRIES,
            obstruction_detected=True,
        )
        ds.get_elapsed_closing_ms.return_value = 500.0
        ds.validate_close_endstop.return_value = obstruction_result
        ds.get_state.return_value = "closed"

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.set_error.assert_called_once()
        ns.send.assert_called_once()
        # resume_after_obstruction should NOT be called once max retries reached
        ds.resume_after_obstruction.assert_not_called()


class TestNoObstruction:
    def test_close_attempt_count_reset_on_clean_close(self, gv) -> None:
        """Genuine close (elapsed ≥ reference − tolerance) → reset count."""
        ds, ns, ast = _make_services(gv, door_state="closed")
        state = _make_state(
            prev_door_state="closing", close_attempt_count=2
        )

        clean_result = CloseAttemptResult(
            accepted=True,
            elapsed_ms=4800.0,
            reference_ms=5000.0,
            attempt_number=3,
            obstruction_detected=False,
        )
        ds.get_elapsed_closing_ms.return_value = 4800.0
        ds.validate_close_endstop.return_value = clean_result
        ds.get_state.return_value = "closed"

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        assert state["close_attempt_count"] == 0
        ds.resume_after_obstruction.assert_not_called()
        ns.send.assert_not_called()

    def test_obstruction_check_skipped_when_not_closing(self, gv) -> None:
        """If prev_state is 'open', the obstruction check must be skipped entirely."""
        ds, ns, ast = _make_services(gv, door_state="closed")
        state = _make_state(prev_door_state="open")

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.validate_close_endstop.assert_not_called()
        ds.resume_after_obstruction.assert_not_called()

    def test_obstruction_check_skipped_when_reference_not_set(self, gv) -> None:
        """If reference_door_endstops_ms is None, skip validation."""
        ds, ns, ast = _make_services(gv, door_state="closed")
        gv.set_value("reference_door_endstops_ms", None)
        state = _make_state(prev_door_state="closing")

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.validate_close_endstop.assert_not_called()


# ---------------------------------------------------------------------------
# clear_error_state handling
# ---------------------------------------------------------------------------


class TestClearError:
    def test_clear_error_resets_close_attempt_count(self, gv) -> None:
        ds, ns, ast = _make_services(gv, door_state="stopped")
        gv.set_value("clear_error_state", True)
        state = _make_state(close_attempt_count=3)

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.clear_error.assert_called_once()
        assert state["close_attempt_count"] == 0
        assert state["sent_error_notification"] is False

    def test_clear_error_state_flag_reset_in_global_vars(self, gv) -> None:
        from protected_dict import protected_dict as _pd

        ds, ns, ast = _make_services(gv, door_state="stopped")
        gv.set_value("clear_error_state", True)
        state = _make_state()

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        assert _pd.instance().get_value("clear_error_state") is False


# ---------------------------------------------------------------------------
# State persistence across iterations
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_close_attempt_count_persists_across_iterations(self, gv) -> None:
        """State dict mutation in _single_iteration must be visible in caller."""
        ds, ns, ast = _make_services(gv, door_state="closed")

        state = _make_state(prev_door_state="closing", close_attempt_count=0)

        obstruction_result = CloseAttemptResult(
            accepted=False,
            elapsed_ms=800.0,
            reference_ms=5000.0,
            attempt_number=1,
            obstruction_detected=True,
        )
        ds.get_elapsed_closing_ms.return_value = 800.0
        ds.validate_close_endstop.return_value = obstruction_result
        ds.get_state.side_effect = [
            "closed", "stopped", "stopped",  # iteration 1: initial, after-resume, final
        ]

        # First iteration → count goes from 0 to 1
        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)
        assert state["close_attempt_count"] == 1

        # Simulate next iteration: door closing again and hitting endstop again
        state["prev_door_state"] = "closing"
        ds.get_elapsed_closing_ms.return_value = 800.0
        ds.validate_close_endstop.return_value = CloseAttemptResult(
            accepted=False,
            elapsed_ms=800.0,
            reference_ms=5000.0,
            attempt_number=2,
            obstruction_detected=True,
        )
        ds.get_state.side_effect = [
            "closed", "stopped", "stopped",  # iteration 2: initial, after-resume, final
        ]

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)
        # count persisted from iteration 1 → now 2
        assert state["close_attempt_count"] == 2


# ---------------------------------------------------------------------------
# prev_door_state tracking
# ---------------------------------------------------------------------------


class TestPrevDoorStateTracking:
    def test_prev_door_state_updated_at_end_of_iteration(self, gv) -> None:
        ds, ns, ast = _make_services(gv, door_state="open")
        state = _make_state(prev_door_state="opening")

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        assert state["prev_door_state"] == "open"

    def test_prev_door_state_transitions_correctly(self, gv) -> None:
        ds, ns, ast = _make_services(gv, door_state="closing")
        # desired = "closed" so door is still moving
        gv.set_value("desired_door_state", "closed")
        state = _make_state(prev_door_state="closed")

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        assert state["prev_door_state"] == "closing"


# ---------------------------------------------------------------------------
# Motor drive logic
# ---------------------------------------------------------------------------


class TestMotorDriveLogic:
    def test_door_stop_called_when_desired_is_stopped(self, gv) -> None:
        ds, ns, ast = _make_services(gv, door_state="opening")
        gv.set_value("desired_door_state", "stopped")
        state = _make_state()

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.stop.assert_called()

    def test_door_open_called_when_desired_is_open(self, gv) -> None:
        ds, ns, ast = _make_services(gv, door_state="closed")
        gv.set_values({"desired_door_state": "open", "reference_door_endstops_ms": 5000.0})
        state = _make_state()

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.open.assert_called()

    def test_door_close_called_when_desired_is_closed(self, gv) -> None:
        ds, ns, ast = _make_services(gv, door_state="open")
        gv.set_values({"desired_door_state": "closed", "reference_door_endstops_ms": 5000.0})
        state = _make_state()

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.close.assert_called()

    def test_error_set_when_move_timeout_exceeded_closing(self, gv) -> None:
        """If door_move_count > endstop_timeout + margin, set_error must be called."""
        ds, ns, ast = _make_services(gv, door_state="open")
        gv.set_values({"desired_door_state": "closed", "reference_door_endstops_ms": 5000.0})
        # 5000 ms → 5 s timeout, + 1 s margin = 6 s. Set count beyond that.
        # Also set last_d_door_state="closed" to prevent the door_move_count reset.
        state = _make_state(door_move_count=7.0, last_d_door_state="closed")

        _single_iteration(door_service=ds, notification_service=ns, astro_service=ast, state=state)

        ds.set_error.assert_called()
