"""Typed application state model for the Dinky Coop controller.

``AppState`` is a dataclass that holds every piece of runtime state previously
scattered as untyped string keys in ``protected_dict._dictionary``.  It brings:

* Proper Python types (``bool``, ``int``, ``float``, ``Optional[float]``, …)
* A single authoritative definition of defaults (fixes the hardcoded-Boulder
  location fallback and the ``auto_mode`` string/bool inconsistency)
* IDE autocompletion and mypy type-checking for all state access
* Easier test assertions (compare typed values, not opaque strings)

The ``protected_dict`` singleton still controls all multi-threaded access via
its ``threading.Lock``; this module is purely structural.

Sub-configs
-----------
``LocationConfig`` and ``GpioConfig`` capture the GPIO and location defaults
in one place instead of in multiple ``load_config`` / ``GPIO_DEFAULTS`` dicts
spread across ``app.py``.  They are stored as plain ``dict`` fields on
``AppState`` (via ``default_factory``) so callers that already read them as
dicts continue to work without change.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Sub-config dataclasses (define canonical defaults)
# ---------------------------------------------------------------------------

@dataclass
class LocationConfig:
    """Geographic location used for sunrise/sunset calculations."""
    city: str = "Boulder"
    region: str = "USA"
    timezone: str = "America/Denver"
    latitude: float = 40.01499
    longitude: float = -105.27055


@dataclass
class GpioConfig:
    """BCM GPIO pin assignments and behaviour flags for all connected hardware."""
    motor_in1: int = 17
    motor_in2: int = 27
    motor_ena: int = 22
    endstop_up: int = 23
    endstop_down: int = 24
    override_open: int = 5
    override_close: int = 6
    dht11_data: int = 26
    dht22_data: int = 21
    dht22_power: int = 20
    invert_end_up: bool = False
    invert_end_down: bool = False
    reference_timeout: int = 60


def _default_location() -> dict:
    """Return the default location as a plain dict (mirrors old config.yaml shape)."""
    return dataclasses.asdict(LocationConfig())


def _default_gpio() -> dict:
    """Return the default GPIO config as a plain dict (mirrors old config.yaml shape)."""
    return dataclasses.asdict(GpioConfig())


# ---------------------------------------------------------------------------
# Main application state
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    """All runtime state for the coop door controller.

    Fields are grouped by lifecycle:

    * **Config** — loaded from ``config.yaml`` at startup, persisted on change.
    * **Door state** — ephemeral, updated by ``DoorTaskRunner`` every 0.5 s.
    * **Sun times** — ephemeral, recalculated each loop iteration.
    * **Sensor readings** — ephemeral, updated by ``temperature_task`` every 2.5 s.
    * **Control flags** — single-use boolean triggers consumed by ``DoorTaskRunner``.
    * **Secrets** — VAPID keys loaded from ``.secrets.yaml``, never persisted.
    """

    # ── Config (persisted to config.yaml) ------------------------------------
    auto_mode: bool = True
    sunrise_offset: int = 0
    sunset_offset: int = 0
    consoleLogToFile: bool = False
    csvLog: bool = True
    enable_camera: bool = False
    camera_index: int = 0
    outdoor_sensor_type: str = "dht22"
    location: dict = field(default_factory=_default_location)
    gpio: dict = field(default_factory=_default_gpio)

    # ── Door state (runtime only) --------------------------------------------
    desired_door_state: str = "stopped"
    state: str = "stopped"
    override: bool = False
    error_state: str = ""
    reference_door_endstops_ms: Optional[float] = None
    door_position_estimate: float = -1.0

    # ── Calculated sun times (runtime only) ----------------------------------
    sunrise: Optional[datetime] = None
    sunset: Optional[datetime] = None

    # ── Sensor readings (runtime only) ---------------------------------------
    temp_in: Optional[float] = None
    temp_in_min: float = 500.0
    temp_in_max: float = -500.0
    hum_in: Optional[float] = None
    hum_in_min: float = 500.0
    hum_in_max: float = -500.0
    temp_out: Optional[float] = None
    temp_out_min: float = 500.0
    temp_out_max: float = -500.0
    hum_out: Optional[float] = None
    hum_out_min: float = 500.0
    hum_out_max: float = -500.0
    cpu_temp: Optional[float] = None
    cpu_temp_min: float = 500.0
    cpu_temp_max: float = -500.0

    # ── Control flags (runtime only) -----------------------------------------
    toggle_reference_of_endstops: bool = False
    clear_error_state: bool = False
    debug_error: bool = False

    # ── VAPID secrets for Web Push (loaded from .secrets.yaml) ---------------
    vapid_public_key: Optional[str] = None
    vapid_private_key: Optional[str] = None
