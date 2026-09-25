"""JSON payloads for the dashboard (``data`` event, CSV log, ``/api/status``)
and the debug panel.  Field names and formats are the frontend contract."""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..config import Mode

if TYPE_CHECKING:  # pragma: no cover
    from ..application import Application

MASK = "********"


def _temp(value: float | None) -> str:
    return "" if value is None else "%0.1f\N{DEGREE SIGN}C" % value


def _hum(value: float | None) -> str:
    return "" if value is None else "%0.1f%%" % value


def _clock_time(value: datetime | None) -> str:
    return value.strftime("%I:%M:%S %p").lstrip("0") if value else ""


def _countdown(target: datetime | None, now: datetime) -> str:
    if target is None:
        return ""
    delta = target - now
    if delta <= timedelta(0):
        return "passed"
    return (datetime.min + delta).strftime("%H:%M:%S")


def _flag(value: bool) -> str:
    return "True" if value else "False"


def dashboard_payload(app: "Application") -> dict:
    s = app.settings
    st = app.controller.status
    env = app.environment.values
    now = app.clock.now()
    metrics = app.system.metrics()

    if s.mode is Mode.MANUAL:
        tu_open = tu_close = "disabled"
    else:
        tu_open, tu_close = _countdown(st.open_time, now), _countdown(st.close_time, now)

    data = {
        "time": now.strftime("%H:%M:%S.%f")[:-3],
        "os_timestamp": int(time.time()),
        "os_time_local_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for name in ("temp_in", "hum_in", "temp_out", "hum_out", "cpu_temp"):
        fmt = _hum if name.startswith("hum") else _temp
        m = env[name]
        data[name], data[f"{name}_min"], data[f"{name}_max"] = fmt(m.value), fmt(m.min), fmt(m.max)
    data = {k: data[k] for k in (
        "time", "os_timestamp", "os_time_local_str",
        "temp_in", "temp_in_min", "temp_in_max", "hum_in", "hum_in_min", "hum_in_max",
        "temp_out", "temp_out_min", "temp_out_max", "hum_out", "hum_out_min", "hum_out_max",
        "cpu_temp", "cpu_temp_min", "cpu_temp_max")}
    data.update({
        "state": st.state.value,
        "override": st.state.value if st.override else "off",
        "door_position_estimate": str(round(st.position, 4)) if st.position is not None else "-1",
        "uptime": app.system.uptime(),
        "sunrise": _clock_time(st.sunrise),
        "sunset": _clock_time(st.sunset),
        "tu_open": tu_open,
        "tu_close": tu_close,
        "reference_door_endstops_ms": str(st.reference_ms) if st.reference_ms else "Not set",
        "auto_mode": _flag(s.mode is Mode.AUTO),
        "errorstate": st.error or "",
        "camera_enabled": _flag(s.enable_camera),
        "cpu_percent": str(round(metrics["cpu_percent"], 1)),
        "ram_used_mb": str(round(metrics["ram_used_mb"], 0)),
        "ram_total_mb": str(round(metrics["ram_total_mb"], 0)),
        "ram_percent": str(round(metrics["ram_percent"], 1)),
        "disk_used_gb": str(round(metrics["disk_used_gb"], 1)),
        "disk_total_gb": str(round(metrics["disk_total_gb"], 1)),
        "disk_percent": str(round(metrics["disk_percent"], 1)),
        "python_version": sys.version.split()[0],
        "timer_mode": _flag(s.mode is Mode.TIMER),
        "timer_open_time": s.timer_open_time,
        "timer_close_time": s.timer_close_time,
    })
    return data


_PIN_INFO = (
    ("motor_in1", "Motor UP", "OUT"),
    ("motor_in2", "Motor DOWN", "OUT"),
    ("motor_ena", "Motor Enable", "OUT"),
    ("endstop_up", "Endstop UP", "IN"),
    ("endstop_down", "Endstop DOWN", "IN"),
    ("override_open", "Manual Open Switch", "IN"),
    ("override_close", "Manual Close Switch", "IN"),
    ("dht22_data", "DHT22 Outdoor Data", "IN"),
    ("dht11_data", "DHT11 Indoor Data", "IN"),
    ("dht22_power", "DHT22 Outdoor Power", "OUT"),
)


def _jsonable(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S %Z")
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return value.value  # enums
    return value


def debug_payload(app: "Application") -> dict:
    s = app.settings
    pins = []
    for name, purpose, direction in sorted(_PIN_INFO, key=lambda p: getattr(s.gpio, p[0]) or -1):
        pin = getattr(s.gpio, name)
        if pin is None:
            continue
        # Reading DHT data lines would disturb the sensor protocol.
        readable = name not in ("dht11_data", "dht22_data")
        try:
            state = ("HIGH" if app.hardware.gpio.read(pin) else "LOW") if readable else "N/A"
        except Exception:
            state = "N/A"
        pins.append({"pin": pin, "name": name, "purpose": purpose, "direction": direction,
                     "state": state, "mode": direction})

    door_constants = {
        "in1 (Motor UP)": s.gpio.motor_in1,
        "in2 (Motor DOWN)": s.gpio.motor_in2,
        "ena (Motor Enable)": s.gpio.motor_ena,
        "end_up (Endstop UP)": s.gpio.endstop_up,
        "end_down (Endstop DOWN)": s.gpio.endstop_down,
        "o_pin (Manual Open)": s.gpio.override_open,
        "c_pin (Manual Close)": s.gpio.override_close,
        "invert_end_up": app.driver.pins.invert_end_up,
        "invert_end_down": app.driver.pins.invert_end_down,
        "referenceSequenceTimeout": app.driver.pins.reference_timeout,
        "move_margin_s": app.controller.MOVE_MARGIN_S,
        "premature_close_threshold": app.controller.PREMATURE_CLOSE_THRESHOLD,
        "premature_close_max_retries": app.controller.PREMATURE_CLOSE_MAX_RETRIES,
    }

    st = app.controller.status
    state: dict = {f"door.{k}": _jsonable(v) for k, v in vars(st).items()}
    settings = s.to_yaml_dict()
    settings["wifi"] = dict(settings["wifi"], password=MASK if s.wifi.password else "",
                            ap_password=MASK if s.wifi.ap_password else "")
    settings["auth"] = {"username": s.auth.username, "enabled": s.auth.enabled}
    for k, v in settings.items():
        state[f"config.{k}"] = v
    for k, v in app.environment.values.items():
        state[f"sensor.{k}"] = {"value": v.value, "min": v.min, "max": v.max}
    state["push.enabled"] = app.notifier.enabled
    state["push.subscriptions"] = len(app.subscriptions.all())
    state["hardware.mock"] = app.hardware.is_mock

    import psutil
    mem = psutil.virtual_memory()
    system = {
        "os_name": os.name,
        "platform": sys.platform,
        "python_version": sys.version,
        "uptime": app.system.uptime(),
        "cpu_percent": psutil.cpu_percent(interval=0),
        "memory_total_mb": round(mem.total / (1024 * 1024), 1),
        "memory_used_mb": round(mem.used / (1024 * 1024), 1),
        "memory_percent": mem.percent,
        "version": app.system.version(),
    }
    workers = {w.name: w for w in app.workers}
    threads = [{"name": t.name, "daemon": t.daemon, "alive": t.is_alive()} for t in threading.enumerate()
               if t.name not in workers]
    threads += [{"name": f"worker:{w.name}", "daemon": True, "alive": w.alive, "errors": w.errors}
                for w in app.workers]
    return {
        "pins": pins,
        "door_constants": door_constants,
        "global_vars": state,
        "system": system,
        "threads": threads,
        "logs": list(app.log_buffer.lines),
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
    }
