import logging
import time

import requests

from temperature_sensor import TemperatureSensor

logger = logging.getLogger(__name__)

_API_URL = "https://api.open-meteo.com/v1/forecast"
_DEFAULT_CACHE_SECONDS = 300  # 5 minutes — avoids hammering the free API


class LocationAPITemperatureSensor(TemperatureSensor):
    """Fetches outdoor temperature and humidity from the Open-Meteo API.

    Open-Meteo (https://open-meteo.com) is a free, no-API-key-required weather
    service.  This sensor uses the configured location latitude/longitude to
    request the current ``temperature_2m`` and ``relative_humidity_2m`` values.

    Results are cached for *cache_seconds* (default 5 min) so that the normal
    temperature task polling interval (~2.5 s) does not generate an HTTP
    request on every iteration.

    Args:
        get_location: callable that returns a dict with at least the keys
            ``"latitude"`` and ``"longitude"`` (as returned by
            ``global_vars.instance().get_value("location")``).
        cache_seconds: how long a successful fetch is reused before a new
            HTTP request is made.
    """

    def __init__(self, get_location, cache_seconds: int = _DEFAULT_CACHE_SECONDS):
        self._get_location = get_location
        self._cache_seconds = cache_seconds
        self._cached_result: tuple = (None, None)
        self._last_fetch_time: float = 0.0

    def get_temperature_and_humidity(self) -> tuple:
        now = time.time()
        if now - self._last_fetch_time < self._cache_seconds:
            return self._cached_result

        try:
            location = self._get_location()
            lat = float(location["latitude"])
            lon = float(location["longitude"])

            response = requests.get(
                _API_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m",
                    "temperature_unit": "fahrenheit",
                    "timezone": "auto",
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            current = data.get("current", {})
            temp_f = current.get("temperature_2m")
            hum = current.get("relative_humidity_2m")

            result = (
                float(temp_f) if temp_f is not None else None,
                float(hum) if hum is not None else None,
            )
            self._cached_result = result
            self._last_fetch_time = now
            logger.debug(
                "LocationAPITemperatureSensor: %.1f°F  %.0f%%",
                temp_f if temp_f is not None else float("nan"),
                hum if hum is not None else float("nan"),
            )

        except requests.exceptions.RequestException as e:
            logger.warning("LocationAPITemperatureSensor: HTTP error — %s", e)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("LocationAPITemperatureSensor: unexpected response — %s", e)

        return self._cached_result
