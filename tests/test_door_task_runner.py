"""Unit tests for :class:`door_task_runner.DoorTaskRunner` branches that the
scenario tests in ``test_integration.py`` do not exercise: mode selection,
manual-override handling, notifications, state commit and the door position
estimate.
"""

from datetime import datetime, timedelta

import pytest
import pytz

import door as door_module
import mock_gpio
from door import DOOR
from door_task_runner import DoorTaskRunner
from mock_gpio import MockGPIO
from protected_dict import protected_dict as gv

TZ = pytz.timezone("America/Denver")
NOON = TZ.localize(datetime(2025, 6, 1, 12, 0))
SUNRISE = TZ.localize(datetime(2025, 6, 1, 6, 0))
SUNSET = TZ.localize(datetime(2025, 6, 1, 20, 0))


def gvals(*keys):
    return gv.instance().get_values(list(keys))


def set_pin(name, state):
    mock_gpio.globalPins[getattr(door_module, name)]["state"] = state


class Harness:
    def __init__(self, *, now=NOON, sunrise=SUNRISE, sunset=SUNSET, **gv_values):
        defaults = {
            "auto_mode": "False",
            "timer_mode": "False",
            "desired_door_state": "stopped",
            "reference_door_endstops_ms": 10_000.0,
            "sunrise_offset": 0,
            "sunset_offset": 0,
            "timer_open_time": "08:00",
            "timer_close_time": "20:00",
        }
        defaults.update(gv_values)
        gv.instance().set_values(defaults)
        self.now = now
        self.sunrise = sunrise
        self.sunset = sunset
        self.notifications = []
        self.door = DOOR()
        self.runner = DoorTaskRunner(
            door=self.door,
            get_sunrise_sunset=lambda: (self.sunrise, self.sunset),
            get_current_time=lambda: self.now,
            send_notification=lambda t, b: self.notifications.append((t, b)),
        )

    def step(self, n=1):
        for _ in range(n):
            result = self.runner.step()
        return result


@pytest.fixture(autouse=True)
def no_hw_sleep(monkeypatch):
    monkeypatch.setattr(door_module, "_hw_sleep", lambda s: None)


# ── debug / clear error flags ────────────────────────────────────────────────

def test_debug_error_flag_sets_test_error_and_resets_flag():
    h = Harness(debug_error=True)
    h.step()
    assert h.door.errorState == "Test Error"
    assert gvals("debug_error") == [False]
    assert gvals("error_state") == ["Test Error"]


def test_clear_error_flag_resets_notification_flags():
    h = Harness()
    h.runner.sentErrorNotification = True
    h.runner.sentOverrideNotification = True
    h.door.ErrorState("jam")
    gv.instance().set_value("clear_error_state", True)
    h.step()
    assert h.door.errorState is None
    assert h.runner.sentErrorNotification is False
    assert gvals("clear_error_state") == [False]


# ── error-state short circuit ────────────────────────────────────────────────

def test_error_state_short_circuit_commits_state_and_notifies_once():
    h = Harness(desired_door_state="open")
    h.door.ErrorState("jam")
    h.runner.door_move_count = 3
    h.runner.auto_close_retry_pending = True

    assert h.step() is True
    h.step()

    assert h.notifications == [("Door Error", "The door is in an error state, please check the door.")]
    assert h.runner.door_move_count == 0
    assert h.runner.auto_close_retry_pending is False
    assert h.runner.first_iter is False
    state, error_state, desired = gvals("state", "error_state", "desired_door_state")
    assert error_state == "jam"
    assert state == h.door.get_state()
    # The desired state is left untouched and the motor is never driven
    assert desired == "open"
    assert MockGPIO.input(door_module.ena) == MockGPIO.LOW


# ── reference sequence toggle ────────────────────────────────────────────────

def test_failed_reference_returns_false_and_clears_toggle():
    h = Harness(toggle_reference_of_endstops=True)
    set_pin("end_up", MockGPIO.HIGH)  # already at an endstop → refused
    assert h.step() is False
    assert gvals("toggle_reference_of_endstops") == [False]


# ── auto mode (sunrise / sunset) ─────────────────────────────────────────────

def test_auto_mode_without_reference_disables_itself():
    h = Harness(auto_mode="True", reference_door_endstops_ms=None)
    h.step()
    assert gvals("auto_mode") == ["False"]


@pytest.mark.parametrize("now, expected", [
    (NOON, "open"),
    (TZ.localize(datetime(2025, 6, 1, 3, 0)), "closed"),
    (TZ.localize(datetime(2025, 6, 1, 22, 0)), "closed"),
])
def test_auto_mode_first_iteration_sets_desired_state(now, expected):
    h = Harness(auto_mode="True", now=now)
    h.step()
    assert gvals("desired_door_state") == [expected]
    # The drive block uses the desired state read at the *start* of the
    # iteration, so the motor only starts on the following step.
    assert h.door.get_state() == "stopped"
    h.step()
    assert h.door.get_state() == ("opening" if expected == "open" else "closing")


def test_auto_mode_offsets_shift_the_open_time():
    # 06:00 sunrise + 30 min offset; at 06:10 the door must still be closed
    h = Harness(auto_mode="True", sunrise_offset=30,
                now=TZ.localize(datetime(2025, 6, 1, 6, 10)))
    h.step()
    assert gvals("desired_door_state") == ["closed"]


def test_auto_mode_sunrise_window_commands_open_after_first_iteration():
    h = Harness(auto_mode="True", now=TZ.localize(datetime(2025, 6, 1, 5, 0)))
    h.step()
    assert gvals("desired_door_state") == ["closed"]
    # Manual change outside any window is respected ...
    gv.instance().set_value("desired_door_state", "stopped")
    h.step()
    assert gvals("desired_door_state") == ["stopped"]
    # ... until the sunrise window is entered
    h.now = SUNRISE + timedelta(seconds=30)
    h.step()
    assert gvals("desired_door_state") == ["open"]


def test_auto_mode_sunset_window_commands_close():
    h = Harness(auto_mode="True")
    h.step()
    set_pin("end_up", MockGPIO.HIGH)
    h.step()
    assert h.door.get_state() == "open"
    h.now = SUNSET + timedelta(seconds=10)
    h.step()
    assert gvals("desired_door_state") == ["closed"]


def test_auto_mode_sets_door_auto_mode_flag():
    h = Harness(auto_mode="True")
    h.step()
    assert h.door.auto_mode is True
    gv.instance().set_value("auto_mode", "False")
    h.step()
    assert h.door.auto_mode is False


# ── timer mode ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour, expected", [(7, "closed"), (12, "open"), (21, "closed")])
def test_timer_mode_first_iteration(hour, expected):
    h = Harness(timer_mode="True", now=TZ.localize(datetime(2025, 6, 1, hour, 0)))
    h.step()
    assert gvals("desired_door_state") == [expected]


def test_timer_mode_invalid_times_fall_back_to_closed():
    # Unparseable times → open_time == close_time == now: the open window is
    # applied first and then overwritten by the close window.
    h = Harness(timer_mode="True", timer_open_time="bogus", timer_close_time="nope")
    h.step()
    assert gvals("desired_door_state") == ["closed"]


def test_auto_mode_takes_precedence_over_timer_mode():
    h = Harness(auto_mode="True", timer_mode="True", reference_door_endstops_ms=None)
    h.step()
    # Only the auto branch runs: it disables auto mode, timer mode stays on
    assert gvals("auto_mode", "timer_mode") == ["False", "True"]


# ── manual override (physical switch) ────────────────────────────────────────

def _activate_open_switch(h):
    set_pin("o_pin", MockGPIO.HIGH)
    h.door.switch_activated(door_module.o_pin)


def test_override_mirrors_door_state_into_desired_state():
    h = Harness(desired_door_state="closed")
    _activate_open_switch(h)
    h.step()
    assert gvals("desired_door_state", "override") == ["opening", True]


def test_override_released_stops_door():
    h = Harness()
    _activate_open_switch(h)
    h.step()
    set_pin("o_pin", MockGPIO.LOW)
    h.step()
    assert h.door.get_override() is False
    assert h.door.get_state() == "stopped"


def test_auto_mode_override_notifies_once_per_window():
    h = Harness(auto_mode="True")
    _activate_open_switch(h)
    h.step(3)  # first iteration counts as a window
    assert [t for t, _ in h.notifications] == ["Manual Override Active"]
    assert "Auto-mode" in h.notifications[0][1]

    # Releasing the switch re-arms the notification.  The override flag is
    # sampled at the start of a step, so it takes one step to clear it
    # (check_if_switch_neutral) and another to re-arm.
    set_pin("o_pin", MockGPIO.LOW)
    h.step()
    assert h.door.get_override() is False
    assert h.runner.sentOverrideNotification is True
    h.step()
    assert h.runner.sentOverrideNotification is False


def test_auto_mode_override_outside_window_does_not_notify():
    h = Harness(auto_mode="True")
    h.step()  # first iteration without override
    _activate_open_switch(h)
    h.step()
    assert h.notifications == []


def test_timer_mode_override_notifies():
    h = Harness(timer_mode="True")
    _activate_open_switch(h)
    h.step()
    assert len(h.notifications) == 1
    assert "Timer-mode" in h.notifications[0][1]


# ── drive block ──────────────────────────────────────────────────────────────

def test_desired_state_change_resets_move_count():
    h = Harness(desired_door_state="open")
    h.step(3)
    assert h.runner.door_move_count == pytest.approx(1.5)
    gv.instance().set_value("desired_door_state", "closed")
    h.step()
    assert h.runner.door_move_count == pytest.approx(0.5)
    assert h.door.get_state() == "closing"


@pytest.mark.parametrize("endstop, state", [("end_up", "open"), ("end_down", "closed")])
def test_desired_stopped_at_endstop_reconciles_desired_state(endstop, state):
    h = Harness(desired_door_state="stopped")
    set_pin(endstop, MockGPIO.HIGH)
    h.step()
    assert h.door.get_state() == state
    assert gvals("desired_door_state") == [state]


def test_desired_stopped_while_moving_stops_motor():
    h = Harness(desired_door_state="open")
    h.step()
    assert h.door.get_state() == "opening"
    gv.instance().set_value("desired_door_state", "stopped")
    h.step()
    assert h.door.get_state() == "stopped"
    assert MockGPIO.input(door_module.ena) == MockGPIO.LOW


def test_without_reference_default_budget_is_used():
    h = Harness(desired_door_state="open", reference_door_endstops_ms=None)
    budget = 10 + DoorTaskRunner.DOOR_MOVE_MAX_AFTER_ENDSTOPS
    h.step(int(budget / h.runner.thread_sleep_time) + 1)
    assert h.door.errorState is None
    h.step()
    assert h.door.errorState == "Endstop not reached"
    assert gvals("desired_door_state") == ["stopped"]


def test_unknown_desired_state_raises_and_sets_error():
    h = Harness(desired_door_state="sideways")
    with pytest.raises(AssertionError):
        h.step()
    assert "unknown state" in h.door.errorState


# ── state commit & position estimate ─────────────────────────────────────────

def test_commit_publishes_state_to_global_vars():
    h = Harness(desired_door_state="open")
    h.step()
    state, override, error_state, sunrise = gvals("state", "override", "error_state", "sunrise")
    assert (state, override, error_state) == ("opening", False, "")
    assert sunrise is None  # only populated by auto mode


def test_commit_publishes_sunrise_sunset_in_auto_mode():
    h = Harness(auto_mode="True")
    h.step()
    assert gvals("sunrise", "sunset") == [SUNRISE, SUNSET]


@pytest.mark.parametrize("endstop, expected", [("end_up", 1.0), ("end_down", 0.0)])
def test_position_estimate_at_endstops(endstop, expected):
    h = Harness()
    set_pin(endstop, MockGPIO.HIGH)
    h.step()
    assert gvals("door_position_estimate") == [expected]


def test_position_estimate_unknown_without_reference():
    h = Harness(reference_door_endstops_ms=None)
    h.step()
    assert gvals("door_position_estimate") == [-1]


def test_position_estimate_integrates_travel(monkeypatch):
    import door_task_runner as dtr
    clock = {"t": 1000.0}
    monkeypatch.setattr(dtr.time, "time", lambda: clock["t"])
    h = Harness(desired_door_state="open", reference_door_endstops_ms=10_000.0)
    h.runner.door_position_estimate = 0.0
    h.step()           # starts opening, dt = 0
    clock["t"] += 2.5  # 25 % of the 10 s travel
    h.step()
    assert gvals("door_position_estimate") == [pytest.approx(0.25)]
    clock["t"] += 100  # clamps at 1.0
    h.step()
    assert gvals("door_position_estimate") == [1.0]


def test_was_door_closing_tracks_state():
    h = Harness(desired_door_state="closed")
    h.step()
    assert h.runner.was_door_closing is True
    gv.instance().set_value("desired_door_state", "stopped")
    h.step()
    assert h.runner.was_door_closing is False
