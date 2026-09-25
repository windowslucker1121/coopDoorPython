"""Tests for the mock hardware used on Windows / dev machines and in tests.

These mocks stand in for real hardware whenever ``RPi.GPIO`` / Adafruit
libraries are unavailable, so their behaviour is part of the contract the
rest of the application relies on.
"""

import pytest

import mock_gpio
from mock_gpio import MockGPIO
from MockDHT11 import MockDHT11
from MockDHT22 import MockDHT22
from mock_temperatur import MockCPUTemperature
from mock_camera import MockCamera
from mock_board import MockBoard
from temperature_sensor import TemperatureSensor


# ── MockGPIO ─────────────────────────────────────────────────────────────────

class TestMockGPIO:

    def test_setup_initialises_pin_low(self):
        MockGPIO.setup(5, MockGPIO.IN)
        assert mock_gpio.globalPins[5] == {"mode": MockGPIO.IN, "state": MockGPIO.LOW}

    def test_output_on_configured_pin(self):
        MockGPIO.setup(17, MockGPIO.OUT)
        MockGPIO.output(17, MockGPIO.HIGH)
        assert MockGPIO.input(17) == MockGPIO.HIGH

    def test_output_on_unconfigured_pin_raises(self):
        with pytest.raises(ValueError):
            MockGPIO.output(99, MockGPIO.HIGH)

    def test_input_on_unconfigured_pin_is_low(self):
        assert MockGPIO.input(42) == MockGPIO.LOW

    def test_trigger_event_sets_state_and_calls_callbacks(self):
        MockGPIO.setup(23, MockGPIO.IN)
        seen = []
        MockGPIO.add_event_detect(23, MockGPIO.BOTH, callback=seen.append)
        MockGPIO.add_event_detect(23, MockGPIO.BOTH, callback=lambda ch: seen.append(("2nd", ch)))

        MockGPIO.trigger_event(23, MockGPIO.HIGH)

        assert MockGPIO.input(23) == MockGPIO.HIGH
        assert seen == [23, ("2nd", 23)]

    def test_trigger_event_on_unconfigured_pin_only_calls_callbacks(self):
        seen = []
        MockGPIO.add_event_detect(7, callback=seen.append)
        MockGPIO.trigger_event(7, MockGPIO.HIGH)
        assert seen == [7]
        assert 7 not in mock_gpio.globalPins

    def test_add_event_detect_without_callback_registers_nothing(self):
        MockGPIO.add_event_detect(8)
        assert 8 not in mock_gpio.callbacks

    def test_get_all_pins_returns_live_dict(self):
        MockGPIO.setup(1, MockGPIO.OUT)
        assert MockGPIO.get_all_pins() is mock_gpio.globalPins

    def test_cleanup_clears_pins_and_callbacks(self):
        MockGPIO.setup(1, MockGPIO.OUT)
        MockGPIO.add_event_detect(1, callback=lambda ch: None)
        MockGPIO.cleanup()
        assert mock_gpio.globalPins == {}
        assert mock_gpio.callbacks == {}

    def test_module_level_gpio_instance(self):
        assert isinstance(mock_gpio.GPIO, MockGPIO)


# ── Mock sensors ─────────────────────────────────────────────────────────────

def _f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


@pytest.mark.parametrize("cls, t_range, h_range", [
    (MockDHT11, (15.0, 35.0), (30.0, 70.0)),
    (MockDHT22, (20.0, 30.0), (30.0, 60.0)),
])
def test_mock_dht_sensors_return_fahrenheit_within_range(cls, t_range, h_range):
    sensor = cls()
    assert isinstance(sensor, TemperatureSensor)
    for _ in range(50):
        temp_f, hum = sensor.get_temperature_and_humidity()
        assert t_range[0] - 0.5 <= _f_to_c(temp_f) <= t_range[1] + 0.5
        assert h_range[0] - 0.5 <= hum <= h_range[1] + 0.5


def test_mock_dht_constructors_accept_hardware_arguments():
    MockDHT11(data_pin=26)
    MockDHT22(data_pin=21, power_pin=20)


def test_mock_cpu_temperature_stays_in_range():
    cpu = MockCPUTemperature()
    for _ in range(50):
        assert 30.0 <= cpu.temperature <= 70.0


def test_mock_camera_returns_jpeg_bytes():
    cam = MockCamera(device_index=3)
    assert cam.device_index == 3
    frame = cam.get_frame()
    assert frame.startswith(b"\xff\xd8") and frame.endswith(b"\xff\xd9")


def test_mock_camera_uninitialised_returns_none():
    cam = MockCamera()
    cam.is_init = False
    assert cam.get_frame() is None


def test_mock_board_exposes_default_pins():
    board = MockBoard()
    assert board.D21 == 21
    assert board.D26 == 26
    assert board.D20 == 20
