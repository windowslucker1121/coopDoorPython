"""Socket.IO event contract (grid_dashboard.html, debug.html, mock.html)."""

from __future__ import annotations

import pytest
import ruamel.yaml as YAML

from conftest import received
from coop.config import Mode
from coop.door.model import DesiredState


def saved(app):
    with open(app.paths.config) as f:
        return YAML.YAML(typ="safe").load(f)


def test_connect_replays_log_buffer_to_that_client_only(app, web):
    flask_app, socketio = web
    other = socketio.test_client(flask_app)
    other.get_received()
    app.log_buffer.lines.extend(["one", "two"])
    sc = socketio.test_client(flask_app)
    assert [m[0]["message"] for m in received(sc, "log")] == ["one", "two"]
    assert received(other, "log") == []


@pytest.mark.parametrize("event, desired", [("open", DesiredState.OPEN), ("close", DesiredState.CLOSED),
                                            ("stop", DesiredState.STOPPED)])
def test_manual_commands_switch_to_persisted_manual_mode(app, sio, event, desired):
    app.config.update(mode=Mode.AUTO)
    sio.emit(event)
    assert app.settings.mode is Mode.MANUAL
    assert saved(app)["auto_mode"] is False
    app.controller.step()
    assert app.controller.desired is desired


def test_toggles(app, sio):
    app.config.update(mode=Mode.MANUAL)
    sio.emit("toggle", {"toggle": True})
    assert app.settings.mode is Mode.AUTO
    sio.emit("toggle_timer", {"toggle": True})
    assert app.settings.mode is Mode.TIMER
    sio.emit("toggle", {"toggle": False})  # auto is not on → unchanged
    assert app.settings.mode is Mode.TIMER
    sio.emit("toggle_timer", {"toggle": False})
    assert app.settings.mode is Mode.MANUAL
    assert saved(app)["timer_mode"] is False
    sio.emit("toggle", {})
    sio.emit("toggle")
    assert app.settings.mode is Mode.MANUAL


@pytest.mark.parametrize("payload, expected", [
    ({"open_time": "6:05", "close_time": "21:15:00"}, ("06:05", "21:15")),
    ({"timer_open_time": "05:00", "timer_close_time": "22:00"}, ("05:00", "22:00")),
    ({"open_time": "25:00", "close_time": "20:00"}, ("07:00", "20:00")),
    ({"open_time": "06:00"}, ("07:00", "20:00")),
    ("garbage", ("07:00", "20:00")),
])
def test_timer_times(app, sio, payload, expected):
    sio.emit("timer_times", payload)
    assert (app.settings.timer_open_time, app.settings.timer_close_time) == expected


@pytest.mark.parametrize("payload, expected", [
    ({"sunrise_offset": "-15", "sunset_offset": 30}, (-15, 30)),
    ({"sunrise_offset": "abc", "sunset_offset": 0}, (0, 0)),
    ({"sunrise_offset": 5000, "sunset_offset": 0}, (0, 0)),
    ({"sunrise_offset": 5}, (0, 0)),
])
def test_offsets(app, sio, payload, expected):
    sio.emit("auto_offsets", payload)
    assert (app.settings.sunrise_offset, app.settings.sunset_offset) == expected


def test_update_location(app, sio):
    sio.emit("update_location", {"city": "Berlin", "region": "Germany", "timezone": "Europe/Berlin",
                                 "latitude": 52.52, "longitude": 13.4})
    assert app.settings.location.city == "Berlin"
    assert app.sun.location.timezone == "Europe/Berlin"
    assert saved(app)["location"]["city"] == "Berlin"


@pytest.mark.parametrize("payload", [
    {"city": "X", "region": "Y", "timezone": "Mars/Base", "latitude": 1, "longitude": 1},
    {"city": "X", "region": "Y", "timezone": "Europe/Berlin", "latitude": None, "longitude": 1},
    None,
])
def test_invalid_location_ignored(app, sio, payload):
    sio.emit("update_location", payload)
    assert app.settings.location.city == "Boulder"


@pytest.mark.parametrize("event, attr", [("reference_endstops", "reference"), ("clear_error", "clear_error"),
                                         ("generate_error", "test_error")])
def test_controller_requests(app, sio, event, attr):
    sio.emit(event)
    assert getattr(app.controller._commands, attr) is True


def test_csv_data(app, sio):
    from coop.services.datalog import csv_file_name
    import os
    path = csv_file_name(app.paths.log_dir, app.clock.now())
    sio.emit("get_csv_data")
    assert received(sio, "csv_data") == []
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("# a\n1\n")
    sio.emit("get_csv_data")
    assert received(sio, "csv_data") == [[["# a\n", "1\n"]]]


def test_debug_data_goes_to_requester(app, web):
    flask_app, socketio = web
    a, b = socketio.test_client(flask_app), socketio.test_client(flask_app)
    a.get_received(); b.get_received()
    a.emit("get_debug_data")
    assert len(received(a, "debug_data")) == 1
    assert received(b, "debug_data") == []


def test_mock_panel_events(app, sio):
    sio.emit("mock_trigger_pin", {"pin": 23, "state": "HIGH"})
    assert app.hardware.gpio.read(23) is True
    sio.emit("mock_trigger_pin", {"pin": "x"})
    sio.emit("mock_trigger_pin", {"pin": 23, "state": "LOW"})
    app.driver.open()
    sio.emit("mock_get_outputs")
    [[outputs]] = received(sio, "mock_update_outputs")
    assert outputs == {"17": "LOW", "27": "LOW", "22": "HIGH"}  # opening


def test_mock_events_ignored_on_real_hardware(app, sio):
    app.hardware.gpio.is_mock = False
    sio.emit("mock_trigger_pin", {"pin": 23, "state": "HIGH"})
    sio.emit("mock_get_outputs")
    assert app.hardware.gpio.read(23) is False
    assert received(sio, "mock_update_outputs") == []
