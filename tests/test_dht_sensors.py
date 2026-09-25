"""Tests for the real DHT11 / DHT22 wrappers with a fake ``adafruit_dht``.

``dht11.py`` / ``dht22.py`` import ``adafruit_dht`` and ``board`` at module
level, which only exist on a Raspberry Pi.  Fake modules are injected into
``sys.modules`` before importing so the retry / re-init / power-cycling logic
can be tested anywhere.
"""

import importlib
import sys
import types

import pytest

from mock_gpio import MockGPIO


class FakeDHTDevice:
    """Programmable stand-in for ``adafruit_dht.DHT11`` / ``DHT22``.

    ``script`` is a list consumed one entry per ``.temperature`` read: either
    a ``(temp_c, humidity)`` tuple or an exception instance to raise.
    """

    instances = []
    script = []

    def __init__(self, pin):
        self.pin = pin
        self.exited = False
        self._hum = None
        FakeDHTDevice.instances.append(self)

    @property
    def temperature(self):
        item = FakeDHTDevice.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        temp, self._hum = item
        return temp

    @property
    def humidity(self):
        return self._hum

    def exit(self):
        self.exited = True


@pytest.fixture
def dht_modules(monkeypatch):
    fake_adafruit = types.ModuleType("adafruit_dht")
    fake_adafruit.DHT11 = FakeDHTDevice
    fake_adafruit.DHT22 = FakeDHTDevice
    monkeypatch.setitem(sys.modules, "adafruit_dht", fake_adafruit)
    monkeypatch.setitem(sys.modules, "board", types.ModuleType("board"))
    FakeDHTDevice.instances = []
    FakeDHTDevice.script = []

    import dht11
    import dht22
    dht11 = importlib.reload(dht11)
    dht22 = importlib.reload(dht22)
    sleeps = []
    monkeypatch.setattr(dht11.time, "sleep", sleeps.append)
    # dht22 falls back to MockGPIO when RPi.GPIO is unavailable
    dht22.GPIO = MockGPIO
    return types.SimpleNamespace(dht11=dht11, dht22=dht22, sleeps=sleeps)


def test_dht11_converts_to_fahrenheit(dht_modules):
    FakeDHTDevice.script = [(20.0, 55.0)]
    sensor = dht_modules.dht11.DHT11(26)
    assert sensor.get_temperature_and_humidity() == (68.0, 55.0)


def test_dht11_retries_runtime_errors(dht_modules):
    FakeDHTDevice.script = [RuntimeError("checksum"), OverflowError(), (0.0, 10.0)]
    sensor = dht_modules.dht11.DHT11(26)
    assert sensor.get_temperature_and_humidity() == (32.0, 10.0)
    assert dht_modules.sleeps == [2.2, 2.2]


def test_dht11_gives_up_after_three_attempts(dht_modules):
    FakeDHTDevice.script = [RuntimeError()] * 3
    sensor = dht_modules.dht11.DHT11(26)
    assert sensor.get_temperature_and_humidity() == (None, None)


def test_dht11_reinitialises_on_oserror(dht_modules):
    FakeDHTDevice.script = [OSError(22, "pulsein"), (10.0, 20.0)]
    sensor = dht_modules.dht11.DHT11(26)
    first = sensor.dht
    assert sensor.get_temperature_and_humidity() == (50.0, 20.0)
    assert first.exited is True
    assert sensor.dht is not first
    assert len(FakeDHTDevice.instances) == 2


def test_dht22_power_cycles_sensor(dht_modules):
    MockGPIO.setup(20, MockGPIO.OUT)  # ensure pin exists for output()
    FakeDHTDevice.script = [(25.0, 40.0)]
    levels = []
    original_output = MockGPIO.output

    def spy_output(pin, state):
        levels.append((pin, state))
        original_output(pin, state)

    dht_modules.dht22.GPIO = types.SimpleNamespace(
        BCM=MockGPIO.BCM, OUT=MockGPIO.OUT, HIGH=MockGPIO.HIGH, LOW=MockGPIO.LOW,
        setmode=MockGPIO.setmode, setup=MockGPIO.setup, output=spy_output,
    )
    sensor = dht_modules.dht22.DHT22(21, power_pin=20)
    assert sensor.get_temperature_and_humidity() == (77.0, 40.0)
    assert levels == [(20, "LOW"), (20, "HIGH"), (20, "LOW")]


def test_dht22_without_power_pin(dht_modules):
    FakeDHTDevice.script = [(0.0, 50.0)]
    sensor = dht_modules.dht22.DHT22(21)
    assert sensor.pwr is None
    assert sensor.get_temperature_and_humidity() == (32.0, 50.0)


def test_dht22_all_attempts_fail(dht_modules):
    FakeDHTDevice.script = [RuntimeError(), OSError(), RuntimeError()]
    sensor = dht_modules.dht22.DHT22(21)
    assert sensor.get_temperature_and_humidity() == (None, None)
