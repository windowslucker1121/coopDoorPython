"""Temperature / humidity sensors.

Every sensor returns a :class:`Reading` in **degrees Celsius** and percent
relative humidity; either value may be ``None`` when the read failed.
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from .gpio import GpioBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reading:
    temperature_c: float | None = None
    humidity: float | None = None


EMPTY = Reading()


class TemperatureSensor(ABC):
    name = "sensor"

    @abstractmethod
    def read(self) -> Reading:
        """Return the current reading (never raises)."""

    def close(self) -> None:
        pass


# ─────────────────────────────── DHT (Adafruit) ─────────────────────────────

class DhtSensor(TemperatureSensor):
    """Adafruit DHT11 / DHT22 with retries and recovery.

    * ``RuntimeError`` / ``OverflowError`` (checksum, timing) → wait and retry
    * ``OSError`` (PulseIn stuck, errno 22) → re-create the device
    * optional power pin: the sensor is powered only while reading, which
      works around DHT22 lock-ups.
    """

    ATTEMPTS = 3
    RETRY_DELAY_S = 2.2
    POWER_UP_DELAY_S = 2.2

    def __init__(self, model: str, data_pin: int, *, gpio: GpioBackend | None = None,
                 power_pin: int | None = None, sleep: Callable[[float], None] = time.sleep,
                 dht_module=None, board_module=None):
        if dht_module is None:
            import adafruit_dht as dht_module  # noqa: N813
        if board_module is None:
            import board as board_module  # noqa: N813
        self.name = model
        self._dht = dht_module
        self._model = model
        self._pin = getattr(board_module, f"D{data_pin}", data_pin)
        self._gpio = gpio
        self._power_pin = power_pin
        self._sleep = sleep
        if power_pin is not None and gpio is not None:
            gpio.setup_output(power_pin, False)
        self._device = self._create()

    def _create(self):
        return getattr(self._dht, self._model)(self._pin)

    def _reinit(self):
        try:
            self._device.exit()
        except Exception:
            pass
        self._sleep(self.RETRY_DELAY_S)
        self._device = self._create()

    def read(self) -> Reading:
        powered = self._power_pin is not None and self._gpio is not None
        if powered:
            self._gpio.write(self._power_pin, True)
            self._sleep(self.POWER_UP_DELAY_S)
        try:
            for _ in range(self.ATTEMPTS):
                try:
                    return Reading(self._device.temperature, self._device.humidity)
                except (RuntimeError, OverflowError):
                    self._sleep(self.RETRY_DELAY_S)
                except OSError:
                    logger.warning("%s: OSError on read, reinitialising sensor", self._model)
                    self._reinit()
                except Exception as e:  # never let a sensor kill the monitor
                    logger.error("%s: unexpected error: %s", self._model, e)
                    break
            return EMPTY
        finally:
            if powered:
                self._gpio.write(self._power_pin, False)

    def close(self) -> None:
        try:
            self._device.exit()
        except Exception:
            pass


# ─────────────────────────────── Open-Meteo ─────────────────────────────────

class OpenMeteoSensor(TemperatureSensor):
    """Outdoor conditions from the free Open-Meteo API (no key required).

    Results are cached for ``cache_seconds``; failed requests also restart
    the cache window so the API is never hammered.  On failure the last
    good reading is returned.
    """

    name = "open-meteo"
    URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, get_location: Callable[[], tuple[float, float]], *, cache_seconds: float = 300,
                 http_get=None, monotonic: Callable[[], float] = time.monotonic):
        if http_get is None:
            import requests
            http_get = requests.get
        self._get_location = get_location
        self._cache_seconds = cache_seconds
        self._http_get = http_get
        self._monotonic = monotonic
        self._cached = EMPTY
        self._last_fetch: float | None = None

    def read(self) -> Reading:
        now = self._monotonic()
        if self._last_fetch is not None and now - self._last_fetch < self._cache_seconds:
            return self._cached
        self._last_fetch = now
        try:
            lat, lon = self._get_location()
            response = self._http_get(self.URL, params={
                "latitude": float(lat),
                "longitude": float(lon),
                "current": "temperature_2m,relative_humidity_2m",
                "temperature_unit": "celsius",
                "timezone": "auto",
            }, timeout=10)
            response.raise_for_status()
            current = response.json().get("current", {})
            temp, hum = current.get("temperature_2m"), current.get("relative_humidity_2m")
            self._cached = Reading(
                float(temp) if temp is not None else None,
                float(hum) if hum is not None else None,
            )
        except Exception as e:
            logger.warning("Open-Meteo request failed: %s", e)
        return self._cached


# ─────────────────────────────── CPU ────────────────────────────────────────

class CpuTemperatureSensor(TemperatureSensor):
    """SoC temperature from the Linux thermal zone (no gpiozero needed)."""

    name = "cpu"

    def __init__(self, path: str = "/sys/class/thermal/thermal_zone0/temp"):
        self._path = path

    def read(self) -> Reading:
        try:
            with open(self._path) as f:
                return Reading(int(f.read().strip()) / 1000.0, None)
        except (OSError, ValueError):
            return EMPTY


# ─────────────────────────────── mocks ──────────────────────────────────────

class RandomWalkSensor(TemperatureSensor):
    """Plausible, slowly drifting fake readings for development."""

    def __init__(self, name: str, temp_range=(15.0, 30.0), hum_range=(30.0, 70.0),
                 step=(0.3, 1.0), with_humidity: bool = True, rng: random.Random | None = None):
        self.name = name
        self._rng = rng or random.Random()
        self._t_range, self._h_range = temp_range, hum_range
        self._step = step
        self._with_humidity = with_humidity
        self._t = self._rng.uniform(*temp_range)
        self._h = self._rng.uniform(*hum_range)

    def read(self) -> Reading:
        self._t = min(self._t_range[1], max(self._t_range[0], self._t + self._rng.uniform(-self._step[0], self._step[0])))
        self._h = min(self._h_range[1], max(self._h_range[0], self._h + self._rng.uniform(-self._step[1], self._step[1])))
        return Reading(round(self._t, 1), round(self._h, 1) if self._with_humidity else None)


class NullSensor(TemperatureSensor):
    name = "none"

    def read(self) -> Reading:
        return EMPTY
