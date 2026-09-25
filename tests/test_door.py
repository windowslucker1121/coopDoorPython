"""Unit tests for :class:`door.DOOR` — the GPIO-level motor/endstop driver.

All tests run against :class:`mock_gpio.MockGPIO`.  Pin numbers are read
from the ``door`` module at call time because ``DOOR.__init__`` rewrites the
module-level pin globals from the ``gpio`` config (the ``clean_state``
fixture restores them after every test).
"""

import pytest

import door as door_module
import mock_gpio
from door import DOOR
from mock_gpio import MockGPIO
from protected_dict import protected_dict as gv

HIGH, LOW = MockGPIO.HIGH, MockGPIO.LOW


def pin(name):
    return getattr(door_module, name)


def set_pin(name, state):
    """Set an input pin level without firing edge callbacks."""
    mock_gpio.globalPins[pin(name)]["state"] = state


def motor():
    """Return (in1, in2, ena) output levels."""
    return tuple(MockGPIO.input(pin(n)) for n in ("in1", "in2", "ena"))


@pytest.fixture
def door(monkeypatch):
    # switch_activated() debounces with the real sleep — make it instant.
    monkeypatch.setattr(door_module, "_hw_sleep", lambda s: None)
    return DOOR()


# ── construction / configuration ─────────────────────────────────────────────

class TestInit:

    def test_initial_state(self, door):
        assert door.get_state() == "stopped"
        assert door.get_override() is False
        assert door.errorState is None
        assert door.reference_door_endstops_ms is None
        assert door.reference_door_active is False
        assert motor() == (LOW, LOW, LOW)

    def test_default_pins_are_configured(self, door):
        pins = mock_gpio.globalPins
        for out_pin in (17, 27, 22):
            assert pins[out_pin]["mode"] == MockGPIO.OUT
        for in_pin in (5, 6, 23, 24):
            assert pins[in_pin]["mode"] == MockGPIO.IN

    def test_edge_callbacks_are_registered(self, door):
        cbs = mock_gpio.callbacks
        assert cbs[5][0] == door.switch_activated
        assert cbs[6][0] == door.switch_activated
        assert cbs[23][0] == door.endstop_hit
        assert cbs[24][0] == door.endstop_hit

    def test_pins_and_flags_come_from_gpio_config(self):
        gv.instance().set_value("gpio", {
            "motor_in1": 1, "motor_in2": 2, "motor_ena": 3,
            "endstop_up": 4, "endstop_down": 7,
            "override_open": 8, "override_close": 9,
            "invert_end_up": True, "invert_end_down": True,
            "reference_timeout": 123,
        })
        DOOR()
        assert (door_module.in1, door_module.in2, door_module.ena) == (1, 2, 3)
        assert (door_module.end_up, door_module.end_down) == (4, 7)
        assert (door_module.o_pin, door_module.c_pin) == (8, 9)
        assert door_module.invert_end_up is True
        assert door_module.invert_end_down is True
        assert door_module.referenceSequenceTimeout == 123
        assert 4 in mock_gpio.callbacks and 9 in mock_gpio.callbacks

    def test_partial_gpio_config_falls_back_to_module_values(self):
        gv.instance().set_value("gpio", {"motor_in1": 12})
        DOOR()
        assert door_module.in1 == 12
        assert door_module.in2 == 27


# ── open / close / stop ──────────────────────────────────────────────────────

class TestMotor:

    def test_open_drives_motor_up(self, door):
        door.open()
        assert door.get_state() == "opening"
        assert motor() == (LOW, LOW, HIGH)
        assert door.startedMovingTime is not None

    def test_close_drives_motor_down(self, door):
        door.close()
        assert door.get_state() == "closing"
        assert motor() == (HIGH, HIGH, HIGH)
        assert door.startedMovingTime is not None

    def test_stop_cuts_all_outputs(self, door):
        door.open()
        door.stop()
        assert door.get_state() == "stopped"
        assert motor() == (LOW, LOW, LOW)

    def test_stop_with_explicit_state(self, door):
        door.stop(state="open")
        assert door.get_state() == "open"
        assert door.lastState == "open"

    def test_open_when_upper_endstop_active_just_stops(self, door):
        set_pin("end_up", HIGH)
        door.open()
        assert door.get_state() == "open"
        assert motor() == (LOW, LOW, LOW)

    def test_close_when_lower_endstop_active_just_stops(self, door):
        set_pin("end_down", HIGH)
        door.close()
        assert door.get_state() == "closed"
        assert motor() == (LOW, LOW, LOW)

    def test_open_with_inverted_upper_endstop(self, door):
        door_module.invert_end_up = True
        # Inverted: LOW means "hit", pin defaults to LOW
        door.open()
        assert door.get_state() == "open"
        set_pin("end_up", HIGH)
        door.open()
        assert door.get_state() == "opening"

    def test_close_with_inverted_lower_endstop(self, door):
        door_module.invert_end_down = True
        door.close()
        assert door.get_state() == "closed"
        set_pin("end_down", HIGH)
        door.close()
        assert door.get_state() == "closing"

    def test_started_moving_time_only_resets_on_state_change(self, door):
        door.open()
        first = door.startedMovingTime
        door.startedMovingTime = first - 5
        door.open()  # still opening → timestamp kept
        assert door.startedMovingTime == first - 5

    def test_open_and_close_are_noops_in_error_state(self, door):
        door.ErrorState("boom")
        state_in_error = door.get_state()
        door.open()
        assert door.get_state() == state_in_error
        door.close()
        assert door.get_state() == state_in_error
        assert motor() == (LOW, LOW, LOW)

    def test_open_then_stop_and_close_then_stop(self, door, monkeypatch):
        monkeypatch.setattr(door_module.time, "sleep", lambda s: None)
        door.open_then_stop()
        assert door.get_state() == "open"
        door.close_then_stop()
        assert door.get_state() == "closed"
        assert motor() == (LOW, LOW, LOW)

    def test_set_auto_mode(self, door):
        door.set_auto_mode(True)
        assert door.auto_mode is True


# ── error state ──────────────────────────────────────────────────────────────

class TestErrorState:

    def test_query_without_error(self, door):
        assert door.ErrorState() is False

    def test_setting_error_stops_door(self, door):
        door.open()
        assert door.ErrorState("jam") is True
        assert door.errorState == "jam"
        assert motor() == (LOW, LOW, LOW)
        # The error text lives in errorState; the door state stays a real state.
        assert door.get_state() == "stopped"

    def test_setting_error_without_stopping(self, door):
        door.open()
        door.ErrorState("jam", stopDoor=False)
        assert door.errorState == "jam"
        assert door.get_state() == "opening"

    def test_same_error_twice_is_idempotent(self, door):
        door.ErrorState("jam")
        door.stop(state="stopped")
        assert door.ErrorState("jam") is True
        assert door.get_state() == "stopped"

    def test_query_with_error_set(self, door):
        door.ErrorState("jam")
        assert door.ErrorState() is True

    def test_clear_error_state(self, door):
        door.ErrorState("jam")
        door.clear_errorState()
        assert door.errorState is None
        assert door.ErrorState() is False


# ── endstop callback ─────────────────────────────────────────────────────────

class TestEndstopHit:

    def test_upper_endstop_edge_stops_as_open(self, door):
        door.open()
        MockGPIO.trigger_event(pin("end_up"), HIGH)
        assert door.get_state() == "open"
        assert motor() == (LOW, LOW, LOW)

    def test_lower_endstop_edge_stops_as_closed(self, door):
        door.close()
        MockGPIO.trigger_event(pin("end_down"), HIGH)
        assert door.get_state() == "closed"

    def test_lower_edge_ignored_while_opening(self, door):
        door.open()
        MockGPIO.trigger_event(pin("end_down"), HIGH)
        assert door.get_state() == "opening"

    def test_upper_edge_ignored_while_closing(self, door):
        door.close()
        MockGPIO.trigger_event(pin("end_up"), HIGH)
        assert door.get_state() == "closing"

    def test_edge_without_active_endstop_changes_nothing(self, door):
        door.open()
        MockGPIO.trigger_event(pin("end_up"), LOW)
        assert door.get_state() == "opening"

    def test_ignored_during_reference(self, door):
        door.reference_door_active = True
        MockGPIO.trigger_event(pin("end_up"), HIGH)
        assert door.get_state() == "stopped"

    def test_ignored_in_error_state(self, door):
        door.ErrorState("jam")
        door.stop(state="stopped")
        MockGPIO.trigger_event(pin("end_up"), HIGH)
        assert door.get_state() == "stopped"

    def test_inverted_upper_endstop(self, door):
        door_module.invert_end_up = True
        set_pin("end_up", HIGH)
        door.open()
        MockGPIO.trigger_event(pin("end_up"), LOW)
        assert door.get_state() == "open"


# ── endstop polling ──────────────────────────────────────────────────────────

class TestCheckEndstops:

    def test_no_endstop_active(self, door):
        assert door.check_endstops() is False

    def test_upper_active_while_opening(self, door):
        door.open()
        set_pin("end_up", HIGH)
        assert door.check_endstops() is True
        assert door.get_state() == "open"

    def test_lower_active_while_closing(self, door):
        door.close()
        set_pin("end_down", HIGH)
        assert door.check_endstops() is True
        assert door.get_state() == "closed"

    def test_upper_ignored_while_closing(self, door):
        door.close()
        set_pin("end_up", HIGH)
        assert door.check_endstops() is False
        assert door.get_state() == "closing"

    def test_lower_ignored_while_opening(self, door):
        door.open()
        set_pin("end_down", HIGH)
        assert door.check_endstops() is False
        assert door.get_state() == "opening"

    def test_already_open_returns_true_without_restop(self, door):
        door.stop(state="open")
        set_pin("end_up", HIGH)
        assert door.check_endstops() is True
        assert door.get_state() == "open"

    def test_disabled_during_reference(self, door):
        set_pin("end_up", HIGH)
        door.reference_door_active = True
        assert door.check_endstops() is False

    def test_disabled_in_error_state(self, door):
        door.ErrorState("jam")
        set_pin("end_up", HIGH)
        assert door.check_endstops() is False


# ── manual switch ────────────────────────────────────────────────────────────

class TestSwitch:

    def test_open_switch_opens_with_override(self, door):
        set_pin("o_pin", HIGH)
        door.switch_activated(pin("o_pin"))
        assert door.get_override() is True
        assert door.get_state() == "opening"

    def test_close_switch_closes_with_override(self, door):
        set_pin("c_pin", HIGH)
        door.switch_activated(pin("c_pin"))
        assert door.get_override() is True
        assert door.get_state() == "closing"

    def test_switch_via_edge_callback(self, door):
        MockGPIO.trigger_event(pin("o_pin"), HIGH)
        assert door.get_state() == "opening"

    def test_both_or_neither_active_does_nothing(self, door):
        door.switch_activated(pin("o_pin"))
        assert door.get_override() is False
        set_pin("o_pin", HIGH)
        set_pin("c_pin", HIGH)
        door.switch_activated(pin("o_pin"))
        assert door.get_override() is False
        assert door.get_state() == "stopped"

    def test_switch_ignored_in_error_state(self, door):
        door.ErrorState("jam")
        set_pin("o_pin", HIGH)
        door.switch_activated(pin("o_pin"))
        assert door.get_override() is False

    def test_switch_ignored_during_reference(self, door):
        door.reference_door_active = True
        set_pin("o_pin", HIGH)
        door.switch_activated(pin("o_pin"))
        assert door.get_override() is False

    def test_neutral_switch_clears_override_and_stops(self, door):
        set_pin("o_pin", HIGH)
        door.switch_activated(pin("o_pin"))
        set_pin("o_pin", LOW)
        door.check_if_switch_neutral()
        assert door.get_override() is False
        assert door.get_state() == "stopped"

    def test_neutral_switch_uses_given_state(self, door):
        door.check_if_switch_neutral(nuetral_state="whatever")
        assert door.get_state() == "whatever"

    @pytest.mark.parametrize("endstop, expected", [("end_up", "open"), ("end_down", "closed")])
    def test_neutral_switch_prefers_endstop_position(self, door, endstop, expected):
        set_pin(endstop, HIGH)
        door.check_if_switch_neutral()
        assert door.get_state() == expected

    def test_switch_still_active_keeps_override(self, door):
        set_pin("o_pin", HIGH)
        door.switch_activated(pin("o_pin"))
        door.check_if_switch_neutral()
        assert door.get_override() is True
        assert door.get_state() == "opening"

    def test_neutral_check_skipped_in_error_or_reference(self, door):
        door.override = True
        door.reference_door_active = True
        door.check_if_switch_neutral()
        assert door.get_override() is True
        door.reference_door_active = False
        door.ErrorState("jam")
        door.check_if_switch_neutral()
        assert door.get_override() is True


# ── reference sequence (synchronous paths) ───────────────────────────────────

class TestReferenceEndstops:

    def test_refused_in_error_state(self, door):
        door.ErrorState("jam")
        assert door.reference_endstops() is False
        assert door.reference_door_active is False

    def test_refused_when_both_endstops_are_active(self, door):
        set_pin("end_up", HIGH)
        set_pin("end_down", HIGH)
        assert door.reference_endstops() is False
        assert door.reference_door_active is False
        assert door.errorState is None

    @pytest.mark.parametrize("start", ["between", "end_down", "end_up"])
    def test_success_with_simulated_travel(self, door, monkeypatch, start):
        """Drive the whole sequence deterministically via a fake clock/sleep.

        Referencing works from anywhere, including the normal resting
        positions at either endstop.
        """
        if start != "between":
            set_pin(start, HIGH)
        clock = {"t": 1000.0, "sleeps": 0}

        def fake_sleep(s):
            clock["t"] += s
            clock["sleeps"] += 1
            # lower endstop after ~1 s of closing, upper ~2 s later
            if door.get_state() == "closing":
                set_pin("end_up", LOW)
                if clock["t"] >= 1001.0:
                    set_pin("end_down", HIGH)
            if door.get_state() == "opening":
                set_pin("end_down", LOW)
                if clock["t"] >= 1003.0:
                    set_pin("end_up", HIGH)

        monkeypatch.setattr(door_module.time, "sleep", fake_sleep)
        monkeypatch.setattr(door_module.time, "time", lambda: clock["t"])

        assert door.reference_endstops() is True
        assert door.get_state() == "open"
        assert door.reference_door_active is False
        # travel time is measured from the lower to the upper endstop
        assert 1500 <= door.reference_door_endstops_ms <= 3250

    def test_timeout_sets_error(self, door, monkeypatch):
        clock = {"t": 0.0}

        def fake_sleep(s):
            clock["t"] += s

        monkeypatch.setattr(door_module.time, "sleep", fake_sleep)
        monkeypatch.setattr(door_module.time, "time", lambda: clock["t"])
        door_module.referenceSequenceTimeout = 2

        assert door.reference_endstops() is False
        assert "lower Endstop not hit" in door.errorState
        assert door.reference_door_active is False
