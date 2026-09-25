"""DoorController: schedules, manual commands, override switch, supervision.

Scenarios ported from the original integration suite (premature close,
retries, error handling, …) plus the regressions fixed in the review.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from conftest import REF_MS, at
from coop.config import Mode
from coop.door.controller import DoorController
from coop.door.model import DesiredState, DoorState

NIGHT = at(23)
NOON = at(12)


def night_auto(rig_factory, **kw):
    """Auto mode at night: the boot re-sync commands the door closed."""
    rig = rig_factory(mode=Mode.AUTO, now=NIGHT, **kw)
    rig.step()
    assert rig.state is DoorState.CLOSING
    return rig


def premature(rig, elapsed: float = 1.0, release: bool = True) -> None:
    """Door (closing) hits the lower endstop after only *elapsed* seconds."""
    if rig.state is not DoorState.CLOSING:
        rig.step()
        assert rig.state is DoorState.CLOSING
    rig.driver.move_started = rig.clock.monotonic() - elapsed
    rig.lower(True)
    if release:
        rig.lower(False, edge=False)
    rig.step()


def expire_retry(rig) -> None:
    rig.clock.advance(DoorController.RETRY_DELAY_S)


# ═══════════════════════════ manual commands & driving ═══════════════════════

class TestManual:

    def test_starts_idle(self, rig):
        rig.step()
        assert rig.state is DoorState.STOPPED
        assert rig.controller.status.desired is DesiredState.STOPPED

    @pytest.mark.parametrize("desired, moving, endstop, final", [
        (DesiredState.OPEN, DoorState.OPENING, "upper", DoorState.OPEN),
        (DesiredState.CLOSED, DoorState.CLOSING, "lower", DoorState.CLOSED),
    ])
    def test_command_moves_until_endstop(self, rig, desired, moving, endstop, final):
        rig.controller.command(desired)
        rig.step()
        assert rig.state is moving
        getattr(rig, endstop)(True)
        rig.step()
        assert rig.state is final
        assert rig.motor == (False, False, False)

    def test_stop_command_stops_moving_door(self, rig):
        rig.controller.command(DesiredState.OPEN)
        rig.step()
        rig.controller.command(DesiredState.STOPPED)
        rig.step()
        assert rig.state is DoorState.STOPPED
        assert rig.motor == (False, False, False)

    @pytest.mark.parametrize("endstop, desired", [("upper", DesiredState.OPEN), ("lower", DesiredState.CLOSED)])
    def test_stopped_at_endstop_reconciles_desired(self, rig, endstop, desired):
        getattr(rig, endstop)(True, edge=False)
        rig.step()
        assert rig.desired is desired

    def test_commands_are_thread_safe(self, rig):
        t = threading.Thread(target=rig.controller.command, args=(DesiredState.OPEN,))
        t.start(); t.join()
        rig.step()
        assert rig.state is DoorState.OPENING

    def test_step_interval_faster_while_moving(self, rig):
        assert rig.controller.step() == DoorController.IDLE_INTERVAL_S
        rig.controller.command(DesiredState.OPEN)
        assert rig.controller.step() == DoorController.MOVING_INTERVAL_S


class TestMoveBudget:
    """Fault if an endstop is not reached within reference + margin (seconds)."""

    def test_budget_uses_wall_time(self, rig):
        rig.controller.command(DesiredState.OPEN)
        budget = REF_MS / 1000 + DoorController.MOVE_MARGIN_S
        rig.run_for(budget)
        assert rig.driver.fault is None
        rig.run_for(1.0)
        assert rig.driver.fault == "Endstop not reached"
        assert rig.desired is DesiredState.STOPPED
        assert rig.motor == (False, False, False)

    def test_budget_without_reference(self, rig_factory):
        rig = rig_factory(reference_ms=None)
        rig.controller.command(DesiredState.CLOSED)
        rig.run_for(DoorController.DEFAULT_TRAVEL_S + DoorController.MOVE_MARGIN_S)
        assert rig.driver.fault is None
        rig.run_for(1.0)
        assert rig.driver.fault == "Endstop not reached"

    def test_budget_restarts_on_new_command(self, rig):
        rig.controller.command(DesiredState.OPEN)
        rig.run_for(25)
        rig.controller.command(DesiredState.CLOSED)
        rig.run_for(25)
        assert rig.driver.fault is None


# ═══════════════════════════ faults ══════════════════════════════════════════

class TestFaults:

    def test_test_error_and_single_notification(self, rig):
        rig.controller.inject_test_error()
        rig.step(3)
        assert rig.driver.fault == "Test Error"
        assert rig.notifications == [("Door Error", "The door is in an error state, please check the door.")]
        assert rig.controller.status.error == "Test Error"
        assert rig.controller.status.state is DoorState.STOPPED

    def test_motor_locked_while_in_fault(self, rig):
        rig.controller.inject_test_error()
        rig.controller.command(DesiredState.OPEN)
        rig.step(3)
        assert rig.motor == (False, False, False)

    def test_clear_error_resets_and_rearms_notification(self, rig):
        rig.controller.inject_test_error()
        rig.step()
        rig.controller.clear_error()
        rig.step()
        assert rig.driver.fault is None
        rig.controller.inject_test_error()
        rig.step()
        assert len(rig.notifications) == 2

    def test_clearing_error_resyncs_schedule(self, rig_factory):
        rig = rig_factory(mode=Mode.AUTO, now=NOON)
        rig.step()
        rig.controller.inject_test_error()
        rig.step()
        rig.controller.clear_error()
        rig.step()
        assert rig.desired is DesiredState.OPEN
        assert rig.state is DoorState.OPENING

    def test_fault_resets_premature_bookkeeping(self, rig_factory):
        rig = night_auto(rig_factory)
        premature(rig)
        rig.controller.inject_test_error()
        rig.step()
        assert rig.controller.premature_count == 0
        assert not rig.controller.retry_pending


# ═══════════════════════════ reference ═══════════════════════════════════════

class TestReference:

    def _simulate(self, rig, close_after=1.0, open_after=3.0):
        start = rig.clock.monotonic()
        orig = rig.clock.sleep

        def sleep(s):
            orig(s)
            t = rig.clock.monotonic() - start
            if rig.state is DoorState.CLOSING and t >= close_after:
                rig.lower(True, edge=False)
            if rig.state is DoorState.OPENING:
                rig.lower(False, edge=False)
                if t >= open_after:
                    rig.upper(True, edge=False)
        rig.clock.sleep = sleep

    def test_success_is_persisted_and_door_stays_open(self, rig_factory):
        rig = rig_factory(reference_ms=None)
        self._simulate(rig)
        rig.controller.request_reference()
        rig.step()
        ref = rig.store.settings.reference_travel_ms
        assert ref == pytest.approx(2000, abs=300)
        assert rig.controller.status.reference_ms == ref
        assert rig.state is DoorState.OPEN
        rig.step(2)
        assert rig.state is DoorState.OPEN  # does not drive back down
        assert rig.desired is DesiredState.OPEN
        # persisted to disk
        from coop.config import ConfigStore
        assert ConfigStore(rig.store.path).load().reference_travel_ms == ref

    def test_timeout_sets_fault(self, rig_factory):
        rig = rig_factory(reference_ms=None)
        rig.store.update(gpio=rig.store.settings.gpio.merged({"reference_timeout": 5}))
        rig.driver.apply_live_settings(rig.store.settings.gpio)
        rig.controller.request_reference()
        rig.step()
        assert "lower Endstop not hit" in rig.driver.fault
        assert rig.store.settings.reference_travel_ms is None

    def test_refused_when_both_endstops_active(self, rig):
        rig.upper(True, edge=False)
        rig.lower(True, edge=False)
        rig.controller.request_reference()
        rig.step()
        assert rig.driver.fault is None
        assert rig.store.settings.reference_travel_ms == REF_MS


# ═══════════════════════════ schedule modes ══════════════════════════════════

class TestSchedule:

    def test_mode_without_reference_switches_to_manual(self, rig_factory):
        rig = rig_factory(mode=Mode.AUTO, reference_ms=None)
        rig.step()
        assert rig.store.settings.mode is Mode.MANUAL
        assert rig.state is DoorState.STOPPED

    @pytest.mark.parametrize("now, desired", [(NOON, DesiredState.OPEN), (NIGHT, DesiredState.CLOSED),
                                              (at(3), DesiredState.CLOSED)])
    def test_boot_sync_auto(self, rig_factory, now, desired):
        rig = rig_factory(mode=Mode.AUTO, now=now)
        rig.step()
        assert rig.desired is desired
        assert rig.state.moving

    def test_boot_at_night_with_open_door_closes_it(self, rig_factory):
        """Regression: the stale desired state let the 'stopped' reconcile
        overwrite the schedule → door stayed open all night."""
        rig = rig_factory(mode=Mode.AUTO, now=NIGHT)
        rig.upper(True, edge=False)
        rig.step()
        rig.upper(False, edge=False)
        rig.step()
        assert rig.desired is DesiredState.CLOSED
        assert rig.state is DoorState.CLOSING

    @pytest.mark.parametrize("hour, desired", [(7, DesiredState.CLOSED), (12, DesiredState.OPEN),
                                               (21, DesiredState.CLOSED)])
    def test_boot_sync_timer(self, rig_factory, hour, desired):
        rig = rig_factory(mode=Mode.TIMER, now=at(hour), timer_open_time="08:00", timer_close_time="20:00")
        rig.step()
        assert rig.desired is desired

    @pytest.mark.parametrize("hour, desired", [(21, DesiredState.OPEN), (3, DesiredState.OPEN),
                                               (12, DesiredState.CLOSED)])
    def test_timer_overnight(self, rig_factory, hour, desired):
        rig = rig_factory(mode=Mode.TIMER, now=at(hour), timer_open_time="20:00", timer_close_time="06:00")
        rig.step()
        assert rig.desired is desired

    def test_enabling_mode_syncs_immediately(self, rig_factory):
        rig = rig_factory(now=NOON)
        rig.lower(True, edge=False)
        rig.step(2)
        assert rig.state is DoorState.CLOSED
        rig.set_mode(Mode.AUTO)
        rig.step()
        assert rig.state is DoorState.OPENING

    def test_switching_auto_to_timer_resyncs(self, rig_factory):
        rig = rig_factory(mode=Mode.AUTO, now=at(7), timer_open_time="08:00")
        rig.step()
        assert rig.desired is DesiredState.OPEN   # after sunrise
        rig.set_mode(Mode.TIMER)
        rig.step()
        assert rig.desired is DesiredState.CLOSED  # timer opens at 08:00

    def test_boundary_crossings(self, rig_factory):
        rig = rig_factory(mode=Mode.TIMER, now=at(7, 59) + timedelta(seconds=50),
                          timer_open_time="08:00", timer_close_time="20:00")
        rig.lower(True, edge=False)  # door closed
        rig.step()
        assert rig.desired is DesiredState.CLOSED
        rig.run_for(15)
        assert rig.desired is DesiredState.OPEN
        assert rig.state is DoorState.OPENING
        rig.lower(False, edge=False)
        rig.upper(True)
        rig.clock.set_now(at(19, 59) + timedelta(seconds=50))
        rig.step()
        assert rig.desired is DesiredState.OPEN
        rig.run_for(15)
        assert rig.desired is DesiredState.CLOSED
        assert rig.state is DoorState.CLOSING

    def test_manual_command_between_boundaries_is_respected(self, rig_factory):
        """Old behaviour re-applied the schedule every 0.5 s during the
        1-minute window; now only the crossing itself changes the target."""
        rig = rig_factory(mode=Mode.TIMER, now=at(7, 59) + timedelta(seconds=55),
                          timer_open_time="08:00", timer_close_time="20:00")
        rig.lower(True, edge=False)
        rig.step()
        rig.run_for(10)
        assert rig.desired is DesiredState.OPEN
        rig.lower(False, edge=False)  # door has left the lower endstop
        rig.controller.command(DesiredState.STOPPED)  # e.g. via API while the mode stays on
        rig.run_for(20)  # still inside the old 1-minute window
        assert rig.desired is DesiredState.STOPPED

    def test_slow_iteration_cannot_skip_a_boundary(self, rig_factory):
        rig = rig_factory(mode=Mode.TIMER, now=at(7, 58), timer_open_time="08:00")
        rig.step()
        rig.clock.set_now(at(8, 3))  # a 5-minute stall (e.g. reference run)
        rig.step()
        assert rig.desired is DesiredState.OPEN

    def test_wall_clock_jump_resyncs(self, rig_factory):
        rig = rig_factory(mode=Mode.TIMER, now=at(12), timer_open_time="08:00", timer_close_time="20:00")
        rig.step()
        rig.controller.command(DesiredState.STOPPED)
        rig.step()
        rig.clock.set_now(at(22))  # time set via UI / NTP catch-up (> 6 h? no: 10 h)
        rig.step()
        assert rig.desired is DesiredState.CLOSED

    def test_invalid_state_of_schedule_inputs_in_status(self, rig_factory):
        rig = rig_factory(mode=Mode.TIMER, now=at(12), timer_open_time="08:00", timer_close_time="20:00")
        rig.step()
        st = rig.controller.status
        assert (st.open_time.hour, st.close_time.hour) == (8, 20)
        assert st.sunrise is not None and st.sunset is not None  # always reported

    def test_polar_night_does_nothing(self, rig_factory):
        from coop.config import LocationConfig
        rig = rig_factory(mode=Mode.AUTO, now=at(12, month=12, day=21))
        rig.store.update(location=LocationConfig("Alert", "CA", "America/Toronto", 82.5, -62.3))
        from coop.services.sun import SunCalculator
        rig.sun = SunCalculator(rig.store.settings.location)
        rig.step(3)
        assert rig.desired is DesiredState.STOPPED
        assert rig.driver.fault is None


# ═══════════════════════════ override switch ═════════════════════════════════

class TestOverride:

    def test_switch_moves_door_and_blocks_commands(self, rig):
        rig.switch("open")
        rig.controller.command(DesiredState.CLOSED)
        rig.step()
        assert rig.state is DoorState.OPENING
        assert rig.controller.status.override is True

    def test_release_keeps_position(self, rig):
        rig.switch("open")
        rig.step()
        rig.switch(None)
        rig.step(3)
        assert rig.state is DoorState.STOPPED
        assert rig.desired is DesiredState.STOPPED
        assert rig.motor == (False, False, False)

    def test_release_at_endstop(self, rig):
        rig.switch("close")
        rig.step()
        rig.lower(True)
        rig.step()
        rig.switch(None)
        rig.step(2)
        assert rig.state is DoorState.CLOSED
        assert rig.desired is DesiredState.CLOSED

    def test_auto_mode_notifies_once_while_blocked(self, rig_factory):
        rig = rig_factory(mode=Mode.AUTO, now=NOON)
        rig.switch("close")
        rig.step(3)
        assert [t for t, _ in rig.notifications] == ["Manual Override Active"]
        assert "Auto-mode" in rig.notifications[0][1]
        rig.switch(None)
        rig.step()
        rig.switch("close")
        rig.clock.set_now(at(23))  # crosses sunset while blocked → notify again
        rig.step()
        assert len(rig.notifications) == 2

    def test_timer_mode_notification_text(self, rig_factory):
        rig = rig_factory(mode=Mode.TIMER, now=NOON)
        rig.switch("open")
        rig.step()
        assert "Timer-mode" in rig.notifications[0][1]

    def test_no_notification_without_schedule_event(self, rig_factory):
        rig = rig_factory(mode=Mode.AUTO, now=NOON)
        rig.step()
        rig.switch("open")
        rig.step(3)
        assert rig.notifications == []


# ═══════════════════════════ premature close ═════════════════════════════════

class TestPrematureClose:

    def test_first_trigger_stops_and_schedules_retry(self, rig_factory):
        rig = night_auto(rig_factory)
        premature(rig)
        assert rig.state is DoorState.STOPPED
        assert rig.desired is DesiredState.STOPPED
        assert rig.controller.premature_count == 1
        assert rig.controller.retry_pending
        assert rig.driver.fault is None

    def test_retry_after_cooldown(self, rig_factory):
        rig = night_auto(rig_factory)
        premature(rig)
        rig.clock.advance(DoorController.RETRY_DELAY_S - 1)
        rig.step()
        assert rig.state is DoorState.STOPPED
        expire_retry(rig)
        rig.step()
        assert rig.desired is DesiredState.CLOSED
        assert rig.state is DoorState.CLOSING
        assert not rig.controller.retry_pending

    def test_max_retries_sets_fault_with_one_notification(self, rig_factory):
        rig = night_auto(rig_factory)
        n = DoorController.PREMATURE_CLOSE_MAX_RETRIES
        for i in range(n - 1):
            premature(rig)
            assert rig.driver.fault is None, f"fault too early at {i + 1}"
            expire_retry(rig)
            rig.step()
        premature(rig)
        assert "prematurely" in rig.driver.fault
        assert rig.desired is DesiredState.STOPPED
        assert rig.controller.premature_count == 0
        assert len(rig.notifications) == 1
        assert rig.notifications[0][0] == "Door Error"
        rig.step(3)
        assert len(rig.notifications) == 1

    def test_full_travel_close_resets_counter(self, rig_factory):
        rig = night_auto(rig_factory)
        premature(rig)
        expire_retry(rig)
        rig.step()
        premature(rig, elapsed=REF_MS / 1000 * 0.9)
        assert rig.controller.premature_count == 0
        assert not rig.controller.retry_pending
        assert rig.controller.cumulative_close_s == 0.0
        assert rig.state is DoorState.CLOSED

    def test_cumulative_drive_time_counts_as_valid_close(self, rig_factory):
        """Door closing in several partial runs (chicken lifting it) is a
        genuine close once the accumulated drive time reaches 80 %."""
        rig = night_auto(rig_factory)
        premature(rig, elapsed=2.0)
        assert rig.controller.cumulative_close_s == pytest.approx(2.0)
        expire_retry(rig); rig.step()
        premature(rig, elapsed=3.0)
        assert rig.controller.premature_count == 2
        assert rig.controller.cumulative_close_s == pytest.approx(5.0)
        expire_retry(rig); rig.step()
        premature(rig, elapsed=4.0)  # 9 s ≥ 8 s
        assert rig.controller.premature_count == 0
        assert rig.notifications == []

    def test_no_detection_in_manual_mode(self, rig):
        rig.controller.command(DesiredState.CLOSED)
        rig.step()
        premature(rig, elapsed=0.5)
        assert rig.state is DoorState.CLOSED
        assert rig.controller.premature_count == 0

    def test_lingering_endstop_then_release(self, rig_factory):
        rig = night_auto(rig_factory)
        premature(rig, release=False)
        for _ in range(3):
            rig.step()
            assert rig.driver.fault is None
        rig.lower(False, edge=False)
        rig.step()
        expire_retry(rig)
        rig.step()
        assert rig.state is DoorState.CLOSING
        assert rig.controller.premature_count == 1

    def test_endstop_still_active_at_retry_counts_again(self, rig_factory):
        rig = night_auto(rig_factory)
        premature(rig, release=False)
        expire_retry(rig)
        rig.step()  # retry fires, endstop still active → closed immediately
        rig.step()  # detected as the next premature close
        assert rig.controller.premature_count == 2 or rig.driver.fault

    def test_schedule_close_boundary_does_not_bypass_cooldown(self, rig_factory):
        rig = rig_factory(mode=Mode.AUTO, now=at(20, 0))
        rig.step()
        # just before sunset → door commanded open; now cross sunset
        rig.clock.set_now(rig.controller.status.close_time - timedelta(seconds=1))
        rig.step()
        rig.clock.advance(2)
        rig.step()
        assert rig.desired is DesiredState.CLOSED
        premature(rig)
        assert rig.controller.retry_pending
        rig.clock.set_now(rig.clock.now() + timedelta(seconds=1))
        for _ in range(4):
            rig.clock.advance(0.5)
            rig.step()
            assert rig.desired is DesiredState.STOPPED
            assert rig.state is DoorState.STOPPED

    def test_disabling_mode_clears_retry(self, rig_factory):
        rig = night_auto(rig_factory)
        premature(rig)
        rig.set_mode(Mode.MANUAL)
        rig.step()
        assert rig.controller.premature_count == 0
        assert not rig.controller.retry_pending

    def test_timer_mode_detection(self, rig_factory):
        rig = rig_factory(mode=Mode.TIMER, now=at(21), timer_close_time="20:00")
        rig.step()
        premature(rig)
        assert rig.controller.premature_count == 1


# ═══════════════════════════ status / position ═══════════════════════════════

class TestStatus:

    def test_position_at_endstops_and_unknown_between(self, rig):
        rig.step()
        assert rig.controller.status.position is None
        rig.upper(True, edge=False)
        rig.step()
        assert rig.controller.status.position == 1.0

    def test_position_integrates_travel(self, rig):
        rig.upper(True, edge=False)
        rig.step(advance=0)
        rig.upper(False, edge=False)
        rig.controller.command(DesiredState.CLOSED)
        rig.step(advance=2.5)  # starts closing
        rig.step(advance=0)    # 2.5 s of a 10 s travel later
        assert rig.controller.status.position == pytest.approx(0.75)

    def test_position_none_without_reference(self, rig_factory):
        rig = rig_factory(reference_ms=None)
        rig.controller.command(DesiredState.OPEN)
        rig.step(2)
        assert rig.controller.status.position is None

    def test_status_is_an_immutable_snapshot(self, rig):
        rig.step()
        st = rig.controller.status
        with pytest.raises(Exception):
            st.state = DoorState.OPEN  # type: ignore[misc]

    def test_notification_failure_does_not_break_loop(self, rig_factory):
        rig = rig_factory()
        rig.controller._notify_cb = lambda t, b: (_ for _ in ()).throw(RuntimeError("push down"))
        rig.controller.inject_test_error()
        rig.step()
        assert rig.driver.fault == "Test Error"
