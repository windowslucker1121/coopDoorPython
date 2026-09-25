"""Hardware layer: GPIO backends, sensors, camera, door simulator, factory."""

from __future__ import annotations

import types
from dataclasses import replace
from unittest import mock

import pytest

from coop.config import GpioConfig, OutdoorSensorType, Settings
from coop.hardware import build_hardware
from coop.hardware.camera import CameraError, MockCamera, OpenCvCamera
from coop.hardware.gpio import MockGpio, RpiGpio
from coop.hardware.sensors import (CpuTemperatureSensor, DhtSensor, NullSensor, OpenMeteoSensor,
                                   RandomWalkSensor, Reading)
from coop.hardware.simulator import DoorSimulator


# ── GPIO ─────────────────────────────────────────────────────────────────────

class TestMockGpio:

    def test_outputs(self):
        g = MockGpio()
        g.setup_output(17, initial=True)
        assert g.read(17) is True
        g.write(17, False)
        assert g.read(17) is False
        with pytest.raises(ValueError):
            g.write(99, True)

    def test_trigger_fires_callbacks_on_change_only(self):
        g = MockGpio()
        g.setup_input(23)
        seen = []
        g.add_edge_callback(23, seen.append)
        g.trigger(23, True)
        g.trigger(23, True)
        g.trigger(23, False)
        assert seen == [23, 23]
        g.set_input(23, True)
        assert seen == [23, 23] and g.read(23) is True

    def test_snapshot_and_cleanup(self):
        g = MockGpio()
        g.setup_output(1)
        g.setup_input(2)
        assert g.snapshot() == {1: {"mode": "OUT", "state": "LOW"}, 2: {"mode": "IN", "state": "LOW"}}
        g.add_edge_callback(2, lambda c: None)
        g.cleanup()
        g.trigger(2, True)  # no callbacks left


class TestRpiGpio:

    def fake_module(self):
        m = mock.Mock()
        m.BCM, m.OUT, m.IN, m.HIGH, m.LOW, m.PUD_DOWN, m.PUD_UP, m.BOTH = "BCM", "OUT", "IN", 1, 0, "D", "U", "B"
        m.input.return_value = 1
        return m

    def test_adapter(self):
        m = self.fake_module()
        g = RpiGpio(m)
        m.setmode.assert_called_once_with("BCM")
        g.setup_output(17, True)
        m.setup.assert_called_with(17, "OUT", initial=1)
        g.setup_input(23)
        m.setup.assert_called_with(23, "IN", pull_up_down="D")
        g.write(17, False)
        m.output.assert_called_with(17, 0)
        assert g.read(23) is True
        cb = lambda c: None
        g.add_edge_callback(23, cb, 250)
        m.add_event_detect.assert_called_with(23, "B", callback=cb, bouncetime=250)
        g.cleanup()
        m.cleanup.assert_called_once()
        assert g.is_mock is False


# ── sensors ──────────────────────────────────────────────────────────────────

class FakeDevice:
    script: list = []
    instances: list = []

    def __init__(self, pin):
        self.pin = pin
        self.exited = False
        self._h = None
        FakeDevice.instances.append(self)

    @property
    def temperature(self):
        item = FakeDevice.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        t, self._h = item
        return t

    @property
    def humidity(self):
        return self._h

    def exit(self):
        self.exited = True


@pytest.fixture
def dht():
    FakeDevice.script, FakeDevice.instances = [], []
    module = types.SimpleNamespace(DHT11=FakeDevice, DHT22=FakeDevice)
    board = types.SimpleNamespace(D26="board-D26")
    sleeps = []

    def make(model="DHT11", **kw):
        return DhtSensor(model, 26, dht_module=module, board_module=board, sleep=sleeps.append, **kw)
    make.sleeps = sleeps
    return make


class TestDht:

    def test_read(self, dht):
        FakeDevice.script = [(20.0, 55.0)]
        s = dht()
        assert s.read() == Reading(20.0, 55.0)
        assert FakeDevice.instances[0].pin == "board-D26"

    def test_retries(self, dht):
        FakeDevice.script = [RuntimeError("checksum"), OverflowError(), (1.0, 2.0)]
        assert dht().read() == Reading(1.0, 2.0)
        assert dht.sleeps == [2.2, 2.2]

    def test_gives_up(self, dht):
        FakeDevice.script = [RuntimeError()] * 3
        assert dht().read() == Reading()

    def test_reinit_on_oserror(self, dht):
        FakeDevice.script = [OSError(22, "pulsein"), (5.0, 6.0)]
        s = dht()
        assert s.read() == Reading(5.0, 6.0)
        assert FakeDevice.instances[0].exited and len(FakeDevice.instances) == 2

    def test_unexpected_error_does_not_raise(self, dht):
        FakeDevice.script = [ValueError("weird")]
        assert dht().read() == Reading()

    def test_power_cycling(self, dht):
        g = MockGpio()
        writes = []
        orig = g.write
        g.write = lambda p, v: (writes.append((p, v)), orig(p, v))
        FakeDevice.script = [(10.0, 20.0)]
        s = dht("DHT22", gpio=g, power_pin=20)
        s.read()
        assert writes == [(20, True), (20, False)]
        assert g.read(20) is False
        FakeDevice.script = [RuntimeError()] * 3
        s.read()
        assert g.read(20) is False  # powered down even on failure

    def test_close(self, dht):
        s = dht()
        s.close()
        assert FakeDevice.instances[0].exited


class TestOpenMeteo:

    def response(self, payload, ok=True):
        r = mock.Mock()
        r.json.return_value = payload
        r.raise_for_status.side_effect = None if ok else RuntimeError("500")
        return r

    def test_fetch_and_cache(self):
        now = {"t": 0.0}
        get = mock.Mock(return_value=self.response({"current": {"temperature_2m": 10, "relative_humidity_2m": 80}}))
        s = OpenMeteoSensor(lambda: (40.0, -105.0), cache_seconds=300, http_get=get, monotonic=lambda: now["t"])
        assert s.read() == Reading(10.0, 80.0)
        params = get.call_args.kwargs["params"]
        assert params["temperature_unit"] == "celsius" and params["latitude"] == 40.0
        now["t"] = 299
        s.read()
        assert get.call_count == 1
        now["t"] = 301
        get.return_value = self.response({"current": {"temperature_2m": 12}})
        assert s.read() == Reading(12.0, None)

    def test_failures_return_last_good_value_and_back_off(self):
        now = {"t": 0.0}
        get = mock.Mock(return_value=self.response({"current": {"temperature_2m": 10, "relative_humidity_2m": 80}}))
        s = OpenMeteoSensor(lambda: (1, 2), cache_seconds=10, http_get=get, monotonic=lambda: now["t"])
        s.read()
        now["t"] = 11
        get.return_value = self.response({}, ok=False)
        assert s.read() == Reading(10.0, 80.0)
        now["t"] = 15
        s.read()
        assert get.call_count == 2

    def test_bad_location(self):
        get = mock.Mock()
        s = OpenMeteoSensor(lambda: (None, 1), http_get=get)
        assert s.read() == Reading()


def test_cpu_sensor(tmp_path):
    f = tmp_path / "temp"
    f.write_text("51234\n")
    assert CpuTemperatureSensor(str(f)).read() == Reading(51.234, None)
    assert CpuTemperatureSensor(str(tmp_path / "missing")).read() == Reading()


def test_random_walk_and_null():
    import random
    s = RandomWalkSensor("x", (10, 20), (30, 40), rng=random.Random(1))
    for _ in range(100):
        r = s.read()
        assert 10 <= r.temperature_c <= 20 and 30 <= r.humidity <= 40
    assert RandomWalkSensor("cpu", with_humidity=False).read().humidity is None
    assert NullSensor().read() == Reading()


# ── camera ───────────────────────────────────────────────────────────────────

class TestCamera:

    def cv2(self, opened=True, frame=(True, "f"), encoded=True):
        cv2 = mock.Mock()
        cv2.VideoCapture.return_value.isOpened.return_value = opened
        cv2.VideoCapture.return_value.read.return_value = frame
        cv2.imencode.return_value = (encoded, mock.Mock(tobytes=lambda: b"jpeg"))
        return cv2

    def test_frame(self):
        cam = OpenCvCamera(2, cv2_module=self.cv2())
        assert cam.get_frame() == b"jpeg"
        cam.close()

    @pytest.mark.parametrize("kwargs, message", [({"opened": False}, "Unable to open"),
                                                 ({"frame": (False, None)}, "capture"),
                                                 ({"encoded": False}, "encode")])
    def test_errors(self, kwargs, message):
        with pytest.raises(CameraError, match=message):
            OpenCvCamera(0, cv2_module=self.cv2(**kwargs)).get_frame()

    def test_missing_opencv(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "cv2", None)
        with pytest.raises(CameraError, match="OpenCV"):
            OpenCvCamera(0)

    def test_mock_camera_jpeg(self):
        frame = MockCamera(1).get_frame()
        assert frame[:2] == b"\xff\xd8" and frame[-2:] == b"\xff\xd9"


# ── simulator ────────────────────────────────────────────────────────────────

class TestSimulator:

    def setup_gpio(self, pins=GpioConfig()):
        g = MockGpio()
        for p in (pins.motor_in1, pins.motor_in2, pins.motor_ena):
            g.setup_output(p)
        return g

    def test_moves_and_drives_endstops(self):
        g = self.setup_gpio()
        sim = DoorSimulator(g, GpioConfig(), travel_time_s=4, position=0.0)
        assert g.read(24) is True and g.read(23) is False
        g.write(22, True)  # open: in1=in2=LOW, ena=HIGH
        sim.tick(2)
        assert sim.position == pytest.approx(0.5)
        assert g.read(24) is False
        sim.tick(3)
        assert sim.position == 1.0 and g.read(23) is True
        g.write(17, True); g.write(27, True)  # close
        sim.tick(4)
        assert sim.position == 0.0 and g.read(24) is True

    def test_hand_toggled_endstop_is_kept_until_the_door_moves(self):
        g = self.setup_gpio()
        sim = DoorSimulator(g, GpioConfig(), travel_time_s=4, position=0.0)
        g.trigger(23, True)  # web simulator panel: press the upper endstop
        sim.tick(1)
        assert g.read(23) is True
        g.write(22, True)  # the door moves → the simulation owns the endstops again
        sim.tick(1)
        assert g.read(23) is False and g.read(24) is False

    def test_idle_motor_does_not_move(self):
        g = self.setup_gpio()
        sim = DoorSimulator(g, GpioConfig(), position=0.5)
        sim.tick(10)
        assert sim.position == 0.5

    def test_inverted_endstops_and_callbacks(self):
        pins = replace(GpioConfig(), invert_end_up=True)
        g = self.setup_gpio(pins)
        seen = []
        g.add_edge_callback(23, seen.append)
        sim = DoorSimulator(g, pins, travel_time_s=1, position=0.5)
        assert g.read(23) is True  # inactive = HIGH when inverted
        seen.clear()  # the initial level change is an edge too
        g.write(22, True)
        sim.tick(1)
        assert g.read(23) is False and seen == [23]
        sim.update_pins(GpioConfig())
        assert g.read(23) is True


# ── factory ──────────────────────────────────────────────────────────────────

class TestFactory:

    def test_mock_hardware(self):
        s = replace(Settings(), use_mock_hardware=True)
        hw = build_hardware(s, lambda: s)
        assert hw.is_mock and hw.simulator is not None
        assert hw.camera_factory is MockCamera
        assert hw.indoor.read().temperature_c is not None

    def test_simulator_optional_and_api_outdoor(self):
        s = replace(Settings(), use_mock_hardware=True, simulate_door=False,
                    outdoor_sensor_type=OutdoorSensorType.API)
        hw = build_hardware(s, lambda: s)
        assert hw.simulator is None
        assert isinstance(hw.outdoor, OpenMeteoSensor)

    def test_missing_rpi_gpio_falls_back_to_mock(self, monkeypatch):
        import coop.hardware as hwmod
        monkeypatch.setattr(hwmod, "RpiGpio", mock.Mock(side_effect=ImportError("no RPi")))
        hw = build_hardware(Settings(), Settings)
        assert hw.is_mock

    def test_real_gpio_with_missing_sensor_drivers_uses_null_sensors(self, monkeypatch):
        import coop.hardware as hwmod
        fake_gpio = MockGpio()
        fake_gpio.is_mock = False
        monkeypatch.setattr(hwmod, "RpiGpio", lambda: fake_gpio)
        monkeypatch.setattr(hwmod, "DhtSensor", mock.Mock(side_effect=ImportError("adafruit")))
        monkeypatch.setattr(hwmod, "_kill_stale_pulsein", lambda: None)
        monkeypatch.setattr(hwmod.os, "name", "posix")
        hw = build_hardware(Settings(), Settings)
        assert not hw.is_mock
        assert isinstance(hw.indoor, NullSensor) and isinstance(hw.outdoor, NullSensor)
        assert isinstance(hw.cpu, CpuTemperatureSensor)
        assert hw.camera_factory is OpenCvCamera
