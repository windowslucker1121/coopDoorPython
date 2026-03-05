"""Integration tests for the coop-door control logic.

Tests exercise :class:`~door_task_runner.DoorTaskRunner` (the extracted loop
body of ``door_task``) together with :class:`~door.DOOR` and
:class:`~mock_gpio.MockGPIO` without starting Flask, Socket.IO, or any real
hardware.

Test scenarios
--------------
Reference sequence
  1. Success path — measures travel time and stores it in global state.
  2. Lower-endstop timeout — runner returns False; error state is set.
  3. Upper-endstop timeout — runner returns False; error state is set.

Premature lower-endstop detection (auto-close safety)
  4. First premature trigger — motor stops, retry scheduled within 5 s.
  5. Retry fires after cooldown — close command re-issued.
  6. Two consecutive premature triggers — counter reaches 2, no error yet.
  7. PREMATURE_CLOSE_MAX_RETRIES consecutive premature triggers — error state + push notification.
  8. Valid (full-travel) close — premature counter reset to 0.
  9. Manual mode — no premature detection even with fast endstop.
 10. Auto mode disabled mid-cycle — retry state fully cleared.
 11. Lingering endstop releases before retry — retry succeeds.
 12. Endstop still active at retry time — stuck-state regression.
 13. door_move_count not reset after retry — motor budget accumulates correctly.
 14. Auto-mode sunset window does not bypass retry cooldown.
 15. Cumulative drive time across retries prevents false error on genuine multi-part close.

Error state management
 11. ``clear_error_state`` flag clears door error and retry counters.
 12. Error-state drive block sends exactly one push notification.

Basic door movement
 13. desired_door_state="open" → door starts opening.
 14. Upper endstop fires → door transitions to "open".
 15. desired_door_state="closed" → door starts closing.
 16. Lower endstop fires → door transitions to "closed".
 17. Move budget exhausted without reaching endstop → error state.
"""

import sys
import os
import time
import threading
from datetime import datetime, timedelta

import pytz
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mock_gpio
from mock_gpio import MockGPIO
from protected_dict import protected_dict as global_vars
from door import DOOR, end_up, end_down
from door_task_runner import DoorTaskRunner

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TZ = pytz.timezone("America/Denver")
#: Reference travel time used by all premature-endstop tests (ms).
REF_MS: float = 10_000.0  # 10 seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(_TZ)


def _make_runner(
    door: DOOR,
    *,
    notifications: list | None = None,
    # Place "current time" before sunrise so auto mode always wants closed.
    sunrise_h: float = 1.0,   # sunrise  N hours from now (future)
    sunset_h: float = 8.0,    # sunset   N hours from now (future)
    now_override: datetime | None = None,
) -> DoorTaskRunner:
    """Create a DoorTaskRunner with deterministic, injected callables.

    Default time positions (sunrise in +1 h, sunset in +8 h) keep the
    runner in "night-before-dawn" so the first-iteration auto-mode logic
    always commands the door *closed* — ideal for premature-endstop tests.
    """
    if notifications is None:
        notifications = []

    now = now_override if now_override is not None else _now()
    sunrise = now + timedelta(hours=sunrise_h)
    sunset  = now + timedelta(hours=sunset_h)

    return DoorTaskRunner(
        door=door,
        get_sunrise_sunset=lambda: (sunrise, sunset),
        get_current_time=lambda: now,
        send_notification=lambda title, body: notifications.append((title, body)),
    )


def _init_gv(
    *,
    auto_mode: str = "False",
    desired_door_state: str = "stopped",
    reference_door_endstops_ms=REF_MS,
    toggle_reference: bool = False,
    clear_error: bool = False,
    debug_error: bool = False,
    sunrise_offset: int = 0,
    sunset_offset: int = 0,
) -> None:
    """Populate global_vars with sensible defaults for a test."""
    global_vars.instance().set_values(
        {
            "auto_mode": auto_mode,
            "desired_door_state": desired_door_state,
            "reference_door_endstops_ms": reference_door_endstops_ms,
            "toggle_reference_of_endstops": toggle_reference,
            "clear_error_state": clear_error,
            "debug_error": debug_error,
            "sunrise_offset": sunrise_offset,
            "sunset_offset": sunset_offset,
        }
    )


# ── GPIO helpers ────────────────────────────────────────────────────────────

def _trigger_lower() -> None:
    """Simulate lower (closed) endstop pressed — fires callback + sets pin HIGH."""
    MockGPIO.trigger_event(end_down, MockGPIO.HIGH)


def _trigger_upper() -> None:
    """Simulate upper (open) endstop pressed — fires callback + sets pin HIGH."""
    MockGPIO.trigger_event(end_up, MockGPIO.HIGH)


def _release_lower() -> None:
    """Deactivate lower endstop (pin back to LOW)."""
    mock_gpio.globalPins[end_down]["state"] = MockGPIO.LOW


def _release_upper() -> None:
    """Deactivate upper endstop (pin back to LOW)."""
    mock_gpio.globalPins[end_up]["state"] = MockGPIO.LOW


# ── Premature-cycle helper ───────────────────────────────────────────────────

def _run_premature_cycle(runner: DoorTaskRunner, door: DOOR) -> None:
    """Execute one complete premature lower-endstop trigger cycle.

    Steps
    -----
    1. If the door is not already *closing*, issue a close command and step
       the runner once so ``door.close()`` is called.
    2. Backdate ``door.startedMovingTime`` to 1 s ago (<<80 % of 10 s ref).
    3. Trigger the lower endstop; the GPIO callback fires and sets
       ``door.state = "closed"``.
    4. Release the lower endstop pin (sets it back to LOW) so that the
       subsequent ``check_if_switch_neutral`` inside ``runner.step()``
       does not see an active endstop and override the "stopped" result.
    5. Call ``runner.step()`` — this is the iteration that detects
       the premature trigger and increments the counter (or trips the error).
    """
    if door.get_state() != "closing":
        global_vars.instance().set_value("desired_door_state", "closed")
        runner.step()
        assert door.get_state() == "closing", (
            f"Expected door to be 'closing' after issuing close command, "
            f"got {door.get_state()!r}"
        )

    # Simulate only 1 second of travel (reference = 10 s → 1 s < 80 %)
    door.startedMovingTime = time.time() - 1.0

    # Trigger endstop — callback fires synchronously in MockGPIO
    _trigger_lower()
    assert door.get_state() == "closed", (
        f"Expected door to be 'closed' after endstop trigger, "
        f"got {door.get_state()!r}"
    )

    # Release pin so check_if_switch_neutral won't fight the "stopped" state
    _release_lower()

    # This step detects the premature trigger
    runner.step()


# ---------------------------------------------------------------------------
# Test: Reference sequence
# ---------------------------------------------------------------------------

class TestReferenceSequence:

    def test_success(self):
        """Full reference: lower then upper endstop → travel time recorded."""
        _init_gv(toggle_reference=True, reference_door_endstops_ms=None)
        door = DOOR()
        runner = _make_runner(door)

        result: dict = {}

        def _run() -> None:
            result["returned"] = runner.step()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Give runner time to enter the close-polling loop
        time.sleep(0.25)
        _trigger_lower()    # door reaches closed position

        time.sleep(0.25)
        _trigger_upper()    # door reaches open position

        t.join(timeout=5)
        assert not t.is_alive(), "step() blocked for > 5 s"

        assert result["returned"] is True

        # Travel time stored on DOOR and forwarded to global_vars
        assert door.reference_door_endstops_ms is not None
        assert door.reference_door_endstops_ms > 0
        assert global_vars.instance().get_value("reference_door_endstops_ms") == pytest.approx(
            door.reference_door_endstops_ms
        )

        assert door.get_state() == "open"
        # was_door_closing reset so next step can't misfire premature check
        assert runner.was_door_closing is False

        # toggle flag cleared even on success
        assert global_vars.instance().get_value("toggle_reference_of_endstops") is False

    def test_lower_endstop_timeout_sets_error(self):
        """Reference aborts with error when lower endstop is never reached."""
        import door as door_mod
        saved = door_mod.referenceSequenceTimeout
        door_mod.referenceSequenceTimeout = 0.3   # force a quick timeout

        try:
            _init_gv(toggle_reference=True, reference_door_endstops_ms=None)
            door = DOOR()
            runner = _make_runner(door)
            returned = runner.step()   # blocks ~0.3 s then returns
        finally:
            door_mod.referenceSequenceTimeout = saved

        assert returned is False
        assert door.ErrorState() is True
        assert "timed out" in (door.errorState or "").lower()
        assert global_vars.instance().get_value("toggle_reference_of_endstops") is False

    def test_upper_endstop_timeout_sets_error(self):
        """Reference aborts with error when upper endstop is never reached."""
        import door as door_mod
        saved = door_mod.referenceSequenceTimeout
        door_mod.referenceSequenceTimeout = 0.5

        try:
            _init_gv(toggle_reference=True, reference_door_endstops_ms=None)
            door = DOOR()
            runner = _make_runner(door)

            result: dict = {}

            def _run() -> None:
                result["returned"] = runner.step()

            t = threading.Thread(target=_run, daemon=True)
            t.start()

            # Quickly satisfy the lower phase so the upper phase can time out
            time.sleep(0.1)
            _trigger_lower()

            t.join(timeout=4)
            assert not t.is_alive()
        finally:
            door_mod.referenceSequenceTimeout = saved

        assert result["returned"] is False
        assert door.ErrorState() is True
        assert "timed out" in (door.errorState or "").lower()


# ---------------------------------------------------------------------------
# Test: Premature lower-endstop detection
# ---------------------------------------------------------------------------

class TestPrematureEndstop:

    def _setup(self) -> tuple[DOOR, DoorTaskRunner, list]:
        """Create door + runner configured for auto-close premature tests."""
        _init_gv(
            auto_mode="True",
            desired_door_state="closed",
            reference_door_endstops_ms=REF_MS,
        )
        door = DOOR()
        notifications: list = []
        runner = _make_runner(door, notifications=notifications)
        return door, runner, notifications

    # ── single trigger ───────────────────────────────────────────────────────

    def test_first_trigger_stops_motor_and_schedules_retry(self):
        """First premature trigger: motor stops, 5-second retry is scheduled."""
        door, runner, _ = self._setup()

        _run_premature_cycle(runner, door)

        assert door.get_state() == "stopped"
        assert global_vars.instance().get_value("desired_door_state") == "stopped"
        assert runner.auto_close_premature_count == 1
        assert runner.auto_close_retry_pending is True
        assert runner.auto_close_retry_time is not None
        assert runner.auto_close_retry_time > time.time()
        assert door.ErrorState() is False

    def test_retry_fires_after_cooldown_reissues_close(self):
        """After cooldown expires the runner re-issues the close command."""
        door, runner, _ = self._setup()

        _run_premature_cycle(runner, door)
        assert runner.auto_close_retry_pending is True

        # Artificially expire the cooldown
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()

        assert global_vars.instance().get_value("desired_door_state") == "closed"
        assert runner.auto_close_retry_pending is False
        assert runner.auto_close_retry_time is None
        # Door should now be closing again (retry triggered door.close())
        assert door.get_state() == "closing"

    # ── two triggers ─────────────────────────────────────────────────────────

    def test_second_trigger_increments_counter_no_error(self):
        """Two consecutive premature triggers: counter = 2, still no error."""
        door, runner, _ = self._setup()

        _run_premature_cycle(runner, door)
        assert runner.auto_close_premature_count == 1

        # Expire cooldown → door starts closing again
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()
        assert global_vars.instance().get_value("desired_door_state") == "closed"

        # Second premature trigger
        _run_premature_cycle(runner, door)

        assert runner.auto_close_premature_count == 2
        assert door.ErrorState() is False

    # ── three triggers → error ───────────────────────────────────────────────

    def test_three_triggers_enter_error_state_with_notification(self):
        """Consecutive premature triggers → error state + one notification.

        The error fires after ``PREMATURE_CLOSE_MAX_RETRIES`` consecutive
        triggers (currently 5).  The test uses the runner's own constant so
        it stays valid if the threshold is tuned in the future.
        """
        door, runner, notifications = self._setup()
        max_retries = runner.PREMATURE_CLOSE_MAX_RETRIES

        for _ in range(max_retries - 1):
            _run_premature_cycle(runner, door)
            assert door.ErrorState() is False, (
                f"Error state set too early at count "
                f"{runner.auto_close_premature_count}"
            )
            # Expire cooldown so the door can start closing again
            runner.auto_close_retry_time = time.time() - 0.1
            runner.step()
            assert global_vars.instance().get_value("desired_door_state") == "closed"

        # Fatal (max_retries-th) trigger
        _run_premature_cycle(runner, door)

        assert door.ErrorState() is True
        assert "prematurely" in (door.errorState or "").lower()
        assert global_vars.instance().get_value("desired_door_state") == "stopped"
        # Counter resets to 0 so repeat attempts after a clear start fresh
        assert runner.auto_close_premature_count == 0
        assert runner.auto_close_retry_pending is False
        # Exactly one push notification (sent inside the premature block)
        assert len(notifications) == 1
        title, body = notifications[0]
        assert "Door Error" in title
        assert "prematurely" in body.lower()

    # ── valid close resets counter ────────────────────────────────────────────

    def test_valid_close_resets_premature_counter(self):
        """A close that travels ≥ 80 % of reference time resets the counter."""
        door, runner, _ = self._setup()

        # Record one premature trigger to raise the counter
        _run_premature_cycle(runner, door)
        assert runner.auto_close_premature_count == 1

        # Expire cooldown → door starts closing in this step
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()
        assert door.get_state() == "closing"

        # Simulate full-travel close: elapsed ≈ 90 % of reference (≥ 80 %)
        door.startedMovingTime = time.time() - (REF_MS / 1000 * 0.9)
        _trigger_lower()
        assert door.get_state() == "closed"
        _release_lower()

        runner.step()  # timing check iteration

        assert runner.auto_close_premature_count == 0
        assert runner.auto_close_retry_pending is False
        assert runner.auto_close_cumulative_drive_s == 0.0
        assert door.ErrorState() is False

    # ── manual mode ──────────────────────────────────────────────────────────

    def test_no_premature_detection_in_manual_mode(self):
        """With auto_mode=False premature detection must not fire."""
        _init_gv(
            auto_mode="False",
            desired_door_state="closed",
            reference_door_endstops_ms=REF_MS,
        )
        door = DOOR()
        runner = _make_runner(door)

        # Start closing
        runner.step()
        assert door.get_state() == "closing"

        # Simulate a fast (premature-looking) endstop hit
        door.startedMovingTime = time.time() - 0.5  # only 0.5 s elapsed
        _trigger_lower()
        assert door.get_state() == "closed"

        runner.step()

        # No premature action in manual mode
        assert runner.auto_close_premature_count == 0
        assert runner.auto_close_retry_pending is False

    # ── lingering endstop (from real-world log 2026-03-03) ───────────────────

    def test_retry_fires_after_endstop_stays_active_then_releases(self):
        """Real-world log scenario: endstop callback fires after premature stop,
        polling keeps seeing it for several iterations, then it releases.
        The retry must still re-issue the close and start the door moving.

        In the real log the endstop released ~4 s before the 5-s retry fired,
        so the retry should always succeed once the pin goes low.
        """
        door, runner, _ = self._setup()

        # ── phase 1: premature trigger ────────────────────────────────────────
        global_vars.instance().set_value("desired_door_state", "closed")
        runner.step()
        assert door.get_state() == "closing"

        door.startedMovingTime = time.time() - 1.0   # only 1 s elapsed
        _trigger_lower()                               # callback sets "closed"
        assert door.get_state() == "closed"
        # Do NOT release yet — endstop still physically active

        runner.step()   # detects premature trigger
        assert runner.auto_close_premature_count == 1
        assert runner.auto_close_retry_pending is True

        # ── phase 2: several iterations with endstop still active ─────────────
        # (mirrors the repeated "stop because door is in moving state" log lines)
        for _ in range(3):
            runner.step()
            assert door.ErrorState() is False

        # ── phase 3: chicken moves away, endstop releases ─────────────────────
        _release_lower()
        runner.step()   # one iteration with inactive endstop

        # ── phase 4: retry fires ──────────────────────────────────────────────
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()

        assert global_vars.instance().get_value("desired_door_state") == "closed"
        assert runner.auto_close_retry_pending is False
        assert door.get_state() == "closing", (
            f"Expected door to be closing after retry, got {door.get_state()!r}"
        )
        assert door.ErrorState() is False
        assert runner.auto_close_premature_count == 1   # unchanged — still on attempt 1

    def test_retry_stuck_when_endstop_still_active_at_retry_time(self):
        """Bug scenario: endstop remains active through the entire cooldown.

        Without the fix, when the retry fires ``check_endstops()`` has already
        set ``door_state = d_door_state = "closed"`` so the drive block's
        ``elif door_state != d_door_state`` is False, ``door.close()`` is never
        called, and the door is permanently stuck.

        With the fix (``_close_retry_just_fired`` flag + fresh ``startedMovingTime``),
        the drive block is forced to run, and if the endstop is still active the
        next iteration's premature detection fires again, incrementing the counter
        toward the error-state threshold.
        """
        door, runner, notifications = self._setup()

        # ── premature trigger ────────────────────────────────────────────────
        global_vars.instance().set_value("desired_door_state", "closed")
        runner.step()
        assert door.get_state() == "closing"

        door.startedMovingTime = time.time() - 1.0
        _trigger_lower()
        assert door.get_state() == "closed"
        # Leave endstop active — chicken is still in the doorway

        runner.step()   # detects premature trigger
        assert runner.auto_close_premature_count == 1
        assert runner.auto_close_retry_pending is True

        # ── retry fires while endstop is STILL active ──────────────────────
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()   # retry fires; drive block runs; endstop still HIGH →
                        # door.close() returns immediately, sets "closed" without
                        # running the motor; _close_retry_just_fired stays True
        runner.step()   # _close_retry_just_fired flag is seen by premature check
                        # → count incremented to 2, new retry scheduled

        # After the retry the door must NOT be stuck in a silent "closed"
        # limbo.  The system must either:
        #   (a) have a SECOND retry pending (premature count incremented), or
        #   (b) have entered error state (if this pushed the count to ≥ 3).
        assert runner.auto_close_premature_count >= 2 or door.ErrorState() is True, (
            f"Door stuck: state={door.get_state()!r}, "
            f"desired={global_vars.instance().get_value('desired_door_state')!r}, "
            f"premature_count={runner.auto_close_premature_count}, "
            f"retry_pending={runner.auto_close_retry_pending}, "
            f"error={door.ErrorState()}"
        )

        # Release endstop so subsequent test teardown is clean
        _release_lower()

    # ── move-count not reset after retry ─────────────────────────────────────

    def test_door_move_count_not_reset_after_retry(self):
        """Regression: door_move_count must accumulate across iterations after a
        retry fires, not reset to 0 each time.

        Root cause: when the retry fired, only the local ``d_door_state`` was
        updated to "closed" but ``last_d_door_state`` was left as "stopped".
        In the next iteration the differ check reset ``door_move_count`` to 0,
        meaning the motor budget timeout never advanced and a stuck door would
        never trigger an error.

        Fix: the retry block now also sets ``self.last_d_door_state = "closed"``.
        """
        door, runner, _ = self._setup()

        # ── trigger one premature close ───────────────────────────────────────
        _run_premature_cycle(runner, door)
        assert runner.auto_close_premature_count == 1
        assert runner.auto_close_retry_pending is True

        # ── expire cooldown so retry fires on the next step ──────────────────
        runner.auto_close_retry_time = time.time() - 0.1

        # ── step 1: retry fires, door.close() called, door_move_count = 0.5 ──
        runner.step()
        assert global_vars.instance().get_value("desired_door_state") == "closed"
        assert door.get_state() == "closing"
        count_after_retry = runner.door_move_count
        assert count_after_retry == pytest.approx(0.5), (
            f"Expected door_move_count == 0.5 after retry step, got {count_after_retry}"
        )

        # ── step 2 (BUG WOULD RESET count to 0 here) ─────────────────────────
        runner.step()
        count_after_second = runner.door_move_count
        assert count_after_second == pytest.approx(1.0), (
            f"door_move_count was reset instead of accumulating: "
            f"expected 1.0, got {count_after_second}"
        )

    # ── cumulative drive time prevents false error after genuine multi-part close ─

    def test_cumulative_drive_time_prevents_false_error_after_genuine_close(self):
        """Real-world regression: door closes in multiple partial runs whose
        total accumulated drive time meets the reference threshold — should be
        treated as a VALID close, not as repeated premature triggers.

        Scenario from log 2026-03-05 (ref ≈ 17.6 s, threshold 80% ≈ 14.1 s):
          attempt 1: 5.5 s elapsed (chicken lifts door briefly)      cumulative=5.5
          attempt 2: 3.5 s elapsed (chicken again)                   cumulative=9.1
          attempt 3: 8.1 s elapsed (door reaches ground, total=17.2) → VALID CLOSE

        Without the fix each attempt is judged by its own elapsed time; after a
        valid close the endstop remains active and two more 0.5-s "attempts"
        (elapsed each measured from the freshly-reset startedMovingTime) push the
        premature counter to 5, triggering an error for a door that is actually
        fully closed.

        With the fix (``auto_close_cumulative_drive_s``):
          attempt 1: 2 s   cumulative=2 s < threshold(8 s)  → premature (count=1)
          attempt 2: 3 s   cumulative=5 s < threshold       → premature (count=2)
          attempt 3: 4 s   total=5+4=9 s ≥ threshold        → VALID, reset counter
        """
        door, runner, notifications = self._setup()
        ref_s = REF_MS / 1000.0          # 10 s
        threshold_s = ref_s * runner.PREMATURE_CLOSE_THRESHOLD  # 8 s

        # ── attempt 1: 2 s elapsed → premature ──────────────────────────────
        global_vars.instance().set_value("desired_door_state", "closed")
        runner.step()
        assert door.get_state() == "closing"
        door.startedMovingTime = time.time() - 2.0
        _trigger_lower()
        _release_lower()
        runner.step()
        assert runner.auto_close_premature_count == 1
        assert runner.auto_close_cumulative_drive_s == pytest.approx(2.0, abs=0.1)

        # ── attempt 2: 3 s elapsed → still premature ─────────────────────────
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()   # retry fires → door starts closing
        assert door.get_state() == "closing"
        door.startedMovingTime = time.time() - 3.0
        _trigger_lower()
        _release_lower()
        runner.step()
        assert runner.auto_close_premature_count == 2
        assert runner.auto_close_cumulative_drive_s == pytest.approx(5.0, abs=0.1)

        # ── attempt 3: 4 s elapsed → cumulative = 9 s ≥ 8 s → VALID CLOSE ────
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()   # retry fires → door starts closing
        assert door.get_state() == "closing"
        door.startedMovingTime = time.time() - 4.0
        _trigger_lower()
        _release_lower()
        runner.step()   # premature check → total=9 s ≥ 8 s → valid

        assert runner.auto_close_premature_count == 0, (
            f"Expected count reset to 0 on valid cumulative close, "
            f"got {runner.auto_close_premature_count}"
        )
        assert runner.auto_close_cumulative_drive_s == 0.0, (
            "Cumulative drive time must reset after a valid close"
        )
        assert runner.auto_close_retry_pending is False
        assert door.ErrorState() is False
        assert len(notifications) == 0, "No error notification should be sent"

    # ── auto-mode sunset window must not override retry cooldown ─────────────

    def test_auto_mode_sunset_window_does_not_bypass_retry_cooldown(self):
        """Real-world regression from 2026-03-05 log.

        When a premature endstop fires WHILE the auto-mode sunset 1-minute close
        window is active, the auto-mode block runs every 0.5 s and was writing
        'desired_door_state = closed' back to global_vars, immediately overriding
        the 'stopped' state set by premature detection.  On the next iteration
        d_door_state changed from 'stopped' back to 'closed', door_move_count was
        reset to 0, and the close was re-issued without waiting for the cooldown.

        Fix: the sunset window write is now guarded with
        ``not self.auto_close_retry_pending``.

        This test places current_time inside the sunset 1-minute window
        (sunset was 3 seconds ago) and verifies that:
        1. After a premature trigger the desired state stays 'stopped'.
        2. door_move_count does NOT reset to 0 while the cooldown is active.
        3. The door does NOT start closing again before the retry fires.
        """
        _init_gv(
            auto_mode="True",
            desired_door_state="closed",
            reference_door_endstops_ms=REF_MS,
        )
        door = DOOR()
        notifications: list = []

        # Place current_time 3 seconds after sunset → inside the 1-minute window.
        # sunset_h = -3/3600 means sunset was 3 seconds ago.
        runner = _make_runner(
            door,
            notifications=notifications,
            sunset_h=-3 / 3600,   # sunset 3 s in the past (inside 1-min window)
            sunrise_h=12.0,        # sunrise well in the future
        )

        # Start closing (first_iter fires the boot-time close command)
        runner.step()
        assert door.get_state() == "closing"

        # Simulate a premature endstop: only 1 s of travel (ref = 10 s)
        door.startedMovingTime = time.time() - 1.0
        _trigger_lower()
        assert door.get_state() == "closed"
        _release_lower()

        # This step detects the premature trigger
        runner.step()
        assert runner.auto_close_premature_count == 1
        assert runner.auto_close_retry_pending is True
        assert global_vars.instance().get_value("desired_door_state") == "stopped"

        # ── Several steps WHILE cooldown is active and sunset window is still on ──
        # BUG: auto-mode block would write "closed" back, overriding "stopped".
        for _ in range(4):
            runner.step()
            assert global_vars.instance().get_value("desired_door_state") == "stopped", (
                "Auto-mode sunset window must not override premature-retry cooldown"
            )
            assert door.get_state() == "stopped", (
                f"Door must stay stopped during cooldown, got {door.get_state()}"
            )

        # door_move_count should still be 0 (door hasn't been commanded to close)
        assert runner.door_move_count == 0, (
            f"door_move_count should be 0 while cooldown is active, "
            f"got {runner.door_move_count}"
        )

        # ── Cooldown expires → retry must fire and door must start closing ──
        runner.auto_close_retry_time = time.time() - 0.1
        runner.step()
        assert global_vars.instance().get_value("desired_door_state") == "closed"
        assert door.get_state() == "closing"

    # ── disable auto mid-cycle resets retry state ─────────────────────────────

    def test_disabling_auto_mode_clears_retry_state(self):
        """Disabling auto mode after a premature trigger clears all retry state."""
        door, runner, _ = self._setup()

        _run_premature_cycle(runner, door)
        assert runner.auto_close_premature_count == 1

        # Operator disables auto mode from the web UI
        global_vars.instance().set_value("auto_mode", "False")
        runner.step()

        assert runner.auto_close_premature_count == 0
        assert runner.auto_close_retry_pending is False
        assert runner.auto_close_retry_time is None


# ---------------------------------------------------------------------------
# Test: Error state management
# ---------------------------------------------------------------------------

class TestErrorStateManagement:

    def test_clear_error_flag_resets_door_and_retry_state(self):
        """Setting clear_error_state=True clears door error and retry counters."""
        _init_gv(auto_mode="False", desired_door_state="stopped")
        door = DOOR()
        runner = _make_runner(door)

        # Inject error directly
        door.ErrorState("Injected test error", stopDoor=False)
        assert door.ErrorState() is True

        # Simulate leftover retry state from a premature cycle
        runner.auto_close_premature_count = 2
        runner.auto_close_retry_pending = True
        runner.auto_close_retry_time = time.time() + 999
        runner.sentErrorNotification = True

        # Tell the runner to clear the error
        global_vars.instance().set_value("clear_error_state", True)
        runner.step()

        assert door.ErrorState() is False
        assert runner.auto_close_premature_count == 0
        assert runner.auto_close_retry_pending is False
        assert runner.auto_close_retry_time is None
        assert runner.sentErrorNotification is False
        # Flag consumed
        assert global_vars.instance().get_value("clear_error_state") is False

    def test_error_state_drive_sends_notification_exactly_once(self):
        """Error during motor drive emits one push notification even over N steps."""
        _init_gv(auto_mode="False", desired_door_state="open")
        door = DOOR()
        notifications: list = []
        runner = _make_runner(door, notifications=notifications)

        # Inject error before any steps
        door.ErrorState("Stuck", stopDoor=False)

        for _ in range(4):
            runner.step()

        assert len(notifications) == 1


# ---------------------------------------------------------------------------
# Test: Basic door movement
# ---------------------------------------------------------------------------

class TestBasicDoorMovement:

    def test_desired_open_starts_opening(self):
        """desired_door_state='open' → step calls door.open() → state 'opening'."""
        _init_gv(auto_mode="False", desired_door_state="open")
        door = DOOR()
        runner = _make_runner(door)

        runner.step()
        assert door.get_state() == "opening"

    def test_upper_endstop_transitions_to_open(self):
        """Door transitions opening → open when upper endstop fires."""
        _init_gv(auto_mode="False", desired_door_state="open")
        door = DOOR()
        runner = _make_runner(door)

        runner.step()
        assert door.get_state() == "opening"

        _trigger_upper()
        assert door.get_state() == "open"

        runner.step()
        assert door.get_state() == "open"

    def test_desired_closed_starts_closing(self):
        """desired_door_state='closed' → step calls door.close() → state 'closing'."""
        _init_gv(auto_mode="False", desired_door_state="closed")
        door = DOOR()
        runner = _make_runner(door)

        runner.step()
        assert door.get_state() == "closing"

    def test_lower_endstop_transitions_to_closed(self):
        """Door transitions closing → closed when lower endstop fires."""
        _init_gv(auto_mode="False", desired_door_state="closed")
        door = DOOR()
        runner = _make_runner(door)

        runner.step()
        assert door.get_state() == "closing"

        _trigger_lower()
        assert door.get_state() == "closed"

        runner.step()
        assert door.get_state() == "closed"

    def test_move_budget_exhausted_sets_error(self):
        """Error state set if endstop not reached within reference + margin time.

        The motor timeout reads ``door.reference_door_endstops_ms`` (the DOOR
        object's own attribute set by the reference sequence) — not the value
        stored in global_vars.  Both must be populated for the timeout logic to
        use the desired small budget.

        Budget = 0.5 s reference + 1 s margin = 1.5 s.
        thread_sleep_time = 0.5 s  →  error fires on step 5
        (count sequence: 0 → 0.5 → 1.0 → 1.5 → 2.0 > 1.5 → error).
        """
        _init_gv(
            auto_mode="False",
            desired_door_state="open",
            reference_door_endstops_ms=500,
        )
        door = DOOR()
        # The motor timeout path uses door.reference_door_endstops_ms directly,
        # so set it on the DOOR object as the reference sequence would.
        door.reference_door_endstops_ms = 500
        runner = _make_runner(door)

        for _ in range(5):
            runner.step()

        assert door.ErrorState() is True
