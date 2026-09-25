"""GPIO-level door driver: motor, endstops, manual override switch.

Wiring (H-bridge):

=========  =====  =====  =====
action     in1    in2    ena
=========  =====  =====  =====
open (up)  LOW    LOW    HIGH
close      HIGH   HIGH   HIGH
stop       LOW    LOW    LOW
=========  =====  =====  =====

An endstop is *active* when its input is HIGH (LOW if ``invert_end_*``).
The lower endstop sits at the motor and fires when the rope goes slack.

Threading
---------
Endstop edge callbacks run in RPi.GPIO's native thread.  They only cut the
motor outputs and record which endstop was reached — no locks, no logging —
and :meth:`DoorDriver.sync` (called by the control loop) applies the state
change.  The manual switch is polled by :meth:`sync`, it needs no callback.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from ..clock import Clock
from ..config import GpioConfig
from ..hardware.gpio import GpioBackend
from .model import DoorState

logger = logging.getLogger(__name__)


class DoorDriver:
    ENDSTOP_BOUNCE_MS = 250
    REFERENCE_POLL_S = 0.1

    def __init__(self, gpio: GpioBackend, pins: GpioConfig, clock: Clock):
        self._gpio = gpio
        self._pins = pins
        self._clock = clock
        self.state = DoorState.STOPPED
        self.override = False
        self.fault: str | None = None
        self.move_started: float | None = None  # monotonic time the current movement began
        self.reference_active = False
        self._edge_stop: DoorState | None = None

        for pin in (pins.motor_in1, pins.motor_in2, pins.motor_ena):
            gpio.setup_output(pin, False)
        for pin in (pins.endstop_up, pins.endstop_down, pins.override_open, pins.override_close):
            gpio.setup_input(pin, pull_down=True)
        gpio.add_edge_callback(pins.endstop_up, self._on_endstop_edge, self.ENDSTOP_BOUNCE_MS)
        gpio.add_edge_callback(pins.endstop_down, self._on_endstop_edge, self.ENDSTOP_BOUNCE_MS)

    # ── configuration ────────────────────────────────────────────────
    @property
    def pins(self) -> GpioConfig:
        return self._pins

    def apply_live_settings(self, pins: GpioConfig) -> None:
        """Apply the settings that can change at runtime (invert flags and
        reference timeout).  Pin numbers need a restart."""
        self._pins = replace(self._pins, invert_end_up=pins.invert_end_up,
                             invert_end_down=pins.invert_end_down,
                             reference_timeout=pins.reference_timeout)

    # ── inputs ───────────────────────────────────────────────────────
    def upper_active(self) -> bool:
        return self._gpio.read(self._pins.endstop_up) != self._pins.invert_end_up

    def lower_active(self) -> bool:
        return self._gpio.read(self._pins.endstop_down) != self._pins.invert_end_down

    def switch_position(self) -> DoorState | None:
        """``OPEN`` / ``CLOSED`` while exactly one switch input is active."""
        o = self._gpio.read(self._pins.override_open)
        c = self._gpio.read(self._pins.override_close)
        if o and not c:
            return DoorState.OPEN
        if c and not o:
            return DoorState.CLOSED
        return None

    # ── motor primitives ─────────────────────────────────────────────
    def _drive(self, direction: DoorState) -> None:
        p = self._pins
        closing = direction is DoorState.CLOSING
        self._gpio.write(p.motor_in1, closing)
        self._gpio.write(p.motor_in2, closing)
        self._gpio.write(p.motor_ena, True)
        if self.state is not direction:
            self.move_started = self._clock.monotonic()
            logger.info("Door %s", direction.value)
        self.state = direction

    def _cut_motor(self) -> None:
        p = self._pins
        self._gpio.write(p.motor_in1, False)
        self._gpio.write(p.motor_in2, False)
        self._gpio.write(p.motor_ena, False)

    def stop(self, state: DoorState = DoorState.STOPPED) -> None:
        self._cut_motor()
        if self.state is not state:
            logger.info("Door stopped: %s", state.value)
        self.state = state

    def open(self) -> None:
        """Start (or keep) opening; stops as OPEN at the upper endstop."""
        if self.fault:
            return
        if self.upper_active():
            self.stop(DoorState.OPEN)
        else:
            self._drive(DoorState.OPENING)

    def close(self) -> None:
        """Start (or keep) closing; stops as CLOSED at the lower endstop."""
        if self.fault:
            return
        if self.lower_active():
            self.stop(DoorState.CLOSED)
        else:
            self._drive(DoorState.CLOSING)

    def motor_outputs(self) -> dict[str, bool]:
        p = self._pins
        return {name: self._gpio.read(getattr(p, name)) for name in ("motor_in1", "motor_in2", "motor_ena")}

    # ── faults ───────────────────────────────────────────────────────
    def set_fault(self, message: str) -> None:
        """Stop the motor and lock out all movement until cleared."""
        self.stop()
        if message != self.fault:
            logger.critical("Door fault: %s - motor locked until the error is cleared.", message)
        self.fault = message

    def clear_fault(self) -> None:
        if self.fault:
            logger.info("Door fault cleared")
        self.fault = None

    # ── edge callback (native thread!) ───────────────────────────────
    def _on_endstop_edge(self, channel: int) -> None:
        if self.reference_active or self.fault:
            return
        state = self.state
        if state is DoorState.OPENING and channel == self._pins.endstop_up and self.upper_active():
            self._cut_motor()
            self._edge_stop = DoorState.OPEN
        elif state is DoorState.CLOSING and channel == self._pins.endstop_down and self.lower_active():
            self._cut_motor()
            self._edge_stop = DoorState.CLOSED

    # ── polling (control loop) ───────────────────────────────────────
    def sync(self) -> None:
        """Bring ``state`` / ``override`` in line with the inputs.

        * applies a stop recorded by the edge callback
        * polls the endstops (safety net for missed edges): the upper
          endstop is ignored while closing, the lower one while opening
        * handles the manual override switch
        """
        if self.reference_active:
            return
        edge_stop, self._edge_stop = self._edge_stop, None
        if self.fault:
            return
        if edge_stop is not None and self.state.moving:
            logger.info("Endstop reached (edge): %s", edge_stop.value)
            self.stop(edge_stop)

        if self.state is not DoorState.CLOSING and self.upper_active():
            if self.state is not DoorState.OPEN:
                self.stop(DoorState.OPEN)
        elif self.state is not DoorState.OPENING and self.lower_active():
            if self.state is not DoorState.CLOSED:
                self.stop(DoorState.CLOSED)

        switch = self.switch_position()
        if switch is not None:
            if not self.override:
                logger.info("[Hardware Switch] %s", "OPEN" if switch is DoorState.OPEN else "CLOSE")
            self.override = True
            self.open() if switch is DoorState.OPEN else self.close()
        elif self.override:
            logger.info("[Hardware Switch] released")
            self.override = False
            self.stop(self.rest_state())

    def rest_state(self) -> DoorState:
        if self.upper_active():
            return DoorState.OPEN
        if self.lower_active():
            return DoorState.CLOSED
        return DoorState.STOPPED

    # ── reference sequence ───────────────────────────────────────────
    def reference(self) -> float | None:
        """Measure the closed → open travel time (blocking).

        Closes to the lower endstop, then opens to the upper one.  Works
        from any start position.  Returns the travel time in ms, or ``None``
        on failure (a timeout sets a fault).
        """
        if self.fault:
            logger.error("Reference refused: door is in fault state")
            return None
        if self.upper_active() and self.lower_active():
            logger.error("Both endstops report active - check wiring / invert settings. Reference aborted.")
            return None
        timeout = self._pins.reference_timeout
        logger.info("Reference sequence started (timeout %ss per direction)", timeout)
        self.reference_active = True
        try:
            if not self._reference_leg(DoorState.CLOSING, self.lower_active, timeout):
                self.set_fault("Reference sequence timed out - lower Endstop not hit")
                return None
            self.stop(DoorState.CLOSED)
            started = self._clock.monotonic()
            if not self._reference_leg(DoorState.OPENING, self.upper_active, timeout):
                self.set_fault("Reference sequence timed out - upper Endstop not hit")
                return None
            travel_ms = (self._clock.monotonic() - started) * 1000.0
            self.stop(DoorState.OPEN)
            logger.info("Reference sequence finished: %.0f ms", travel_ms)
            return travel_ms
        finally:
            self.reference_active = False

    def _reference_leg(self, direction: DoorState, reached, timeout: float) -> bool:
        if not reached():
            self._drive(direction)
        started = self._clock.monotonic()
        while True:
            if reached():
                self._clock.sleep(self.REFERENCE_POLL_S)  # confirm (contact bounce)
                if reached():
                    return True
            if self._clock.monotonic() - started > timeout:
                self.stop()
                return False
            self._clock.sleep(self.REFERENCE_POLL_S)

    def shutdown(self) -> None:
        try:
            self._cut_motor()
        except Exception:
            pass
