"""Application wiring, workers, CLI and a full end-to-end simulation."""

from __future__ import annotations

import base64
from datetime import timedelta
from unittest import mock

import pytest

from conftest import DENVER, FixedSensor, at
from coop.clock import FakeClock
from coop.config import ConfigStore, LocationConfig, Mode
from coop.door.model import DesiredState, DoorState
from coop.hardware import Hardware
from coop.hardware.camera import CameraError, MockCamera
from coop.hardware.gpio import MockGpio
from coop.hardware.simulator import DoorSimulator


class TestWiring:

    def test_loads_config_and_restores_reference(self, make_app):
        app = make_app({"auto_mode": True, "reference_door_endstops_ms": 9000})
        assert app.settings.reference_travel_ms == 9000
        app.controller.step()
        assert app.settings.mode is Mode.AUTO  # reference known → stays in auto after restart

    def test_location_change_updates_sun(self, app):
        app.config.update(location=LocationConfig("Berlin", "DE", "Europe/Berlin", 52.5, 13.4))
        assert app.sun.location.city == "Berlin"

    def test_gpio_live_settings_reach_driver(self, app):
        app.config.update(gpio=app.settings.gpio.merged({"invert_end_down": True}))
        assert app.driver.pins.invert_end_down is True

    def test_log_level_change(self, app):
        import logging
        app.config.update(log_level="WARNING")
        assert logging.getLogger().level == logging.WARNING
        app.config.update(log_level="INFO")

    def test_manual_command(self, make_app):
        app = make_app({"timer_mode": True, "auto_mode": False, "reference_door_endstops_ms": 5000})
        app.manual_command(DesiredState.OPEN)
        assert app.settings.mode is Mode.MANUAL
        app.controller.step()
        assert app.controller.desired is DesiredState.OPEN

    def test_set_mode(self, app):
        app.set_mode(Mode.TIMER, True)
        assert app.settings.mode is Mode.TIMER
        app.set_mode(Mode.AUTO, False)
        assert app.settings.mode is Mode.TIMER
        app.set_mode(Mode.TIMER, False)
        assert app.settings.mode is Mode.MANUAL


class TestWorkers:

    def test_default_worker_set(self, app):
        assert [w.name for w in app.build_workers()] == ["door", "environment", "broadcast", "csv-log"]

    def test_optional_workers(self, make_app):
        gpio = MockGpio()
        from coop.config import GpioConfig
        hw = Hardware(gpio=gpio, indoor=FixedSensor(), outdoor=FixedSensor(), cpu=FixedSensor(),
                      camera_factory=MockCamera, simulator=DoorSimulator(gpio, GpioConfig()))
        wifi = mock.Mock(mock=False)
        app = make_app({"enable_camera": True, "csvLog": False}, hardware=hw, wifi=wifi)
        names = [w.name for w in app.build_workers()]
        assert names == ["door", "environment", "broadcast", "camera", "door-simulator", "wifi-watchdog"]

    def test_door_worker_stops_motor_on_crash(self, app):
        app.driver.open()
        door = app.build_workers()[0]
        app.controller.step = mock.Mock(side_effect=RuntimeError("boom"))
        door._step = app.controller.step
        door.run_once()
        assert app.driver.motor_outputs() == {"motor_in1": False, "motor_in2": False, "motor_ena": False}

    def test_broadcast_emits_data(self, app):
        events = []
        app.emit = lambda e, p: events.append((e, p))
        assert app._broadcast() == app.BROADCAST_INTERVAL_S
        assert events[0][0] == "data" and "state" in events[0][1]

    def test_start_and_stop(self, app):
        app.start()
        assert all(w.alive for w in app.workers)
        app.stop()
        assert not any(w.alive for w in app.workers)


class TestCamera:

    def streamer(self, app, factory):
        from coop.application import CameraStreamer
        app.hardware.camera_factory = factory
        events = []
        app.emit = lambda e, p: events.append((e, p))
        return CameraStreamer(app), events

    def test_frames(self, app):
        s, events = self.streamer(app, lambda idx: mock.Mock(get_frame=lambda: b"\x01\x02"))
        assert s.step() == app.CAMERA_INTERVAL_S
        assert events == [("camera", base64.b64encode(b"\x01\x02").decode())]

    def test_camera_error_ends_worker(self, app):
        s, _ = self.streamer(app, mock.Mock(side_effect=CameraError("no device")))
        assert s.step() is None


class TestEndToEnd:
    """Real Application + door simulator + fake clock: a day in the life."""

    def test_reference_night_and_sunrise(self, paths):
        from coop.application import Application
        from conftest import RecordingNotifier, FakeSystem, FakeWifi
        from coop.config import GpioConfig

        gpio = MockGpio()
        sim = DoorSimulator(gpio, GpioConfig(), travel_time_s=6, position=0.4)

        class SimClock(FakeClock):
            def sleep(self, s):
                super().sleep(s)
                sim.tick(s)

        clock = SimClock(at(21, 30), DENVER)
        hw = Hardware(gpio=gpio, indoor=FixedSensor(), outdoor=FixedSensor(), cpu=FixedSensor(),
                      camera_factory=MockCamera, simulator=sim)
        app = Application(paths, clock=clock, hardware=hw, wifi=FakeWifi(), notifier=RecordingNotifier(),
                          system=FakeSystem())

        def run(seconds):
            elapsed = 0.0
            while elapsed < seconds:
                delay = app.controller.step()
                clock.sleep(delay)
                elapsed += delay

        run(1)
        assert app.settings.mode is Mode.MANUAL  # default auto, but no reference yet

        app.controller.request_reference()
        run(1)
        assert app.settings.reference_travel_ms == pytest.approx(6000, abs=250)
        assert sim.position == 1.0 and app.driver.state is DoorState.OPEN

        app.set_mode(Mode.AUTO, True)
        run(10)  # 21:30 → after sunset: close
        assert sim.position == 0.0 and app.driver.state is DoorState.CLOSED

        sunrise = app.controller.status.sunrise
        clock.set_now(sunrise + timedelta(days=1) - timedelta(seconds=5))
        run(12)
        assert app.driver.state is DoorState.OPEN and sim.position == 1.0
        assert app.controller.status.position == 1.0
        assert app.notifier.sent == []
        # persisted across a restart
        assert ConfigStore(paths.config).load().reference_travel_ms == app.settings.reference_travel_ms


class TestCli:

    def test_check_config(self, paths, monkeypatch, capsys):
        from coop import __main__ as cli
        monkeypatch.setattr("coop.paths.default_root", lambda: paths.root)
        assert cli.main(["check-config"]) == 0
        assert "config.yaml OK" in capsys.readouterr().out

    def test_set_password_and_disable(self, paths, monkeypatch):
        from coop import __main__ as cli
        from werkzeug.security import check_password_hash
        monkeypatch.setattr("coop.paths.default_root", lambda: paths.root)
        answers = iter(["long-password", "long-password"])
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))
        assert cli.main(["set-password", "--username", "coop"]) == 0
        auth = ConfigStore(paths.config).load().auth
        assert auth.username == "coop" and check_password_hash(auth.password_hash, "long-password")
        assert cli.main(["disable-auth"]) == 0
        assert ConfigStore(paths.config).load().auth.enabled is False

    @pytest.mark.parametrize("answers", [["short", "short"], ["long-password", "different"]])
    def test_set_password_rejects(self, paths, monkeypatch, answers):
        from coop import __main__ as cli
        monkeypatch.setattr("coop.paths.default_root", lambda: paths.root)
        it = iter(answers)
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(it))
        assert cli.main(["set-password"]) == 1
