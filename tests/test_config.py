"""Typed settings: parsing, validation, legacy compatibility, persistence."""

from __future__ import annotations

from dataclasses import replace

import pytest
import ruamel.yaml as YAML

from coop.config import (AuthConfig, ConfigError, ConfigStore, GpioConfig, LocationConfig, Mode,
                         OutdoorSensorType, Settings, WifiConfig, parse_bool, parse_float, parse_hhmm,
                         parse_int)


def read_yaml(path):
    with open(path) as f:
        return YAML.YAML(typ="safe").load(f)


def write_yaml(path, data):
    with open(path, "w") as f:
        YAML.YAML().dump(data, f)


@pytest.fixture
def store(tmp_path):
    return ConfigStore(str(tmp_path / "config.yaml"))


# ── parsers ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [(True, True), ("true", True), ("On", True), (1, True),
                                           (False, False), ("false", False), ("0", False), ("", False), (0, False)])
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


@pytest.mark.parametrize("raw", ["maybe", 2, None, [1]])
def test_parse_bool_rejects(raw):
    with pytest.raises(ConfigError):
        parse_bool(raw)


def test_parse_int():
    assert parse_int("12", "x") == 12
    assert parse_int(3.0, "x") == 3
    for bad in ("a", None, True, 3.5, [1]):
        with pytest.raises(ConfigError):
            parse_int(bad, "x")
    with pytest.raises(ConfigError, match="0–40"):
        parse_int(41, "x", 0, 40)


def test_parse_float():
    assert parse_float("52.5", "lat", -90, 90) == 52.5
    for bad in (None, "nan", float("nan"), True, "x", 91):
        with pytest.raises(ConfigError):
            parse_float(bad, "lat", -90, 90)


@pytest.mark.parametrize("raw, expected", [("7:05", "07:05"), ("07:05", "07:05"), ("23:59:59", "23:59")])
def test_parse_hhmm(raw, expected):
    assert parse_hhmm(raw, "t") == expected


@pytest.mark.parametrize("raw", ["25:00", "ab", None, 700, "7"])
def test_parse_hhmm_rejects(raw):
    with pytest.raises(ConfigError):
        parse_hhmm(raw, "t")


# ── sections ─────────────────────────────────────────────────────────────────

class TestGpio:

    def test_defaults_valid(self):
        assert GpioConfig().validate()

    def test_merged(self):
        g = GpioConfig().merged({"motor_in1": "4", "invert_end_up": "true", "reference_timeout": 90,
                                 "dht22_power": None})
        assert (g.motor_in1, g.invert_end_up, g.reference_timeout, g.dht22_power) == (4, True, 90, None)

    @pytest.mark.parametrize("data, fragment", [
        ({"motor_in1": 41}, "'motor_in1' must be 0–40"),
        ({"endstop_up": "x"}, "'endstop_up' must be an integer"),
        ({"invert_end_up": "maybe"}, "'invert_end_up' must be a boolean"),
        ({"reference_timeout": 4}, "'reference_timeout' must be 5–600 seconds"),
        ({"endstop_up": 17}, "GPIO 17"),
    ])
    def test_merged_rejects(self, data, fragment):
        with pytest.raises(ConfigError, match=fragment):
            GpioConfig().merged(data)

    def test_all_errors_reported(self):
        with pytest.raises(ConfigError) as e:
            GpioConfig().merged({"motor_in1": 99, "motor_in2": "x"})
        assert str(e.value).count(";") == 1


class TestWifi:

    def test_merged(self):
        w = WifiConfig().merged({"ssid": "Home", "password": 1234, "timeout": "30", "ap_password": "x" * 8})
        assert (w.ssid, w.password, w.timeout) == ("Home", "1234", 30)

    @pytest.mark.parametrize("data", [{"ap_password": "short"}, {"ap_password": "x" * 64}, {"ap_ssid": " "},
                                      {"timeout": 0}, {"timeout": None}, {"timeout": "soon"},
                                      {"ap_allowed_hosts": "a"}])
    def test_rejects(self, data):
        with pytest.raises(ConfigError):
            WifiConfig().merged(data)


class TestLocation:

    def test_from_dict(self):
        loc = LocationConfig.from_dict({"city": "B", "region": "G", "timezone": "Europe/Berlin",
                                        "latitude": "52.5", "longitude": 13.4})
        assert loc.latitude == 52.5 and str(loc.tz) == "Europe/Berlin"

    @pytest.mark.parametrize("data", [
        {"timezone": "Mars/Base", "latitude": 1, "longitude": 1},
        {"timezone": None, "latitude": 1, "longitude": 1},
        {"timezone": "Europe/Berlin", "latitude": None, "longitude": 1},
        {"timezone": "Europe/Berlin", "latitude": 91, "longitude": 1},
        "not a dict",
    ])
    def test_rejects(self, data):
        with pytest.raises(ConfigError):
            LocationConfig.from_dict(data)


def test_auth_enabled_only_with_hash():
    assert AuthConfig().enabled is False
    assert AuthConfig("a", "hash").enabled is True


# ── YAML mapping ─────────────────────────────────────────────────────────────

class TestYaml:

    def test_legacy_file(self):
        s = Settings.from_yaml_dict({
            "auto_mode": "False", "timer_mode": "True", "csvLog": False, "consoleLogToFile": True,
            "reference_door_endstops_ms": 12345.6, "gpio": {"motor_in1": 4}, "wifi": {"ssid": "x"},
        })
        assert s.mode is Mode.TIMER
        assert s.csv_log is False
        assert s.reference_travel_ms == 12345.6
        assert s.gpio.motor_in1 == 4 and s.gpio.motor_in2 == 27
        assert s.wifi.ssid == "x" and s.wifi.ap_ssid == "DINKY-COOP"
        assert "consoleLogToFile" not in s.extra

    @pytest.mark.parametrize("raw, mode", [
        ({"auto_mode": True}, Mode.AUTO), ({"auto_mode": "True", "timer_mode": "True"}, Mode.AUTO),
        ({"auto_mode": False, "timer_mode": False}, Mode.MANUAL), ({"mode": "timer"}, Mode.TIMER), ({}, Mode.AUTO),
    ])
    def test_mode(self, raw, mode):
        assert Settings.from_yaml_dict(raw).mode is mode

    def test_invalid_values_fall_back_to_defaults(self):
        s = Settings.from_yaml_dict({"sunrise_offset": "abc", "timer_open_time": "99:99", "location": {"timezone": "X"},
                                     "outdoor_sensor_type": "laser", "log_level": "LOUD", "mode": "chaos",
                                     "reference_door_endstops_ms": -5, "camera_index": "x"})
        d = Settings()
        assert (s.sunrise_offset, s.timer_open_time, s.location, s.outdoor_sensor_type, s.log_level, s.mode,
                s.reference_travel_ms, s.camera_index) == (d.sunrise_offset, d.timer_open_time, d.location,
                                                            d.outdoor_sensor_type, d.log_level, d.mode, None, 0)

    def test_questionable_gpio_layout_is_kept(self):
        """Never silently move pins on real hardware — keep it and warn."""
        s = Settings.from_yaml_dict({"gpio": {"endstop_up": 17, "motor_in2": "x"}})
        assert s.gpio.endstop_up == 17 and s.gpio.motor_in2 == 27

    def test_round_trip_and_unknown_keys(self):
        s = replace(Settings.from_yaml_dict({"my_extra": {"a": 1}}), mode=Mode.TIMER,
                    outdoor_sensor_type=OutdoorSensorType.API, reference_travel_ms=900.0)
        again = Settings.from_yaml_dict(s.to_yaml_dict())
        assert again == s and again.extra == {"my_extra": {"a": 1}}
        yaml = s.to_yaml_dict()
        assert (yaml["auto_mode"], yaml["timer_mode"], yaml["csvLog"]) == (False, True, True)

    def test_settings_validate(self):
        with pytest.raises(ConfigError):
            replace(Settings(), sunrise_offset=721).validate()
        with pytest.raises(ConfigError):
            replace(Settings(), reference_travel_ms=0).validate()


# ── store ────────────────────────────────────────────────────────────────────

class TestStore:

    def test_missing_file_is_created(self, store):
        s = store.load()
        assert s == Settings()
        assert read_yaml(store.path)["auto_mode"] is True

    @pytest.mark.parametrize("content", ["", "just text\n", "- 1\n", "{unclosed: [\n"])
    def test_invalid_file_falls_back_and_is_rewritten(self, store, content):
        with open(store.path, "w") as f:
            f.write(content)
        assert store.load() == Settings()
        assert isinstance(read_yaml(store.path), dict)

    def test_existing_file_is_not_rewritten_on_load(self, store):
        write_yaml(store.path, {"sunset_offset": 15, "custom": 1})
        before = open(store.path).read()
        assert store.load().sunset_offset == 15
        assert open(store.path).read() == before

    def test_update_persists_and_notifies(self, store):
        store.load()
        seen = []
        store.subscribe(lambda old, new: seen.append((old.mode, new.mode)))
        store.update(mode=Mode.TIMER)
        assert read_yaml(store.path)["timer_mode"] is True
        assert seen == [(Mode.AUTO, Mode.TIMER)]

    def test_noop_update_does_not_write_or_notify(self, store):
        store.load()
        seen = []
        store.subscribe(lambda o, n: seen.append(1))
        store.update(mode=store.settings.mode)
        assert seen == []

    def test_invalid_update_changes_nothing(self, store):
        store.load()
        before = open(store.path).read()
        with pytest.raises(ConfigError):
            store.update(timer_open_time="nope")
        assert store.settings.timer_open_time == "07:00"
        assert open(store.path).read() == before

    def test_write_failure_keeps_old_file_and_settings(self, store, monkeypatch):
        store.load()
        before = open(store.path).read()
        monkeypatch.setattr(YAML.YAML, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("power cut")))
        with pytest.raises(OSError):
            store.update(mode=Mode.MANUAL)
        assert open(store.path).read() == before
        assert store.settings.mode is Mode.AUTO

    def test_legacy_invalid_section_does_not_block_other_updates(self, store):
        write_yaml(store.path, {"wifi": {"ap_password": "short"}})
        store.load()
        store.update(mode=Mode.MANUAL)  # would fail if wifi were re-validated
        assert store.settings.wifi.ap_password == "short"
        with pytest.raises(ConfigError):
            store.update(wifi=replace(store.settings.wifi, ssid="new"))

    def test_listener_errors_are_contained(self, store):
        store.load()
        store.subscribe(lambda o, n: 1 / 0)
        store.update(mode=Mode.MANUAL)
        assert store.settings.mode is Mode.MANUAL

    def test_update_with_function(self, store):
        store.load()
        store.update(lambda s: replace(s, sunrise_offset=10))
        assert store.settings.sunrise_offset == 10
