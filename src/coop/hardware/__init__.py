"""Hardware selection.

:func:`build_hardware` returns a :class:`Hardware` bundle with either the
real Raspberry Pi backends or mocks:

* ``use_mock_hardware: true`` or no ``RPi.GPIO`` available → mock GPIO,
  random-walk sensors, mock camera and (optionally) the door simulator.
* On real GPIO, a sensor whose driver cannot be loaded is replaced by a
  :class:`~coop.hardware.sensors.NullSensor` (no fake data in production).
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Callable

from ..config import OutdoorSensorType, Settings
from .camera import MockCamera, OpenCvCamera
from .gpio import GpioBackend, MockGpio, RpiGpio
from .sensors import (CpuTemperatureSensor, DhtSensor, NullSensor, OpenMeteoSensor,
                      RandomWalkSensor, TemperatureSensor)
from .simulator import DoorSimulator

logger = logging.getLogger(__name__)


@dataclass
class Hardware:
    gpio: GpioBackend
    indoor: TemperatureSensor
    outdoor: TemperatureSensor
    cpu: TemperatureSensor
    camera_factory: Callable[[int], object]
    simulator: DoorSimulator | None = None

    @property
    def is_mock(self) -> bool:
        return self.gpio.is_mock


def _kill_stale_pulsein() -> None:
    """Adafruit's DHT helper processes survive crashes and block the pins
    ("Unable to set line ... to input")."""
    for name in ("libgpiod_pulsein", "libgpiod_pulsein64"):
        try:
            subprocess.run(["killall", "-9", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
        except (OSError, ValueError):
            pass


def _outdoor_api(settings_provider: Callable[[], Settings]) -> OpenMeteoSensor:
    def location():
        loc = settings_provider().location
        return loc.latitude, loc.longitude
    return OpenMeteoSensor(location)


def build_hardware(settings: Settings, settings_provider: Callable[[], Settings]) -> Hardware:
    gpio: GpioBackend | None = None
    if not settings.use_mock_hardware and os.name != "nt":
        try:
            gpio = RpiGpio()
        except Exception as e:
            logger.error("RPi.GPIO unavailable (%s) - falling back to mock hardware.", e)

    if gpio is None:
        logger.warning("Using MOCK hardware (no real GPIO, simulated sensors).")
        mock = MockGpio()
        outdoor = (_outdoor_api(settings_provider)
                   if settings.outdoor_sensor_type is OutdoorSensorType.API
                   else RandomWalkSensor("mock-dht22", (5.0, 25.0), (40.0, 90.0)))
        simulator = DoorSimulator(mock, settings.gpio) if settings.simulate_door else None
        return Hardware(
            gpio=mock,
            indoor=RandomWalkSensor("mock-dht11", (15.0, 30.0), (30.0, 70.0)),
            outdoor=outdoor,
            cpu=RandomWalkSensor("mock-cpu", (35.0, 65.0), with_humidity=False),
            camera_factory=MockCamera,
            simulator=simulator,
        )

    _kill_stale_pulsein()
    pins = settings.gpio
    try:
        indoor: TemperatureSensor = DhtSensor("DHT11", pins.dht11_data)
    except Exception as e:
        logger.error("DHT11 unavailable: %s", e)
        indoor = NullSensor()
    if settings.outdoor_sensor_type is OutdoorSensorType.API:
        outdoor: TemperatureSensor = _outdoor_api(settings_provider)
    else:
        try:
            outdoor = DhtSensor("DHT22", pins.dht22_data, gpio=gpio, power_pin=pins.dht22_power)
        except Exception as e:
            logger.error("DHT22 unavailable: %s", e)
            outdoor = NullSensor()
    return Hardware(gpio=gpio, indoor=indoor, outdoor=outdoor, cpu=CpuTemperatureSensor(),
                    camera_factory=OpenCvCamera)
