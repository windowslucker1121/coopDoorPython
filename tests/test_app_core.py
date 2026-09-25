"""Tests for the non-HTTP parts of ``src/app.py``: configuration persistence,
location handling, the dashboard data snapshot, background tasks and Web
Push delivery.

See ``conftest.py`` for the ``app_env`` fixture that isolates the module
from the real filesystem / network.
"""

import json
import os
from datetime import datetime, date, timedelta
from unittest import mock

import pytest
import pytz
import ruamel.yaml as YAML

from protected_dict import protected_dict as gv


class StopLoop(Exception):
    """Raised from a patched ``time.sleep`` to break out of ``while True``."""


def sleep_breaker(max_calls=1):
    calls = {"n": 0}

    def _sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= max_calls:
            raise StopLoop()

    return _sleep


def read_yaml(path):
    with open(path) as f:
        return YAML.YAML(typ="safe").load(f)


# ── configuration ────────────────────────────────────────────────────────────

class TestConfig:

    def test_load_without_file_applies_defaults_and_writes_file(self, app_env, tmp_path):
        app_env.load_config()
        store = gv.instance()
        assert store.get_value("auto_mode") == "True"
        assert store.get_value("timer_mode") == "False"
        assert store.get_value("timer_open_time") == "07:00"
        assert store.get_value("timer_close_time") == "20:00"
        assert store.get_value("sunrise_offset") == 0
        assert store.get_value("location")["city"] == "Boulder"
        assert store.get_value("outdoor_sensor_type") == "dht22"
        assert store.get_value("csvLog") is True
        assert store.get_value("enable_camera") is False
        assert store.get_value("gpio") == app_env.GPIO_DEFAULTS
        assert store.get_value("wifi") == app_env.WIFI_DEFAULTS

        on_disk = read_yaml(tmp_path / "config.yaml")
        assert on_disk["auto_mode"] == "True"
        assert on_disk["gpio"]["motor_in1"] == 17

    def test_load_merges_partial_file_with_defaults(self, app_env, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "auto_mode: 'False'\n"
            "sunset_offset: 15\n"
            "gpio:\n  motor_in1: 4\n"
            "wifi:\n  ssid: Home\n"
            "extra_key: 1\n"
        )
        app_env.load_config()
        store = gv.instance()
        assert store.get_value("auto_mode") == "False"
        assert store.get_value("sunset_offset") == 15
        assert store.get_value("timer_mode") == "False"  # default kept
        gpio = store.get_value("gpio")
        assert gpio["motor_in1"] == 4 and gpio["motor_in2"] == 27
        wifi = store.get_value("wifi")
        assert wifi["ssid"] == "Home" and wifi["ap_ssid"] == "DINKY-COOP"
        assert store.get_value("extra_key") == 1  # unknown keys are loaded too
        # An existing file is never rewritten by load_config
        assert "extra_key" in (tmp_path / "config.yaml").read_text()

    def test_save_writes_only_known_keys(self, app_env, tmp_path):
        app_env.load_config()
        gv.instance().set_values({"auto_mode": "False", "sunrise_offset": -10, "not_persisted": 1})
        app_env.save_config()
        on_disk = read_yaml(tmp_path / "config.yaml")
        assert on_disk["auto_mode"] == "False"
        assert on_disk["sunrise_offset"] == -10
        assert "not_persisted" not in on_disk
        assert set(on_disk) == {
            "use_mock_hardware", "auto_mode", "timer_mode", "timer_open_time",
            "timer_close_time", "sunrise_offset", "sunset_offset", "location",
            "consoleLogToFile", "csvLog", "enable_camera", "camera_index",
            "outdoor_sensor_type", "gpio", "wifi",
        }

    def test_save_load_round_trip(self, app_env):
        app_env.load_config()
        gv.instance().set_value("timer_open_time", "06:15")
        app_env.save_config()
        gv.reset_for_testing()
        app_env.load_config()
        assert gv.instance().get_value("timer_open_time") == "06:15"

    def test_load_notification_keys(self, app_env, tmp_path):
        (tmp_path / ".secrets.yaml").write_text(
            "secrets:\n  vapid_public_key: PUB\n  vapid_private_key: PRIV\n")
        app_env.load_notification_keys()
        assert gv.instance().get_values(["vapid_public_key", "vapid_private_key"]) == ["PUB", "PRIV"]

    def test_load_notification_keys_missing_file(self, app_env):
        app_env.load_notification_keys()
        assert gv.instance().get_value("vapid_public_key") is None


# ── location / sun ───────────────────────────────────────────────────────────

class TestLocation:

    def test_reload_location_data_updates_globals(self, app_env):
        gv.instance().set_value("location", {
            "city": "Berlin", "region": "Germany", "timezone": "Europe/Berlin",
            "latitude": 52.52, "longitude": 13.40,
        })
        app_env.reload_location_data()
        assert app_env.boulder.name == "Berlin"
        assert str(app_env.timezone) == "Europe/Berlin"

    def test_sunrise_before_sunset_in_configured_timezone(self, app_env):
        sunrise, sunset = app_env.get_sunrise_and_sunset()
        assert sunrise < sunset
        assert str(sunrise.tzinfo.zone) == "America/Denver"

    def test_get_current_time_is_localized(self, app_env):
        now = app_env.get_current_time()
        assert now.tzinfo is not None

    def test_valid_locations_are_sorted_and_complete(self, app_env):
        locations = app_env.get_valid_locations()
        assert len(locations) > 100
        assert locations == sorted(locations, key=lambda x: (x["name"], x["region"]))
        assert set(locations[0]) == {"name", "region", "timezone", "latitude", "longitude"}
        # Names are "<group> - <city>" in lower case, e.g. "europe - berlin"
        assert any(l["name"] == "europe - berlin" for l in locations)

    def test_get_uptime_format(self, app_env):
        assert app_env.get_uptime().endswith("second(s)")
        assert "day(s)" in app_env.get_uptime()


# ── get_all_data ─────────────────────────────────────────────────────────────

class TestGetAllData:

    def test_empty_store_yields_blank_fields(self, app_env):
        data = app_env.get_all_data()
        assert data["temp_in"] == ""
        assert data["hum_out"] == ""
        assert data["state"] == ""
        assert data["override"] == "off"
        assert data["sunrise"] == ""
        assert data["tu_open"] == ""
        assert data["reference_door_endstops_ms"] == "Not set"
        assert data["door_position_estimate"] == "-1"
        assert data["camera_enabled"] == "None"

    def test_expected_keys(self, app_env):
        assert set(app_env.get_all_data()) == {
            "time", "os_timestamp", "os_time_local_str",
            "temp_in", "temp_in_min", "temp_in_max", "hum_in", "hum_in_min", "hum_in_max",
            "temp_out", "temp_out_min", "temp_out_max", "hum_out", "hum_out_min", "hum_out_max",
            "cpu_temp", "cpu_temp_min", "cpu_temp_max",
            "state", "override", "door_position_estimate", "uptime", "sunrise", "sunset",
            "tu_open", "tu_close", "reference_door_endstops_ms", "auto_mode", "errorstate",
            "camera_enabled", "cpu_percent", "ram_used_mb", "ram_total_mb", "ram_percent",
            "disk_used_gb", "disk_total_gb", "disk_percent", "python_version",
            "timer_mode", "timer_open_time", "timer_close_time",
        }

    def test_formats_temperatures_and_humidity(self, app_env):
        gv.instance().set_values({
            "temp_in": 212.0, "temp_out": 32.0, "hum_in": 45.25, "cpu_temp": 51.23,
        })
        data = app_env.get_all_data()
        assert data["temp_in"] == "100.0°C"   # stored as °F, shown as °C
        assert data["temp_out"] == "0.0°C"
        assert data["hum_in"] == "45.2%"
        assert data["cpu_temp"] == "51.2°C"   # CPU temp is already °C

    def test_override_shows_state_only_when_active(self, app_env):
        gv.instance().set_values({"state": "opening", "override": True})
        assert app_env.get_all_data()["override"] == "opening"
        gv.instance().set_value("override", False)
        assert app_env.get_all_data()["override"] == "off"

    def test_misc_fields(self, app_env):
        gv.instance().set_values({
            "reference_door_endstops_ms": 1234.5, "door_position_estimate": 0.5,
            "error_state": "jam", "enable_camera": True,
            "timer_mode": "True", "timer_open_time": "07:00", "timer_close_time": "19:00",
        })
        data = app_env.get_all_data()
        assert data["reference_door_endstops_ms"] == "1234.5"
        assert data["door_position_estimate"] == "0.5"
        assert data["errorstate"] == "jam"
        assert data["camera_enabled"] == "True"
        assert (data["timer_mode"], data["timer_open_time"], data["timer_close_time"]) == ("True", "07:00", "19:00")

    def test_auto_mode_disabled_countdowns(self, app_env):
        gv.instance().set_value("auto_mode", "False")
        data = app_env.get_all_data()
        assert data["tu_open"] == data["tu_close"] == "disabled"

    def test_countdowns_future_and_passed(self, app_env, monkeypatch):
        tz = pytz.timezone("America/Denver")
        now = tz.localize(datetime(2025, 6, 1, 12, 0, 0))
        monkeypatch.setattr(app_env, "get_current_time", lambda: now)
        gv.instance().set_values({
            "auto_mode": "True",
            "sunrise": now - timedelta(hours=6),
            "sunset": now + timedelta(hours=8, minutes=30),
            "sunrise_offset": 0, "sunset_offset": 15,
        })
        data = app_env.get_all_data()
        assert data["tu_open"] == "passed"
        assert data["tu_close"] == "08:45:00"
        assert data["sunrise"] == "6:00:00 AM"
        assert data["sunset"] == "8:30:00 PM"


# ── temperature task ─────────────────────────────────────────────────────────

class ScriptedSensor:
    def __init__(self, readings):
        self.readings = list(readings)

    def get_temperature_and_humidity(self):
        return self.readings.pop(0) if self.readings else (None, None)


def run_temperature_task(app_env, monkeypatch, indoor, outdoor, cpu, iterations):
    monkeypatch.setattr(app_env, "DHT11", lambda pin: ScriptedSensor(indoor))
    monkeypatch.setattr(app_env, "DHT22", lambda pin, power_pin=None: ScriptedSensor(outdoor))
    cpu_values = list(cpu)
    monkeypatch.setattr(app_env, "CPUTemperature",
                        lambda: mock.Mock(temperature=cpu_values.pop(0) if cpu_values else None))
    monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(iterations))
    with pytest.raises(StopLoop):
        app_env.temperature_task()


class TestTemperatureTask:

    def test_first_reading_sets_value_min_max(self, app_env, monkeypatch):
        run_temperature_task(app_env, monkeypatch,
                             indoor=[(70.0, 40.0)], outdoor=[(50.0, 60.0)], cpu=[45.0], iterations=1)
        store = gv.instance()
        assert store.get_values(["temp_in", "temp_in_min", "temp_in_max"]) == [70.0, 70.0, 70.0]
        assert store.get_values(["temp_out", "hum_out", "hum_in", "cpu_temp"]) == [50.0, 60.0, 40.0, 45.0]

    def test_min_max_track_readings(self, app_env, monkeypatch):
        run_temperature_task(app_env, monkeypatch,
                             indoor=[(70.0, 40), (72.0, 41), (69.0, 39)],
                             outdoor=[], cpu=[], iterations=3)
        assert gv.instance().get_values(["temp_in", "temp_in_min", "temp_in_max"]) == [69.0, 69.0, 72.0]

    def test_spike_filter_rejects_single_outliers(self, app_env, monkeypatch):
        run_temperature_task(app_env, monkeypatch,
                             indoor=[(70.0, 40), (90.0, 40), (71.0, 40)],
                             outdoor=[], cpu=[], iterations=3)
        store = gv.instance()
        assert store.get_value("temp_in") == 71.0
        assert store.get_value("temp_in_max") == 71.0

    def test_spike_filter_accepts_third_consecutive_outlier(self, app_env, monkeypatch):
        run_temperature_task(app_env, monkeypatch,
                             indoor=[(70.0, 40), (90.0, 40), (90.0, 40), (90.0, 40)],
                             outdoor=[], cpu=[], iterations=4)
        store = gv.instance()
        assert store.get_value("temp_in") == 90.0
        assert store.get_value("temp_in_max") == 90.0

    def test_none_readings_are_ignored(self, app_env, monkeypatch):
        run_temperature_task(app_env, monkeypatch,
                             indoor=[(70.0, 40), (None, None)], outdoor=[], cpu=[], iterations=2)
        assert gv.instance().get_value("temp_in") == 70.0

    def test_sensor_exception_does_not_kill_task(self, app_env, monkeypatch):
        class Broken:
            def get_temperature_and_humidity(self):
                raise RuntimeError("sensor gone")
        monkeypatch.setattr(app_env, "DHT11", lambda pin: Broken())
        monkeypatch.setattr(app_env, "DHT22", lambda pin, power_pin=None: Broken())
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(2))
        with pytest.raises(StopLoop):
            app_env.temperature_task()

    def test_api_outdoor_sensor_is_selected(self, app_env, monkeypatch):
        gv.instance().set_value("outdoor_sensor_type", "api")
        created = {}

        class FakeAPISensor:
            def __init__(self, get_location):
                created["get_location"] = get_location

            def get_temperature_and_humidity(self):
                return 40.0, 80.0

        monkeypatch.setattr(app_env, "LocationAPITemperatureSensor", FakeAPISensor)
        monkeypatch.setattr(app_env, "DHT22", mock.Mock(side_effect=AssertionError("DHT22 used")))
        monkeypatch.setattr(app_env, "DHT11", lambda pin: ScriptedSensor([(70.0, 40.0)]))
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(1))
        with pytest.raises(StopLoop):
            app_env.temperature_task()
        assert gv.instance().get_value("temp_out") == 40.0
        gv.instance().set_value("location", {"latitude": 1})
        assert created["get_location"]() == {"latitude": 1}

    def test_gpio_config_pins_are_used(self, app_env, monkeypatch):
        gv.instance().set_value("gpio", {"dht11_data": 26, "dht22_data": 16, "dht22_power": None})
        seen = {}
        monkeypatch.setattr(app_env, "DHT11", lambda pin: seen.setdefault("in", pin) and ScriptedSensor([]))
        monkeypatch.setattr(app_env, "DHT22",
                            lambda pin, power_pin=None: seen.update(out=pin, pwr=power_pin) or ScriptedSensor([]))
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(1))
        with pytest.raises(StopLoop):
            app_env.temperature_task()
        assert seen == {"in": 26, "out": 16, "pwr": None}


# ── data broadcast / CSV logging ─────────────────────────────────────────────

class TestDataTasks:

    def test_data_update_task_emits_snapshot(self, app_env, monkeypatch):
        emit = mock.Mock()
        monkeypatch.setattr(app_env.socketio, "emit", emit)
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(2))
        with pytest.raises(StopLoop):
            app_env.data_update_task()
        assert emit.call_count == 2
        event, payload = emit.call_args[0]
        assert event == "data"
        assert "temp_in" in payload

    def test_data_update_task_survives_errors(self, app_env, monkeypatch):
        monkeypatch.setattr(app_env, "get_all_data", mock.Mock(side_effect=RuntimeError))
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(2))
        with pytest.raises(StopLoop):
            app_env.data_update_task()

    def test_log_file_name(self, app_env, tmp_path):
        expected = tmp_path / "log" / (datetime.now().strftime("%Y_%m_%d") + ".csv")
        assert app_env.get_log_file_name() == str(expected)

    def test_data_log_task_writes_header_once_and_rows(self, app_env, monkeypatch, tmp_path):
        monkeypatch.setattr(app_env, "get_all_data", lambda: {"time": "t", "temp_in": "1.0°C"})
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(2))
        with pytest.raises(StopLoop):
            app_env.data_log_task()
        lines = open(app_env.get_log_file_name()).read().splitlines()
        assert lines == ["# time, temp_in", "t, 1.0°C", "t, 1.0°C"]

    def test_data_log_task_appends_to_existing_file(self, app_env, monkeypatch):
        os.makedirs(os.path.dirname(app_env.get_log_file_name()), exist_ok=True)
        with open(app_env.get_log_file_name(), "w") as f:
            f.write("# a\nold\n")
        monkeypatch.setattr(app_env, "get_all_data", lambda: {"a": "new"})
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(1))
        with pytest.raises(StopLoop):
            app_env.data_log_task()
        assert open(app_env.get_log_file_name()).read() == "# a\nold\nnew\n"


# ── camera task ──────────────────────────────────────────────────────────────

class TestCameraTask:

    def test_disabled_camera_returns_immediately(self, app_env, monkeypatch):
        gv.instance().set_value("enable_camera", False)
        cam = mock.Mock()
        monkeypatch.setattr(app_env, "Camera", cam)
        app_env.camera_task()
        cam.assert_not_called()

    def test_frames_are_base64_emitted(self, app_env, monkeypatch):
        gv.instance().set_values({"enable_camera": True, "camera_index": 2})
        cam_cls = mock.Mock()
        cam_cls.return_value.get_frame.return_value = b"\x01\x02"
        emit = mock.Mock()
        monkeypatch.setattr(app_env, "Camera", cam_cls)
        monkeypatch.setattr(app_env.socketio, "emit", emit)
        monkeypatch.setattr(app_env.time, "sleep", sleep_breaker(1))
        with pytest.raises(StopLoop):
            app_env.camera_task()
        cam_cls.assert_called_once_with(device_index=2)
        emit.assert_called_once_with("camera", "AQI=", namespace="/")

    def test_runtime_error_ends_task(self, app_env, monkeypatch):
        gv.instance().set_value("enable_camera", True)
        cam_cls = mock.Mock()
        cam_cls.return_value.get_frame.side_effect = RuntimeError("no frame")
        monkeypatch.setattr(app_env, "Camera", cam_cls)
        app_env.camera_task()  # returns instead of looping forever

    def test_camera_none_setting_is_treated_as_enabled(self, app_env, monkeypatch):
        # Quirk: only an explicit False disables the camera.
        cam_cls = mock.Mock()
        cam_cls.return_value.get_frame.side_effect = RuntimeError
        monkeypatch.setattr(app_env, "Camera", cam_cls)
        app_env.camera_task()
        cam_cls.assert_called_once()


# ── wifi watchdog ────────────────────────────────────────────────────────────

class TestWifiWatchdog:

    @pytest.fixture(autouse=True)
    def no_sleep(self, app_env, monkeypatch):
        monkeypatch.setattr(app_env.time, "sleep", lambda s: None)

    def test_ap_mode_already_active(self, app_env, fake_wifi):
        fake_wifi.ap_mode = True
        fake_wifi.connection = None
        app_env.wifi_watchdog_task()
        assert fake_wifi.connect_calls == [] and fake_wifi.start_ap_calls == []

    def test_already_connected(self, app_env, fake_wifi):
        app_env.wifi_watchdog_task()
        assert fake_wifi.connect_calls == [] and fake_wifi.start_ap_calls == []

    def test_connects_to_configured_ssid(self, app_env, fake_wifi):
        fake_wifi.connection = None
        gv.instance().set_value("wifi", {"ssid": "Home", "password": "pw", "timeout": "45"})
        app_env.wifi_watchdog_task()
        assert fake_wifi.connect_calls == [("Home", "pw", 45)]
        assert fake_wifi.start_ap_calls == []

    def test_failed_connect_falls_back_to_ap(self, app_env, fake_wifi):
        fake_wifi.connection = None
        fake_wifi.connect_result = False
        gv.instance().set_value("wifi", {"ssid": "Home", "password": "pw", "timeout": "bad",
                                         "ap_ssid": "COOP", "ap_password": "secret"})
        app_env.wifi_watchdog_task()
        assert fake_wifi.connect_calls == [("Home", "pw", 60)]
        assert fake_wifi.start_ap_calls == [("COOP", "secret")]

    def test_no_ssid_starts_ap_with_defaults(self, app_env, fake_wifi):
        fake_wifi.connection = None
        app_env.wifi_watchdog_task()
        assert fake_wifi.start_ap_calls == [("DINKY-COOP", "password")]


# ── push notifications ───────────────────────────────────────────────────────

class TestPushNotifications:

    def _write_subs(self, tmp_path, subs):
        (tmp_path / ".subscriptions.json").write_text(json.dumps({"subscriptions": subs}))

    def test_no_private_key_does_nothing(self, app_env, monkeypatch, tmp_path):
        push = mock.Mock()
        monkeypatch.setattr(app_env, "webpush", push)
        self._write_subs(tmp_path, [{"endpoint": "a"}])
        app_env.send_push_notification("t", "b")
        push.assert_not_called()

    def test_no_subscription_file_does_nothing(self, app_env, monkeypatch, tmp_path):
        gv.instance().set_value("vapid_private_key", "KEY")
        push = mock.Mock()
        monkeypatch.setattr(app_env, "webpush", push)
        app_env.send_push_notification("t", "b")
        push.assert_not_called()
        assert not (tmp_path / ".subscriptions.json").exists()

    def test_sends_to_every_subscription(self, app_env, monkeypatch, tmp_path):
        gv.instance().set_value("vapid_private_key", "KEY")
        push = mock.Mock()
        monkeypatch.setattr(app_env, "webpush", push)
        self._write_subs(tmp_path, [{"endpoint": "a"}, {"endpoint": "b"}])

        app_env.send_push_notification("Title", "Body")

        assert push.call_count == 2
        _, kwargs = push.call_args
        assert json.loads(kwargs["data"]) == {"title": "Title", "body": "Body"}
        assert kwargs["vapid_private_key"] == "KEY"
        assert kwargs["timeout"] == 10
        # The private key is cached at module level after the first call
        assert app_env.vapid_private_key == "KEY"

    def test_gone_subscriptions_are_removed(self, app_env, monkeypatch, tmp_path):
        gv.instance().set_value("vapid_private_key", "KEY")

        def push(sub, **kw):
            if sub["endpoint"] == "gone":
                raise app_env.WebPushException("gone", response=mock.Mock(status_code=410))
            if sub["endpoint"] == "flaky":
                raise app_env.WebPushException("err", response=mock.Mock(status_code=500))

        monkeypatch.setattr(app_env, "webpush", push)
        self._write_subs(tmp_path, [{"endpoint": "ok"}, {"endpoint": "gone"}, {"endpoint": "flaky"}])
        app_env.send_push_notification("t", "b")
        saved = json.loads((tmp_path / ".subscriptions.json").read_text())
        assert saved == {"subscriptions": [{"endpoint": "ok"}, {"endpoint": "flaky"}]}

    def test_individual_send_return_values(self, app_env, monkeypatch):
        monkeypatch.setattr(app_env, "webpush", mock.Mock())
        assert app_env.send_individual_push_notification({}, {}, "k", {}) is True
        monkeypatch.setattr(app_env, "webpush", mock.Mock(side_effect=RuntimeError))
        assert app_env.send_individual_push_notification({}, {}, "k", {}) is True
        monkeypatch.setattr(app_env, "webpush",
                            mock.Mock(side_effect=app_env.WebPushException("x", response=None)))
        assert app_env.send_individual_push_notification({}, {}, "k", {}) is True


# ── logging ──────────────────────────────────────────────────────────────────

def test_socketio_log_handler_buffers_and_emits(app_env, monkeypatch):
    import logging
    emit = mock.Mock()
    monkeypatch.setattr(app_env.socketio, "emit", emit)
    handler = app_env.SocketIOHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    handler.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None))
    assert list(app_env.log_buffer) == ["INFO - hello"]
    emit.assert_called_once_with("log", {"message": "INFO - hello"}, namespace="/")


def test_log_buffer_is_bounded(app_env):
    assert app_env.log_buffer.maxlen == 100


def test_configure_logging_creates_log_dir_and_handlers(app_env, tmp_path):
    import logging
    from logging.handlers import TimedRotatingFileHandler
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        app_env.configure_logging()
        assert (tmp_path / "log").is_dir()
        assert any(isinstance(h, app_env.SocketIOHandler) for h in root.handlers)
        assert logging.getLogger("geventwebsocket.handler").level == logging.WARNING
        # Calling twice must not duplicate handlers
        count = len(root.handlers)
        app_env.configure_logging()
        assert len(root.handlers) == count
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
