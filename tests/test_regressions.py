"""Regression tests for backend logic errors found in the full review.

Every test here failed on the code before the fix (verified by running this
file against the previous revision of ``src/``) and documents the bug it
guards against in its docstring.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
from unittest import mock

import pytest
import pytz
import ruamel.yaml as YAML

import door as door_module
from door_task_runner import DoorTaskRunner
from mock_gpio import MockGPIO
from protected_dict import protected_dict as gv
from test_door_task_runner import Harness, gvals, set_pin, TZ, SUNRISE, SUNSET


@pytest.fixture(autouse=True)
def no_hw_sleep(monkeypatch):
    monkeypatch.setattr(door_module, "_hw_sleep", lambda s: None)


def read_yaml(path):
    with open(path) as f:
        return YAML.YAML(typ="safe").load(f)


NIGHT = TZ.localize(datetime(2025, 6, 1, 23, 0))
NOON = TZ.localize(datetime(2025, 6, 1, 12, 0))


# ═════════════════════════════ door control loop ════════════════════════════

class TestScheduleSync:

    def test_boot_at_night_with_open_door_closes_it(self):
        """The drive block used the desired state read *before* the auto
        block ran.  At boot the "stopped" reconcile then overwrote the
        schedule's "closed" with the door's actual "open" → the door stayed
        open all night."""
        h = Harness(auto_mode="True", now=NIGHT)
        set_pin("end_up", MockGPIO.HIGH)  # door physically open
        h.step()
        set_pin("end_up", MockGPIO.LOW)   # door leaves the endstop
        h.step()
        assert gvals("desired_door_state") == ["closed"]
        assert h.door.get_state() == "closing"

    def test_boot_in_timer_mode_with_closed_door_opens_it(self):
        h = Harness(timer_mode="True", now=NOON)
        set_pin("end_down", MockGPIO.HIGH)
        h.step()
        assert gvals("desired_door_state") == ["open"]
        assert h.door.get_state() == "opening"

    def test_enabling_auto_mode_syncs_door_immediately(self):
        """Switching auto mode on mid-day left a closed door closed until
        the *next* window (sunset!) — only boot used to sync the door."""
        h = Harness(now=NOON)
        set_pin("end_down", MockGPIO.HIGH)
        h.step(2)
        assert h.door.get_state() == "closed"
        gv.instance().set_value("auto_mode", "True")
        h.step()
        assert gvals("desired_door_state") == ["open"]
        assert h.door.get_state() == "opening"

    def test_switching_from_auto_to_timer_resyncs(self):
        h = Harness(auto_mode="True", now=TZ.localize(datetime(2025, 6, 1, 7, 0)))
        h.step()
        assert gvals("desired_door_state") == ["open"]  # after sunrise
        # Timer opens only at 08:00 → switching modes re-evaluates
        gv.instance().set_values({"auto_mode": "False", "timer_mode": "True"})
        h.step()
        assert gvals("desired_door_state") == ["closed"]

    def test_clearing_error_resyncs_schedule(self):
        """After the operator cleared an error in auto mode the door stayed
        'stopped' until the next window."""
        h = Harness(auto_mode="True", now=NOON)
        h.step()
        h.door.ErrorState("jam")
        h.step()
        gv.instance().set_value("desired_door_state", "stopped")
        gv.instance().set_value("clear_error_state", True)
        h.step()
        assert gvals("desired_door_state") == ["open"]
        assert h.door.get_state() == "opening"

    def test_no_resync_while_mode_stays_on(self):
        h = Harness(auto_mode="True", now=NOON)
        h.step()
        gv.instance().set_value("desired_door_state", "stopped")
        h.step(3)
        assert gvals("desired_door_state") == ["stopped"]


class TestTimerOvernight:

    @pytest.mark.parametrize("hour, expected", [(21, "open"), (3, "open"), (12, "closed")])
    def test_schedule_wrapping_midnight(self, hour, expected):
        """open 20:00 / close 06:00 was evaluated as 'never open'."""
        h = Harness(timer_mode="True", timer_open_time="20:00", timer_close_time="06:00",
                    now=TZ.localize(datetime(2025, 6, 1, hour, 0)))
        h.step()
        assert gvals("desired_door_state") == [expected]

    def test_invalid_timer_times_logged_once(self, caplog):
        h = Harness(timer_mode="True", timer_open_time="bogus")
        with caplog.at_level("ERROR"):
            h.step(5)
        assert sum("Error parsing timer times" in r.message for r in caplog.records) == 1


class TestReferencePersistence:

    def test_runner_restores_stored_reference(self):
        """The reference travel time was never persisted/restored, so after
        every restart auto/timer mode disabled itself."""
        h = Harness(auto_mode="True", reference_door_endstops_ms=12_345.0)
        assert h.door.reference_door_endstops_ms == 12_345.0
        h.step()
        assert gvals("auto_mode") == ["True"]

    def test_successful_reference_invokes_persistence_callback(self, monkeypatch):
        saved = []
        gv.instance().set_value("toggle_reference_of_endstops", True)
        door = door_module.DOOR()
        monkeypatch.setattr(door, "reference_endstops", lambda: setattr(door, "reference_door_endstops_ms", 9000.0) or True)
        runner = DoorTaskRunner(door, lambda: (SUNRISE, SUNSET), lambda: NOON,
                                lambda t, b: None, on_reference_complete=saved.append)
        runner.step()
        assert saved == [9000.0]

    def test_callback_failure_does_not_break_step(self, monkeypatch):
        gv.instance().set_value("toggle_reference_of_endstops", True)
        door = door_module.DOOR()
        monkeypatch.setattr(door, "reference_endstops", lambda: setattr(door, "reference_door_endstops_ms", 1.0) or True)
        runner = DoorTaskRunner(door, lambda: (SUNRISE, SUNSET), lambda: NOON, lambda t, b: None,
                                on_reference_complete=mock.Mock(side_effect=OSError("disk full")))
        assert runner.step() is True

    def test_reference_round_trips_through_config(self, app_env, tmp_path):
        app_env.load_config()
        gv.instance().set_value("reference_door_endstops_ms", 8765.4)
        app_env.save_config()
        gv.reset_for_testing()
        app_env.load_config()
        assert gv.instance().get_value("reference_door_endstops_ms") == 8765.4


def test_error_state_keeps_real_door_state():
    """ErrorState() passed the error message to stop() → door.state (and
    the UI state / CSV) became the error text."""
    h = Harness(desired_door_state="open")
    h.step()
    h.door.ErrorState("Endstop not reached")
    h.step()
    assert gvals("state", "error_state") == ["stopped", "Endstop not reached"]


def test_unknown_desired_state_does_not_raise():
    """An unknown desired state raised AssertionError, killing door_task."""
    h = Harness(desired_door_state="opening")
    assert h.step() is True
    assert h.door.errorState is not None


# ═════════════════════════════ app: door task ═══════════════════════════════

class StopLoop(BaseException):
    pass


def test_door_task_survives_exceptions_and_stops_motor(app_env, monkeypatch):
    """Any exception inside step() terminated the door thread for good."""
    stops = []

    class FakeRunner:
        thread_sleep_time = 0.5
        calls = 0

        def __init__(self, **kw):
            FakeRunner.kwargs = kw

        def step(self):
            FakeRunner.calls += 1
            if FakeRunner.calls == 1:
                raise ValueError("sun never rises")
            raise StopLoop()

    fake_door = mock.Mock(stop=lambda *a, **k: stops.append(1))
    monkeypatch.setattr(app_env, "DOOR", lambda: fake_door)
    monkeypatch.setattr(app_env, "DoorTaskRunner", FakeRunner)
    monkeypatch.setattr(app_env.time, "sleep", lambda s: None)
    with pytest.raises(StopLoop):
        app_env.door_task()
    assert FakeRunner.calls == 2
    assert stops == [1]
    # Notifications are sent asynchronously and the reference is persisted
    assert FakeRunner.kwargs["send_notification"] is app_env.send_push_notification_async
    assert FakeRunner.kwargs["on_reference_complete"] is not None


def test_push_async_does_not_block(app_env, monkeypatch):
    """Web Push (10 s timeout per subscription) ran inside the motor loop."""
    started = threading.Event()
    release = threading.Event()

    def slow_send(title, body):
        started.set()
        release.wait(5)

    monkeypatch.setattr(app_env, "send_push_notification", slow_send)
    t0 = time.monotonic()
    app_env.send_push_notification_async("t", "b")
    assert time.monotonic() - t0 < 0.5
    assert started.wait(2)
    release.set()


# ═════════════════════════════ app: time & location ═════════════════════════

class TestTimezone:

    def test_current_time_is_location_wall_clock(self, app_env, monkeypatch):
        """localize(datetime.now()) labelled the *system* local time with the
        location's timezone — hours off whenever they differ."""
        tz = pytz.timezone("Pacific/Kiritimati")  # UTC+14: never the host zone
        monkeypatch.setattr(app_env, "timezone", tz)
        now = app_env.get_current_time()
        real = datetime.now(tz)
        assert abs((now - real).total_seconds()) < 5
        assert now.utcoffset() == real.utcoffset()

    def test_sunrise_uses_location_date(self, app_env, monkeypatch):
        tz = pytz.timezone("Pacific/Kiritimati")
        monkeypatch.setattr(app_env, "timezone", tz)
        monkeypatch.setattr(app_env, "boulder",
                            app_env.LocationInfo("Kiritimati", "KI", "Pacific/Kiritimati", 1.87, -157.4))
        sunrise, _ = app_env.get_sunrise_and_sunset()
        assert sunrise.date() == datetime.now(tz).date()


class TestLocationValidation:

    @pytest.mark.parametrize("payload", [
        {"city": "X", "region": "Y", "timezone": "Mars/Olympus", "latitude": 1, "longitude": 1},
        {"city": "X", "region": "Y", "timezone": "Europe/Berlin", "latitude": None, "longitude": 1},
        {"city": "X", "region": "Y", "timezone": "Europe/Berlin", "latitude": 91, "longitude": 1},
        {"city": "X", "region": "Y", "timezone": None, "latitude": 1, "longitude": 1},
    ])
    def test_invalid_location_rejected_and_not_saved(self, app_env, sio_client, tmp_path, payload):
        """An invalid timezone was saved to config.yaml first and then made
        reload_location_data() raise — also on every following boot."""
        app_env.load_config()
        before = (tmp_path / "config.yaml").read_text()
        sio_client.emit("update_location", payload)
        assert gv.instance().get_value("location")["city"] == "Boulder"
        assert (tmp_path / "config.yaml").read_text() == before

    def test_numeric_strings_are_accepted(self, app_env, sio_client):
        app_env.load_config()
        sio_client.emit("update_location", {"city": "B", "region": "G", "timezone": "Europe/Berlin",
                                             "latitude": "52.5", "longitude": "13.4"})
        assert gv.instance().get_value("location")["latitude"] == 52.5


class TestSocketInputValidation:

    def test_invalid_timer_times_rejected(self, app_env, sio_client):
        app_env.load_config()
        sio_client.emit("timer_times", {"open_time": "25:99", "close_time": "20:00"})
        assert gvals("timer_open_time") == ["07:00"]

    def test_timer_times_with_seconds_are_normalised(self, app_env, sio_client):
        app_env.load_config()
        sio_client.emit("timer_times", {"open_time": "6:05:00", "close_time": "20:30"})
        assert gvals("timer_open_time", "timer_close_time") == ["06:05", "20:30"]

    @pytest.mark.parametrize("payload", [
        {"sunrise_offset": "abc", "sunset_offset": "0"},
        {"sunrise_offset": "10"},
        {"sunrise_offset": "5000", "sunset_offset": "0"},
    ])
    def test_invalid_offsets_ignored(self, app_env, sio_client, payload):
        app_env.load_config()
        sio_client.emit("auto_offsets", payload)
        assert gvals("sunrise_offset", "sunset_offset") == [0, 0]

    @pytest.mark.parametrize("event", ["toggle", "toggle_timer"])
    def test_toggle_without_payload_is_ignored(self, app_env, sio_client, event):
        app_env.load_config()
        sio_client.emit(event, {})
        assert gvals("auto_mode") == ["True"]


# ═════════════════════════════ app: config ══════════════════════════════════

class TestConfigRobustness:

    @pytest.mark.parametrize("content", ["", "just a string\n", "- 1\n- 2\n"])
    def test_empty_or_invalid_config_falls_back_to_defaults(self, app_env, tmp_path, content):
        """An empty config.yaml crashed load_config() ('in None') at boot."""
        (tmp_path / "config.yaml").write_text(content)
        app_env.load_config()
        assert gv.instance().get_value("auto_mode") == "True"
        assert read_yaml(tmp_path / "config.yaml")["auto_mode"] == "True"

    def test_save_is_atomic(self, app_env, tmp_path, monkeypatch):
        """config.yaml was truncated in place; a crash mid-write lost it."""
        app_env.load_config()
        original = (tmp_path / "config.yaml").read_text()

        def boom(*a, **k):
            raise OSError("power cut")

        monkeypatch.setattr(YAML.YAML, "dump", boom)
        with pytest.raises(OSError):
            app_env.save_config()
        assert (tmp_path / "config.yaml").read_text() == original

    def test_secrets_without_section_do_not_crash(self, app_env, tmp_path):
        (tmp_path / ".secrets.yaml").write_text("other: 1\n")
        app_env.load_notification_keys()
        assert gv.instance().get_value("vapid_private_key") is None


# ═════════════════════════════ app: sensors / tasks ═════════════════════════

def test_midnight_reset_does_not_show_sentinel_min_max(app_env, monkeypatch):
    """Min/max were reset to 500/-500; with a sensor that returns nothing the
    dashboard showed 260.0°C / -295.6°C."""
    class NoReading:
        def get_temperature_and_humidity(self):
            return None, None

    class Stop(Exception):
        pass

    monkeypatch.setattr(app_env, "DHT11", lambda pin: NoReading())
    monkeypatch.setattr(app_env, "DHT22", lambda pin, power_pin=None: NoReading())
    monkeypatch.setattr(app_env, "CPUTemperature", lambda: mock.Mock(temperature=None))
    monkeypatch.setattr(app_env.time, "sleep", mock.Mock(side_effect=Stop))
    with pytest.raises(Stop):
        app_env.temperature_task()
    data = app_env.get_all_data()
    assert data["temp_out_min"] == data["temp_out_max"] == ""
    assert data["hum_in_max"] == ""


def test_data_log_task_survives_data_errors(app_env, monkeypatch):
    """An exception in get_all_data() killed the CSV logging thread."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("psutil hiccup")
        return {"a": "1"}

    class Stop(Exception):
        pass

    sleeps = mock.Mock(side_effect=[None, Stop()])
    monkeypatch.setattr(app_env, "get_all_data", flaky)
    monkeypatch.setattr(app_env.time, "sleep", sleeps)
    with pytest.raises(Stop):
        app_env.data_log_task()
    assert open(app_env.get_log_file_name()).read() == "# a\n1\n"


def test_camera_init_failure_ends_task_cleanly(app_env, monkeypatch):
    gv.instance().set_value("enable_camera", True)
    monkeypatch.setattr(app_env, "Camera", mock.Mock(side_effect=RuntimeError("no device")))
    app_env.camera_task()  # used to raise out of the thread


# ═════════════════════════════ app: CSV ═════════════════════════════════════

class TestCsvColumns:

    def test_logged_rows_keep_columns_aligned(self, app_env, client, monkeypatch):
        """Values were joined with ', ' unquoted; uptime ("0 day(s), 1
        hour(s), ...") added 3 columns, so auto_mode / errorstate etc. in
        the data viewer showed the wrong fields."""
        gv.instance().set_values({"auto_mode": "True", "error_state": "jam, badly", "state": "open"})

        class Stop(Exception):
            pass

        monkeypatch.setattr(app_env.time, "sleep", mock.Mock(side_effect=Stop))
        with pytest.raises(Stop):
            app_env.data_log_task()
        name = os.path.basename(app_env.get_log_file_name())
        row = client.get(f"/api/csv/{name}").get_json()["rows"][0]
        assert row["auto_mode"] == "True"
        assert row["errorstate"] == "jam, badly"
        assert row["state"] == "open"

    def test_legacy_unquoted_rows_are_realigned(self, app_env, client, tmp_path):
        (tmp_path / "log").mkdir()
        (tmp_path / "log" / "old.csv").write_text(
            "# time, temp_in, state, uptime, auto_mode, errorstate\n"
            "10:00:00.000, 21.5°C, open, 0 day(s), 1 hour(s), 2 minute(s), 3 second(s), True, \n"
        )
        row = client.get("/api/csv/old.csv").get_json()["rows"][0]
        assert row == {"time": "10:00:00", "temp_in": 21.5, "state": "open",
                       "auto_mode": "True", "errorstate": ""}


# ═════════════════════════════ app: HTTP API ════════════════════════════════

class TestGpioValidation:

    @pytest.mark.parametrize("raw, expected", [("false", False), ("true", True), ("0", False), (0, False), (True, True)])
    def test_bool_strings_parsed(self, app_env, client, raw, expected):
        """bool("false") is True — the invert flag could not be switched off
        with a string value."""
        app_env.load_config()
        resp = client.post("/api/gpio-config", json={"invert_end_up": raw})
        assert resp.status_code == 200
        assert gv.instance().get_value("gpio")["invert_end_up"] is expected

    def test_invalid_bool_rejected(self, client):
        assert client.post("/api/gpio-config", json={"invert_end_up": "maybe"}).status_code == 400

    def test_duplicate_pins_rejected(self, app_env, client):
        app_env.load_config()
        resp = client.post("/api/gpio-config", json={"endstop_up": 17})  # = motor_in1
        assert resp.status_code == 400
        assert "GPIO 17" in resp.get_json()["error"]
        assert gv.instance().get_value("gpio")["endstop_up"] == 23


class TestWifiValidation:

    @pytest.mark.parametrize("pw", ["short", "x" * 64])
    def test_ap_password_length(self, app_env, client, pw):
        """nmcli refuses WPA2 passwords < 8 chars; saving one meant the
        fallback AP could never start → device unreachable."""
        app_env.load_config()
        assert client.post("/api/wifi-config", json={"ap_password": pw}).status_code == 400
        assert gv.instance().get_value("wifi")["ap_password"] == "password"

    @pytest.mark.parametrize("timeout", [None, [1], 0, -5])
    def test_bad_timeout_rejected(self, client, timeout):
        assert client.post("/api/wifi-config", json={"timeout": timeout}).status_code == 400

    def test_empty_ap_ssid_rejected(self, client):
        assert client.post("/api/wifi-config", json={"ap_ssid": "  "}).status_code == 400


class TestFilesUnderRootPath:
    """Files were resolved against the process CWD instead of the repo root."""

    @pytest.fixture
    def other_cwd(self, tmp_path, monkeypatch):
        d = tmp_path / "elsewhere"
        d.mkdir()
        monkeypatch.chdir(d)
        return d

    def test_version(self, client, tmp_path, other_cwd):
        (tmp_path / "version.txt").write_text("abc123")
        assert client.get("/version").get_json() == {"version": "abc123"}

    def test_subscribe(self, client, tmp_path, other_cwd):
        client.post("/subscribe", json={"endpoint": "e"})
        assert (tmp_path / ".subscriptions.json").exists()
        assert not (other_cwd / ".subscriptions.json").exists()

    def test_push_reads_root_subscriptions(self, app_env, tmp_path, other_cwd, monkeypatch):
        gv.instance().set_value("vapid_private_key", "K")
        (tmp_path / ".subscriptions.json").write_text(json.dumps({"subscriptions": [{"endpoint": "e"}]}))
        push = mock.Mock()
        monkeypatch.setattr(app_env, "webpush", push)
        app_env.send_push_notification("t", "b")
        push.assert_called_once()

    def test_generate_vapid_pair_targets_repo_root(self):
        import generateVapidPair  # importing must not prompt / generate keys
        root = os.path.dirname(os.path.dirname(os.path.realpath(generateVapidPair.__file__)))
        assert generateVapidPair.SECRETS_FILE == os.path.join(root, ".secrets.yaml")
        assert generateVapidPair.SUBSCRIPTIONS_FILE == os.path.join(root, ".subscriptions.json")


class TestSubscriptions:

    def test_corrupt_file_is_recovered(self, client, tmp_path):
        """A corrupt .subscriptions.json made /subscribe return 500 forever."""
        (tmp_path / ".subscriptions.json").write_text("{not json")
        assert client.post("/subscribe", json={"endpoint": "e"}).status_code == 200
        assert json.loads((tmp_path / ".subscriptions.json").read_text()) == {"subscriptions": [{"endpoint": "e"}]}

    def test_subscription_without_endpoint_rejected(self, client):
        assert client.post("/subscribe", json={"foo": 1}).status_code == 400
        assert client.post("/subscribe").status_code == 400

    def test_push_does_not_rewrite_file_without_expired_subscriptions(self, app_env, tmp_path, monkeypatch):
        gv.instance().set_value("vapid_private_key", "K")
        path = tmp_path / ".subscriptions.json"
        path.write_text(json.dumps({"subscriptions": [{"endpoint": "e"}]}))
        os.utime(path, (1, 1))
        monkeypatch.setattr(app_env, "webpush", mock.Mock())
        app_env.send_push_notification("t", "b")
        assert os.stat(path).st_mtime == 1

    def test_404_subscriptions_are_removed(self, app_env, tmp_path, monkeypatch):
        """Only 410 was treated as expired; 404 (also 'gone') was kept forever."""
        gv.instance().set_value("vapid_private_key", "K")
        (tmp_path / ".subscriptions.json").write_text(
            json.dumps({"subscriptions": [{"endpoint": "gone"}, {"endpoint": "ok"}]}))

        def push(sub, **kw):
            if sub["endpoint"] == "gone":
                raise app_env.WebPushException("x", response=mock.Mock(status_code=404))

        monkeypatch.setattr(app_env, "webpush", push)
        app_env.send_push_notification("t", "b")
        saved = json.loads((tmp_path / ".subscriptions.json").read_text())
        assert saved == {"subscriptions": [{"endpoint": "ok"}]}

    def test_subscription_added_during_send_is_kept(self, app_env, tmp_path, monkeypatch):
        gv.instance().set_value("vapid_private_key", "K")
        path = tmp_path / ".subscriptions.json"
        path.write_text(json.dumps({"subscriptions": [{"endpoint": "gone"}]}))

        def push(sub, **kw):
            # a browser subscribes while notifications are being sent
            path.write_text(json.dumps({"subscriptions": [{"endpoint": "gone"}, {"endpoint": "new"}]}))
            raise app_env.WebPushException("x", response=mock.Mock(status_code=410))

        monkeypatch.setattr(app_env, "webpush", push)
        app_env.send_push_notification("t", "b")
        assert json.loads(path.read_text()) == {"subscriptions": [{"endpoint": "new"}]}


def test_captive_probe_routes_registered_once(app_env):
    rules = [r.rule for r in app_env.app.url_map.iter_rules()]
    assert rules.count("/generate_204") == 1
    assert rules.count("/gen_204") == 1


def test_valid_locations_are_cached(app_env):
    assert app_env.get_valid_locations() is app_env.get_valid_locations()
