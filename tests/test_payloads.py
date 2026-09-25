"""Dashboard / debug payloads — exact field names and formats the frontend reads."""

from __future__ import annotations

from conftest import at
from coop.web.payloads import dashboard_payload, debug_payload

DASHBOARD_KEYS = [
    "time", "os_timestamp", "os_time_local_str",
    "temp_in", "temp_in_min", "temp_in_max", "hum_in", "hum_in_min", "hum_in_max",
    "temp_out", "temp_out_min", "temp_out_max", "hum_out", "hum_out_min", "hum_out_max",
    "cpu_temp", "cpu_temp_min", "cpu_temp_max",
    "state", "override", "door_position_estimate", "uptime", "sunrise", "sunset", "tu_open", "tu_close",
    "reference_door_endstops_ms", "auto_mode", "errorstate", "camera_enabled",
    "cpu_percent", "ram_used_mb", "ram_total_mb", "ram_percent", "disk_used_gb", "disk_total_gb", "disk_percent",
    "python_version", "timer_mode", "timer_open_time", "timer_close_time",
    "mode", "open_time", "close_time", "door_desired", "override_active", "retry_pending", "retry_count",
    "retry_max", "reference_running", "hardware_mock", "events",
]


def test_keys_and_order(app):
    assert list(dashboard_payload(app)) == DASHBOARD_KEYS


def test_empty_values(app):
    app.controller.step()  # no reference → manual mode
    d = dashboard_payload(app)
    assert (d["temp_in"], d["state"], d["override"], d["door_position_estimate"], d["errorstate"],
            d["reference_door_endstops_ms"], d["tu_open"], d["auto_mode"], d["timer_mode"], d["camera_enabled"]) == \
           ("", "stopped", "off", "-1", "", "Not set", "disabled", "False", "False", "False")


def test_formatting(app):
    app.environment.poll()
    app.controller.step()
    d = dashboard_payload(app)
    assert (d["temp_in"], d["temp_in_min"], d["hum_in"], d["temp_out"], d["cpu_temp"], d["hum_out"]) == \
           ("21.5°C", "21.5°C", "45.2%", "4.0°C", "51.2°C", "80.0%")
    assert (d["cpu_percent"], d["ram_used_mb"], d["disk_total_gb"]) == ("12.3", "512.0", "29.9")
    assert d["uptime"].endswith("second(s)")
    assert d["time"].count(":") == 2 and isinstance(d["os_timestamp"], int)


def test_door_fields(make_app):
    app = make_app({"auto_mode": True, "reference_door_endstops_ms": 8000.5, "enable_camera": True}, now=at(12))
    app.hardware.gpio.set_input(app.settings.gpio.endstop_up, True)
    app.controller.step()
    d = dashboard_payload(app)
    assert (d["state"], d["door_position_estimate"], d["reference_door_endstops_ms"], d["auto_mode"],
            d["camera_enabled"]) == ("open", "1.0", "8000.5", "True", "True")
    assert d["sunrise"].endswith("AM") and not d["sunrise"].startswith("0")
    assert d["tu_open"] == "passed" and d["tu_close"].count(":") == 2


def test_override_and_error(app):
    app.hardware.gpio.set_input(app.settings.gpio.override_open, True)
    app.controller.step()
    assert dashboard_payload(app)["override"] == "opening"
    app.controller.inject_test_error()
    app.controller.step()
    d = dashboard_payload(app)
    assert d["errorstate"] == "Test Error" and d["state"] == "stopped"


def test_timer_countdown(make_app):
    app = make_app({"timer_mode": True, "auto_mode": False, "reference_door_endstops_ms": 5000,
                    "timer_open_time": "13:30", "timer_close_time": "20:00"}, now=at(12))
    app.controller.step()
    d = dashboard_payload(app)
    assert (d["tu_open"], d["tu_close"], d["timer_mode"]) == ("01:30:00", "08:00:00", "True")


def test_debug_payload(app):
    app.config.update(wifi=app.settings.wifi.merged({"password": "secretpw"}))
    app.controller.step()
    d = debug_payload(app)
    assert set(d) == {"pins", "door_constants", "global_vars", "system", "threads", "logs", "timestamp"}
    pins = [p["pin"] for p in d["pins"]]
    assert pins == sorted(pins)
    assert {p["name"] for p in d["pins"]} >= {"motor_in1", "endstop_up", "dht22_power"}
    assert next(p for p in d["pins"] if p["name"] == "dht11_data")["state"] == "N/A"
    assert d["door_constants"]["in1 (Motor UP)"] == 17
    g = d["global_vars"]
    assert g["door.state"] == "stopped" and g["config.wifi"]["password"] == "********"
    assert "password_hash" not in str(g["config.auth"])
    assert "secretpw" not in str(d)
    assert d["system"]["version"] == "abc1234"
