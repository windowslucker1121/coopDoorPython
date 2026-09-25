"""Physical door simulation for mock hardware.

Watches the motor outputs on a :class:`~coop.hardware.gpio.MockGpio` and
moves a virtual door accordingly, driving the endstop inputs (including
edge callbacks) exactly like the real switches would.  Lets the complete
application — schedules, reference run, premature-close detection — be run
and demonstrated on any machine.
"""

from __future__ import annotations

from ..config import GpioConfig
from .gpio import MockGpio


class DoorSimulator:
    def __init__(self, gpio: MockGpio, pins: GpioConfig, travel_time_s: float = 8.0,
                 position: float = 0.0):
        self._gpio = gpio
        self._pins = pins
        self.travel_time_s = travel_time_s
        self.position = position  # 0.0 = closed, 1.0 = open
        self._apply_endstops()

    def update_pins(self, pins: GpioConfig) -> None:
        self._pins = pins
        self._apply_endstops()

    def tick(self, dt: float) -> None:
        pins = self._pins
        if self._gpio.read(pins.motor_ena):
            closing = self._gpio.read(pins.motor_in1)
            delta = dt / self.travel_time_s
            self.position = max(0.0, min(1.0, self.position + (-delta if closing else delta)))
        self._apply_endstops()

    def _apply_endstops(self) -> None:
        pins = self._pins
        upper = self.position >= 1.0
        lower = self.position <= 0.0
        self._gpio.trigger(pins.endstop_up, upper != pins.invert_end_up)
        self._gpio.trigger(pins.endstop_down, lower != pins.invert_end_down)
