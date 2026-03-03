"""Tests for services.config_service."""

from __future__ import annotations

import os

import pytest

from protected_dict import protected_dict
from services.config_service import ConfigService, _DEFAULTS


class TestConfigServiceLoad:
    def test_load_defaults_when_no_file(self, gv, tmp_config: str) -> None:
        svc = ConfigService(tmp_config, gv)
        svc.load()
        assert gv.get_value("auto_mode") == "True"
        assert gv.get_value("sunrise_offset") == 0
        assert gv.get_value("csvLog") is True
        assert gv.get_value("reference_door_endstops_ms") is None

    def test_load_creates_config_file_when_missing(self, gv, tmp_config: str) -> None:
        svc = ConfigService(tmp_config, gv)
        svc.load()
        assert os.path.exists(tmp_config)

    def test_load_reads_existing_file(self, gv, tmp_config: str, tmp_path) -> None:
        # Write a partial config
        cfg_text = (
            "auto_mode: 'False'\n"
            "sunrise_offset: 30\n"
            "sunset_offset: -15\n"
        )
        with open(tmp_config, "w") as f:
            f.write(cfg_text)

        svc = ConfigService(tmp_config, gv)
        svc.load()

        assert gv.get_value("auto_mode") == "False"
        assert gv.get_value("sunrise_offset") == 30
        assert gv.get_value("sunset_offset") == -15
        # Keys not in the file should still be defaults
        assert gv.get_value("csvLog") is True

    def test_load_merges_with_defaults(self, gv, tmp_config: str) -> None:
        cfg_text = "enable_camera: true\ncamera_index: 1\n"
        with open(tmp_config, "w") as f:
            f.write(cfg_text)

        svc = ConfigService(tmp_config, gv)
        svc.load()

        assert gv.get_value("enable_camera") is True
        assert gv.get_value("camera_index") == 1
        assert gv.get_value("auto_mode") == "True"  # default preserved

    def test_load_persists_reference_ms(self, gv, tmp_config: str) -> None:
        cfg_text = "reference_door_endstops_ms: 4800.5\n"
        with open(tmp_config, "w") as f:
            f.write(cfg_text)

        svc = ConfigService(tmp_config, gv)
        svc.load()
        assert gv.get_value("reference_door_endstops_ms") == pytest.approx(4800.5)


class TestConfigServiceSave:
    def test_save_writes_file(self, gv, tmp_config: str) -> None:
        gv.set_values(
            {
                "auto_mode": "True",
                "sunrise_offset": 10,
                "sunset_offset": -5,
                "location": {"city": "Test", "region": "R", "timezone": "UTC",
                             "latitude": 0.0, "longitude": 0.0},
                "consoleLogToFile": False,
                "csvLog": True,
                "enable_camera": False,
                "camera_index": 0,
                "reference_door_endstops_ms": 5000.0,
            }
        )
        svc = ConfigService(tmp_config, gv)
        svc.save()
        assert os.path.exists(tmp_config)

    def test_save_then_load_roundtrip(self, gv, tmp_config: str) -> None:
        gv.set_values(
            {
                "auto_mode": "False",
                "sunrise_offset": 20,
                "sunset_offset": -10,
                "location": {"city": "Roundtrip", "region": "R", "timezone": "UTC",
                             "latitude": 1.0, "longitude": 2.0},
                "consoleLogToFile": False,
                "csvLog": False,
                "enable_camera": True,
                "camera_index": 2,
                "reference_door_endstops_ms": 3750.0,
            }
        )
        svc = ConfigService(tmp_config, gv)
        svc.save()

        # Clear global vars and reload
        protected_dict._dictionary = {}
        svc.load()

        assert gv.get_value("auto_mode") == "False"
        assert gv.get_value("sunrise_offset") == 20
        assert gv.get_value("camera_index") == 2
        assert gv.get_value("reference_door_endstops_ms") == pytest.approx(3750.0)

    def test_save_raises_on_bad_path(self, gv) -> None:
        svc = ConfigService("/nonexistent/path/config.yaml", gv)
        # set enough keys to not fail on get_value
        gv.set_values({k: None for k in [
            "auto_mode", "sunrise_offset", "sunset_offset", "location",
            "consoleLogToFile", "csvLog", "enable_camera", "camera_index",
            "reference_door_endstops_ms",
        ]})
        with pytest.raises(OSError):
            svc.save()
