"""Temperature / humidity background task.

Reads two DHT22 sensors (indoor + outdoor) every 2.5 s and writes the
values — plus CPU temperature — into ``protected_dict``.  Min / max
tracking is reset at midnight.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date as _date
from typing import Any

from protected_dict import protected_dict as global_vars

logger = logging.getLogger(__name__)


def temperature_task_main() -> None:
    """Background loop — runs forever in a daemon thread."""
    if os.name == "nt":
        from MockDHT22 import MockDHT22 as DHT22
        from mock_board import MockBoard as _BoardModule
        from mock_temperatur import MockCPUTemperature as CPUTemperature

        board = _BoardModule()
    else:
        import board  # type: ignore[import]
        from dht22 import DHT22
        from gpiozero import CPUTemperature  # type: ignore[import]

    data_pin_out = board.D21
    data_pin_in = board.D16
    dht_out = DHT22(data_pin_out, power_pin=20)
    dht_in = DHT22(data_pin_in, power_pin=26)
    last_date: _date | None = None

    while True:
        temp_out, hum_out = dht_out.get_temperature_and_humidity()
        temp_in, hum_in = dht_in.get_temperature_and_humidity()

        # Reset min/max counters at midnight
        current_date = _date.today()
        if current_date != last_date:
            global_vars.instance().set_values(
                {
                    "temp_in_min": 500,
                    "temp_in_max": -500,
                    "temp_out_min": 500,
                    "temp_out_max": -500,
                    "hum_in_min": 500,
                    "hum_in_max": -500,
                    "hum_out_min": 500,
                    "hum_out_max": -500,
                    "cpu_temp_min": 500,
                    "cpu_temp_max": -500,
                }
            )
            last_date = current_date

        _update_val(temp_in, "temp_in")
        _update_val(hum_in, "hum_in")
        _update_val(temp_out, "temp_out")
        _update_val(hum_out, "hum_out")

        cpu_temp: float | None = CPUTemperature().temperature
        _update_val(cpu_temp, "cpu_temp")

        time.sleep(2.5)


def _update_val(val: float | None, name: str) -> None:
    """Write *val* into ``protected_dict[name]`` and maintain min/max.

    Sanity-checks the reading: if it deviates more than ±5 from the
    previous value it is silently discarded (keeps errant DHT22 spikes out
    of the display).
    """
    if val is None:
        return

    val_old: Any
    val_max: Any
    val_min: Any
    val_old, val_max, val_min = global_vars.instance().get_values(
        [name, name + "_max", name + "_min"]
    )

    # Spike filter
    if val_old is not None:
        if val > val_old + 5.0 or val < val_old - 5.0:
            val = val_old

    val_max = val_max if val_max is not None else -500.0
    val_min = val_min if val_min is not None else 500.0

    if val > val_max:
        val_max = val
    if val < val_min:
        val_min = val

    global_vars.instance().set_values(
        {name: val, name + "_max": val_max, name + "_min": val_min}
    )
