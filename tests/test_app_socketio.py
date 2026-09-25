"""Socket.IO event handler tests for ``src/app.py``.

Uses Flask-SocketIO's test client, which runs handlers synchronously and
collects server emits so they can be asserted on.
"""

from datetime import datetime

import pytest
import ruamel.yaml as YAML

from protected_dict import protected_dict as gv


def gvals(*keys):
    return gv.instance().get_values(list(keys))


def received(sc, name):
    return [m["args"] for m in sc.get_received() if m["name"] == name]


@pytest.fixture
def config_loaded(app_env):
    app_env.load_config()
    return app_env


def saved_config(tmp_path):
    with open(tmp_path / "config.yaml") as f:
        return YAML.YAML(typ="safe").load(f)


# ── connection ───────────────────────────────────────────────────────────────

def test_connect_replays_log_buffer(app_env):
    app_env.log_buffer.extend(["line 1", "line 2"])
    sc = app_env.socketio.test_client(app_env.app)
    logs = [m["args"][0]["message"] for m in sc.get_received() if m["name"] == "log"]
    assert logs == ["line 1", "line 2"]
    sc.disconnect()


# ── manual door commands ─────────────────────────────────────────────────────

@pytest.mark.parametrize("event, desired", [("open", "open"), ("close", "closed")])
def test_open_close_switch_to_manual_mode(sio_client, event, desired):
    gv.instance().set_values({"auto_mode": "True", "timer_mode": "True"})
    sio_client.emit(event)
    assert gvals("desired_door_state", "auto_mode", "timer_mode") == [desired, "False", "False"]


@pytest.mark.parametrize("event", ["open", "close", "stop"])
def test_manual_commands_persist_manual_mode(config_loaded, sio_client, tmp_path, event):
    gv.instance().set_values({"auto_mode": "True", "timer_mode": "True"})
    sio_client.emit(event)
    cfg = saved_config(tmp_path)
    assert (cfg["auto_mode"], cfg["timer_mode"]) == ("False", "False")


def test_stop_switches_to_manual_mode(config_loaded, sio_client):
    gv.instance().set_values({"auto_mode": "True", "timer_mode": "True"})
    sio_client.emit("stop")
    assert gvals("desired_door_state", "auto_mode", "timer_mode") == ["stopped", "False", "False"]


# ── mode toggles ─────────────────────────────────────────────────────────────

def test_toggle_auto_on_disables_timer_and_persists(config_loaded, sio_client, tmp_path):
    gv.instance().set_value("timer_mode", "True")
    sio_client.emit("toggle", {"toggle": True})
    assert gvals("auto_mode", "timer_mode") == ["True", "False"]
    cfg = saved_config(tmp_path)
    assert (cfg["auto_mode"], cfg["timer_mode"]) == ("True", "False")


def test_toggle_auto_off(config_loaded, sio_client, tmp_path):
    sio_client.emit("toggle", {"toggle": False})
    assert gvals("auto_mode") == ["False"]
    assert saved_config(tmp_path)["auto_mode"] == "False"


def test_toggle_timer_on_disables_auto(config_loaded, sio_client, tmp_path):
    sio_client.emit("toggle_timer", {"toggle": True})
    assert gvals("auto_mode", "timer_mode") == ["False", "True"]
    assert saved_config(tmp_path)["timer_mode"] == "True"


def test_toggle_timer_off_leaves_auto(config_loaded, sio_client):
    gv.instance().set_value("auto_mode", "True")
    sio_client.emit("toggle_timer", {"toggle": False})
    assert gvals("auto_mode", "timer_mode") == ["True", "False"]


# ── settings ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    {"timer_open_time": "06:30", "timer_close_time": "21:15"},
    {"open_time": "06:30", "close_time": "21:15"},
])
def test_timer_times_accepts_both_key_styles(config_loaded, sio_client, tmp_path, payload):
    sio_client.emit("timer_times", payload)
    assert gvals("timer_open_time", "timer_close_time") == ["06:30", "21:15"]
    assert saved_config(tmp_path)["timer_open_time"] == "06:30"


def test_timer_times_requires_both(config_loaded, sio_client):
    sio_client.emit("timer_times", {"timer_open_time": "06:30"})
    assert gvals("timer_open_time") == ["07:00"]


def test_auto_offsets_are_cast_to_int(config_loaded, sio_client, tmp_path):
    sio_client.emit("auto_offsets", {"sunrise_offset": "-15", "sunset_offset": "30"})
    assert gvals("sunrise_offset", "sunset_offset") == [-15, 30]
    assert saved_config(tmp_path)["sunset_offset"] == 30


def test_update_location(config_loaded, sio_client, tmp_path):
    loc = {"city": "Berlin", "region": "Germany", "timezone": "Europe/Berlin",
           "latitude": 52.52, "longitude": 13.40}
    sio_client.emit("update_location", loc)
    assert gvals("location") == [loc]
    assert saved_config(tmp_path)["location"]["city"] == "Berlin"
    assert config_loaded.boulder.name == "Berlin"
    assert str(config_loaded.timezone) == "Europe/Berlin"


# ── flags consumed by the door task ──────────────────────────────────────────

@pytest.mark.parametrize("event, key", [
    ("reference_endstops", "toggle_reference_of_endstops"),
    ("clear_error", "clear_error_state"),
    ("generate_error", "debug_error"),
])
def test_door_task_flags(sio_client, event, key):
    sio_client.emit(event)
    assert gvals(key) == [True]


# ── CSV data ─────────────────────────────────────────────────────────────────

def test_get_csv_data_emits_todays_file(app_env, sio_client):
    import os
    path = app_env.get_log_file_name()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# a, b\n1, 2\n")
    sio_client.emit("get_csv_data")
    assert received(sio_client, "csv_data") == [[["# a, b\n", "1, 2\n"]]]


def test_get_csv_data_without_file_emits_nothing(sio_client):
    sio_client.emit("get_csv_data")
    assert received(sio_client, "csv_data") == []


# ── debug panel ──────────────────────────────────────────────────────────────

def test_get_debug_data_payload(config_loaded, sio_client):
    gv.instance().set_values({
        "vapid_private_key": "SECRET", "vapid_public_key": None,
        "sunrise": datetime(2025, 1, 1, 7, 0), "some_date": datetime(2025, 1, 1).date(),
    })
    sio_client.emit("get_debug_data")
    [[payload]] = received(sio_client, "debug_data")

    assert set(payload) == {"pins", "door_constants", "global_vars", "system", "threads", "logs", "timestamp"}
    pin_numbers = [p["pin"] for p in payload["pins"]]
    assert pin_numbers == sorted(pin_numbers)
    assert {p["name"] for p in payload["pins"]} >= {"motor_in1", "endstop_up", "dht22_power"}
    assert all(p["state"] in ("HIGH", "LOW", "N/A") for p in payload["pins"])
    assert payload["door_constants"]["in1 (Motor UP)"] == 17
    assert payload["global_vars"]["vapid_private_key"] == "***"
    assert payload["global_vars"]["vapid_public_key"] is None
    assert payload["global_vars"]["sunrise"].startswith("2025-01-01 07:00:00")
    assert payload["global_vars"]["some_date"] == "2025-01-01"
    assert payload["system"]["os_name"] == "posix"
    assert any(t["name"] == "MainThread" for t in payload["threads"])


# ── Windows-only mock handlers ───────────────────────────────────────────────

def test_mock_handlers_are_noops_off_windows(sio_client):
    sio_client.emit("mock_trigger_pin", {"pin": 23, "state": "HIGH"})
    sio_client.emit("mock_get_outputs")
    assert received(sio_client, "mock_update_outputs") == []
