"""Tests for :class:`LocationAPITemperatureSensor` (Open-Meteo outdoor data)."""

from unittest import mock

import pytest
import requests

import location_temperature_sensor as lts
from location_temperature_sensor import LocationAPITemperatureSensor

LOCATION = {"latitude": 40.0, "longitude": -105.0}


def _response(payload, status_ok=True):
    resp = mock.Mock()
    resp.json.return_value = payload
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
    return resp


@pytest.fixture
def fake_get(monkeypatch):
    get = mock.Mock()
    monkeypatch.setattr(lts.requests, "get", get)
    return get


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(lts.time, "time", lambda: now["t"])
    return now


def test_successful_fetch_returns_floats_and_sends_expected_params(fake_get, clock):
    fake_get.return_value = _response({"current": {"temperature_2m": 50, "relative_humidity_2m": 40}})
    sensor = LocationAPITemperatureSensor(lambda: LOCATION)

    assert sensor.get_temperature_and_humidity() == (50.0, 40.0)

    args, kwargs = fake_get.call_args
    assert args[0] == "https://api.open-meteo.com/v1/forecast"
    assert kwargs["params"]["latitude"] == 40.0
    assert kwargs["params"]["longitude"] == -105.0
    assert kwargs["params"]["temperature_unit"] == "fahrenheit"
    assert kwargs["params"]["current"] == "temperature_2m,relative_humidity_2m"
    assert kwargs["timeout"] == 10


def test_result_is_cached_until_cache_expires(fake_get, clock):
    fake_get.return_value = _response({"current": {"temperature_2m": 50, "relative_humidity_2m": 40}})
    sensor = LocationAPITemperatureSensor(lambda: LOCATION, cache_seconds=300)

    sensor.get_temperature_and_humidity()
    clock["t"] += 299
    sensor.get_temperature_and_humidity()
    assert fake_get.call_count == 1

    clock["t"] += 2
    fake_get.return_value = _response({"current": {"temperature_2m": 60, "relative_humidity_2m": 30}})
    assert sensor.get_temperature_and_humidity() == (60.0, 30.0)
    assert fake_get.call_count == 2


def test_missing_fields_become_none(fake_get, clock):
    fake_get.return_value = _response({"current": {}})
    sensor = LocationAPITemperatureSensor(lambda: LOCATION)
    assert sensor.get_temperature_and_humidity() == (None, None)


def test_http_error_returns_previous_cached_value_and_backs_off(fake_get, clock):
    fake_get.return_value = _response({"current": {"temperature_2m": 50, "relative_humidity_2m": 40}})
    sensor = LocationAPITemperatureSensor(lambda: LOCATION, cache_seconds=10)
    sensor.get_temperature_and_humidity()

    clock["t"] += 11
    fake_get.return_value = _response({}, status_ok=False)
    assert sensor.get_temperature_and_humidity() == (50.0, 40.0)

    # The failed attempt also resets the cache timer (no hammering on errors)
    clock["t"] += 5
    sensor.get_temperature_and_humidity()
    assert fake_get.call_count == 2


def test_network_exception_before_first_success_returns_none(fake_get, clock):
    fake_get.side_effect = requests.exceptions.ConnectionError("offline")
    sensor = LocationAPITemperatureSensor(lambda: LOCATION)
    assert sensor.get_temperature_and_humidity() == (None, None)


@pytest.mark.parametrize("location", [
    {},                                         # KeyError
    {"latitude": "abc", "longitude": 1},        # ValueError
    {"latitude": None, "longitude": 1},         # TypeError
])
def test_bad_location_is_handled(fake_get, clock, location):
    sensor = LocationAPITemperatureSensor(lambda: location)
    assert sensor.get_temperature_and_humidity() == (None, None)
    fake_get.assert_not_called()


def test_non_numeric_payload_is_handled(fake_get, clock):
    fake_get.return_value = _response({"current": {"temperature_2m": "warm", "relative_humidity_2m": 1}})
    sensor = LocationAPITemperatureSensor(lambda: LOCATION)
    assert sensor.get_temperature_and_humidity() == (None, None)
