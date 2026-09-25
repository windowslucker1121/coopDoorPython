"""GPIO backends.

The application talks to GPIO exclusively through :class:`GpioBackend`, so
the door driver and sensors work identically on a Raspberry Pi
(:class:`RpiGpio`) and on a development machine / in tests
(:class:`MockGpio`).

Levels are plain booleans (``True`` = HIGH).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

EdgeCallback = Callable[[int], None]


class GpioBackend(Protocol):
    is_mock: bool

    def setup_output(self, pin: int, initial: bool = False) -> None: ...
    def setup_input(self, pin: int, pull_down: bool = True) -> None: ...
    def write(self, pin: int, value: bool) -> None: ...
    def read(self, pin: int) -> bool: ...
    def add_edge_callback(self, pin: int, callback: EdgeCallback, bouncetime_ms: int) -> None: ...
    def cleanup(self) -> None: ...


class MockGpio:
    """In-memory GPIO.  Inputs can be driven with :meth:`set_input` (no
    callbacks) or :meth:`trigger` (sets the level and fires edge callbacks
    synchronously, like a real edge would)."""

    is_mock = True

    def __init__(self):
        self._lock = threading.Lock()
        self._levels: dict[int, bool] = {}
        self._modes: dict[int, str] = {}
        self._callbacks: dict[int, list[EdgeCallback]] = {}

    def setup_output(self, pin: int, initial: bool = False) -> None:
        with self._lock:
            self._modes[pin] = "OUT"
            self._levels[pin] = initial

    def setup_input(self, pin: int, pull_down: bool = True) -> None:
        with self._lock:
            self._modes[pin] = "IN"
            self._levels.setdefault(pin, False)

    def write(self, pin: int, value: bool) -> None:
        with self._lock:
            if self._modes.get(pin) != "OUT":
                raise ValueError(f"GPIO {pin} is not configured as output")
            self._levels[pin] = bool(value)

    def read(self, pin: int) -> bool:
        return self._levels.get(pin, False)

    def add_edge_callback(self, pin: int, callback: EdgeCallback, bouncetime_ms: int = 0) -> None:
        with self._lock:
            self._callbacks.setdefault(pin, []).append(callback)

    def cleanup(self) -> None:
        with self._lock:
            self._callbacks.clear()

    # ── test / simulation helpers ─────────────────────────────────────
    def set_input(self, pin: int, value: bool) -> None:
        self._levels[pin] = bool(value)

    def trigger(self, pin: int, value: bool) -> None:
        changed = self._levels.get(pin, False) != bool(value)
        self._levels[pin] = bool(value)
        if changed:
            for cb in list(self._callbacks.get(pin, [])):
                cb(pin)

    def mode(self, pin: int) -> str | None:
        return self._modes.get(pin)

    def snapshot(self) -> dict[int, dict]:
        return {pin: {"mode": self._modes.get(pin, "N/A"), "state": "HIGH" if level else "LOW"}
                for pin, level in self._levels.items()}


class RpiGpio:
    """Adapter around the ``RPi.GPIO`` module (or ``rpi-lgpio``)."""

    is_mock = False

    def __init__(self, module=None):
        if module is None:
            import RPi.GPIO as module  # noqa: N813 — raises on non-Pi hosts
        self._gpio = module
        self._gpio.setwarnings(False)
        self._gpio.setmode(self._gpio.BCM)

    def setup_output(self, pin: int, initial: bool = False) -> None:
        self._gpio.setup(pin, self._gpio.OUT, initial=self._gpio.HIGH if initial else self._gpio.LOW)

    def setup_input(self, pin: int, pull_down: bool = True) -> None:
        pud = self._gpio.PUD_DOWN if pull_down else self._gpio.PUD_UP
        self._gpio.setup(pin, self._gpio.IN, pull_up_down=pud)

    def write(self, pin: int, value: bool) -> None:
        self._gpio.output(pin, self._gpio.HIGH if value else self._gpio.LOW)

    def read(self, pin: int) -> bool:
        return bool(self._gpio.input(pin))

    def add_edge_callback(self, pin: int, callback: EdgeCallback, bouncetime_ms: int = 0) -> None:
        self._gpio.add_event_detect(pin, self._gpio.BOTH, callback=callback, bouncetime=bouncetime_ms)

    def cleanup(self) -> None:
        try:
            self._gpio.cleanup()
        except Exception:  # pragma: no cover - best effort on shutdown
            pass
