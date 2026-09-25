"""Temperature / humidity monitoring with spike filtering and daily min/max."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from ..clock import Clock
from ..hardware.sensors import Reading, TemperatureSensor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricValue:
    value: float | None = None
    min: float | None = None
    max: float | None = None


class MetricTracker:
    """Current value + min/max since the last reset, with a spike filter.

    DHT sensors occasionally return a single wildly wrong value.  A change
    larger than ``spike_threshold`` is ignored — unless it repeats
    ``accept_after`` times in a row, in which case the change is real (e.g.
    the sensor was offline during a cold night) and accepted.
    """

    def __init__(self, name: str, spike_threshold: float, accept_after: int = 3):
        self.name = name
        self.spike_threshold = spike_threshold
        self.accept_after = accept_after
        self._rejected = 0
        self.current = MetricValue()

    def update(self, value: float | None) -> None:
        if value is None:
            return
        cur = self.current
        if cur.value is not None and abs(value - cur.value) > self.spike_threshold:
            self._rejected += 1
            if self._rejected < self.accept_after:
                value = cur.value
            else:
                logger.warning("%s: accepting large change after %d consecutive readings (%.1f -> %.1f)",
                               self.name, self._rejected, cur.value, value)
                self._rejected = 0
        else:
            self._rejected = 0
        self.current = MetricValue(
            value,
            value if cur.min is None else min(cur.min, value),
            value if cur.max is None else max(cur.max, value),
        )

    def reset_extremes(self) -> None:
        v = self.current.value
        self.current = MetricValue(v, None, None)


# (metric name, sensor attribute, reading field, spike threshold)
_METRICS = (
    ("temp_in", "indoor", "temperature_c", 3.0),
    ("hum_in", "indoor", "humidity", 5.0),
    ("temp_out", "outdoor", "temperature_c", 3.0),
    ("hum_out", "outdoor", "humidity", 5.0),
    ("cpu_temp", "cpu", "temperature_c", 5.0),
)


class EnvironmentMonitor:
    """Polls the sensors and keeps the latest values (thread-safe snapshot)."""

    INTERVAL_S = 2.5

    def __init__(self, indoor: TemperatureSensor, outdoor: TemperatureSensor,
                 cpu: TemperatureSensor, clock: Clock):
        self._indoor = indoor
        self._outdoor = outdoor
        self._cpu = cpu
        self._clock = clock
        self._trackers = {name: MetricTracker(name, threshold) for name, _, _, threshold in _METRICS}
        self._day: date | None = None
        self._snapshot: dict[str, MetricValue] = {name: MetricValue() for name in self._trackers}

    @property
    def values(self) -> dict[str, MetricValue]:
        return self._snapshot

    def poll(self) -> float:
        readings: dict[str, Reading] = {
            "indoor": self._safe_read(self._indoor),
            "outdoor": self._safe_read(self._outdoor),
            "cpu": self._safe_read(self._cpu),
        }
        today = self._clock.now().date()
        if today != self._day:
            for tracker in self._trackers.values():
                tracker.reset_extremes()
            self._day = today
        for name, sensor, field, _ in _METRICS:
            self._trackers[name].update(getattr(readings[sensor], field))
        self._snapshot = {name: t.current for name, t in self._trackers.items()}
        return self.INTERVAL_S

    @staticmethod
    def _safe_read(sensor: TemperatureSensor) -> Reading:
        try:
            return sensor.read()
        except Exception as e:
            logger.error("Sensor %s failed: %s", getattr(sensor, "name", sensor), e)
            return Reading()
