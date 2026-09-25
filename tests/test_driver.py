"""DoorDriver: GPIO motor control, endstops, override switch, reference run."""

from __future__ import annotations

from dataclasses import replace

import pytest

from coop.clock import FakeClock
from coop.config import GpioConfig
from coop.door.driver import DoorDriver
from coop.door.model import DoorState
from coop.hardware.gpio import MockGpio

PINS = GpioConfig()


@pytest.fixture
def gpio():
    return MockGpio()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def drv(gpio, clock):
    return DoorDriver(gpio, PINS, clock)


def motor(gpio, pins=PINS):
    return tuple(gpio.read(getattr(pins, n)) for n in ("motor_in1", "motor_in2", "motor_ena"))


class TestSetup:

    def test_initial_state(self, drv, gpio):
        assert drv.state is DoorState.STOPPED
        assert drv.override is False and drv.fault is None
        assert motor(gpio) == (False, False, False)

    def test_pin_modes(self, drv, gpio):
        for p in (17, 27, 22):
            assert gpio.mode(p) == "OUT"
        for p in (5, 6, 23, 24):
            assert gpio.mode(p) == "IN"

    def test_custom_pins(self, gpio, clock):
        pins = replace(PINS, motor_in1=2, motor_in2=3, motor_ena=4, endstop_up=7, endstop_down=8)
        d = DoorDriver(gpio, pins, clock)
        d.open()
        assert (gpio.read(2), gpio.read(3), gpio.read(4)) == (False, False, True)
        gpio.trigger(7, True)
        d.sync()
        assert d.state is DoorState.OPEN


class TestMotor:

    def test_open(self, drv, gpio, clock):
        drv.open()
        assert drv.state is DoorState.OPENING
        assert motor(gpio) == (False, False, True)
        assert drv.move_started == clock.monotonic()

    def test_close(self, drv, gpio):
        drv.close()
        assert drv.state is DoorState.CLOSING
        assert motor(gpio) == (True, True, True)

    def test_stop(self, drv, gpio):
        drv.open()
        drv.stop()
        assert drv.state is DoorState.STOPPED
        assert motor(gpio) == (False, False, False)

    def test_move_start_only_on_direction_change(self, drv, clock):
        drv.open()
        started = drv.move_started
        clock.advance(3)
        drv.open()
        assert drv.move_started == started
        drv.close()
        assert drv.move_started == clock.monotonic()

    @pytest.mark.parametrize("method, pin, state", [("open", 23, DoorState.OPEN), ("close", 24, DoorState.CLOSED)])
    def test_at_destination_endstop_just_stops(self, drv, gpio, method, pin, state):
        gpio.set_input(pin, True)
        getattr(drv, method)()
        assert drv.state is state
        assert motor(gpio) == (False, False, False)

    def test_inverted_endstops(self, gpio, clock):
        d = DoorDriver(gpio, replace(PINS, invert_end_up=True, invert_end_down=True), clock)
        d.open()  # LOW = active when inverted
        assert d.state is DoorState.OPEN
        gpio.set_input(23, True)
        gpio.set_input(24, True)
        d.open()
        assert d.state is DoorState.OPENING

    def test_live_invert_update(self, drv, gpio):
        drv.apply_live_settings(replace(PINS, invert_end_up=True, motor_in1=99, reference_timeout=77))
        assert drv.upper_active() is True
        assert drv.pins.motor_in1 == 17  # pin numbers need a restart
        assert drv.pins.reference_timeout == 77


class TestFault:

    def test_fault_stops_and_locks(self, drv, gpio):
        drv.open()
        drv.set_fault("jam")
        assert drv.state is DoorState.STOPPED
        assert drv.fault == "jam"
        drv.open(); drv.close()
        assert motor(gpio) == (False, False, False)

    def test_clear(self, drv):
        drv.set_fault("jam")
        drv.clear_fault()
        drv.open()
        assert drv.state is DoorState.OPENING


class TestEndstops:

    def test_edge_cuts_motor_immediately(self, drv, gpio):
        drv.open()
        gpio.trigger(23, True)
        assert motor(gpio) == (False, False, False)  # in the callback itself
        assert drv.state is DoorState.OPENING           # state applied by sync()
        drv.sync()
        assert drv.state is DoorState.OPEN

    def test_edge_of_other_endstop_ignored(self, drv, gpio):
        drv.open()
        gpio.trigger(24, True)  # door leaving the lower endstop
        assert motor(gpio) == (False, False, True)

    def test_edge_ignored_in_fault_and_reference(self, drv, gpio):
        drv.close()
        drv.reference_active = True
        gpio.trigger(24, True)
        assert motor(gpio) == (True, True, True)

    def test_poll_catches_missed_edge(self, drv, gpio):
        drv.close()
        gpio.set_input(24, True)
        drv.sync()
        assert drv.state is DoorState.CLOSED

    def test_poll_ignores_leaving_endstop(self, drv, gpio):
        gpio.set_input(24, True)
        drv.open()
        drv.sync()
        assert drv.state is DoorState.OPENING

    @pytest.mark.parametrize("pin, state", [(23, DoorState.OPEN), (24, DoorState.CLOSED)])
    def test_poll_updates_rest_state(self, drv, gpio, pin, state):
        gpio.set_input(pin, True)
        drv.sync()
        assert drv.state is state

    def test_sync_does_nothing_in_fault(self, drv, gpio):
        drv.set_fault("jam")
        gpio.set_input(23, True)
        drv.sync()
        assert drv.state is DoorState.STOPPED


class TestSwitch:

    def test_open_switch(self, drv, gpio):
        gpio.set_input(5, True)
        drv.sync()
        assert drv.override is True
        assert drv.state is DoorState.OPENING

    def test_close_switch(self, drv, gpio):
        gpio.set_input(6, True)
        drv.sync()
        assert drv.state is DoorState.CLOSING

    def test_both_inputs_means_neutral(self, drv, gpio):
        gpio.set_input(5, True)
        gpio.set_input(6, True)
        drv.sync()
        assert drv.override is False

    def test_held_switch_stops_at_endstop(self, drv, gpio):
        gpio.set_input(5, True)
        drv.sync()
        gpio.trigger(23, True)
        drv.sync()
        drv.sync()
        assert drv.state is DoorState.OPEN
        assert drv.override is True
        assert motor(gpio) == (False, False, False)

    def test_release(self, drv, gpio):
        gpio.set_input(6, True)
        drv.sync()
        gpio.set_input(6, False)
        drv.sync()
        assert drv.override is False
        assert drv.state is DoorState.STOPPED

    def test_ignored_in_fault(self, drv, gpio):
        drv.set_fault("jam")
        gpio.set_input(5, True)
        drv.sync()
        assert drv.override is False


class TestReference:

    def travel(self, drv, gpio, clock, close_after=1.0, open_after=3.0):
        start = clock.monotonic()
        orig = clock.sleep

        def sleep(s):
            orig(s)
            t = clock.monotonic() - start
            if drv.state is DoorState.CLOSING:
                gpio.set_input(23, False)
                if t >= close_after:
                    gpio.set_input(24, True)
            if drv.state is DoorState.OPENING:
                gpio.set_input(24, False)
                if t >= open_after:
                    gpio.set_input(23, True)
        clock.sleep = sleep

    @pytest.mark.parametrize("start", [None, 23, 24])
    def test_success_from_any_position(self, drv, gpio, clock, start):
        if start:
            gpio.set_input(start, True)
        self.travel(drv, gpio, clock)
        ms = drv.reference()
        assert 1500 <= ms <= 3300
        assert drv.state is DoorState.OPEN
        assert drv.reference_active is False

    def test_lower_timeout(self, gpio, clock):
        d = DoorDriver(gpio, replace(PINS, reference_timeout=5), clock)
        assert d.reference() is None
        assert "lower Endstop not hit" in d.fault
        assert d.reference_active is False
        assert motor(gpio) == (False, False, False)

    def test_upper_timeout(self, gpio, clock):
        d = DoorDriver(gpio, replace(PINS, reference_timeout=5), clock)
        gpio.set_input(24, True)
        orig = clock.sleep

        def sleep(s):
            orig(s)
            if d.state is DoorState.OPENING:
                gpio.set_input(24, False)
        clock.sleep = sleep
        assert d.reference() is None
        assert "upper Endstop not hit" in d.fault

    def test_refused_in_fault_or_both_active(self, drv, gpio):
        drv.set_fault("jam")
        assert drv.reference() is None
        drv.clear_fault()
        gpio.set_input(23, True)
        gpio.set_input(24, True)
        assert drv.reference() is None
        assert drv.fault is None

    def test_bounce_is_confirmed(self, drv, gpio, clock):
        """A single-sample contact bounce does not end a leg."""
        hits = {"n": 0}
        orig = clock.sleep

        def sleep(s):
            orig(s)
            if drv.state is DoorState.CLOSING:
                hits["n"] += 1
                gpio.set_input(24, hits["n"] in (3,) or hits["n"] >= 10)
            if drv.state is DoorState.OPENING:
                gpio.set_input(24, False)
                gpio.set_input(23, True)
        clock.sleep = sleep
        assert drv.reference() is not None
        assert hits["n"] >= 10


def test_shutdown_cuts_motor(drv, gpio):
    drv.open()
    drv.shutdown()
    assert motor(gpio) == (False, False, False)
