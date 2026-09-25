"""Sunrise / sunset calculations and the astral location list."""

from __future__ import annotations

import functools
import logging
from datetime import date, datetime

from astral import LocationInfo
from astral.geocoder import database
from astral.sun import sun

from ..config import LocationConfig

logger = logging.getLogger(__name__)


class SunCalculator:
    """Sun times for a location, cached per day."""

    def __init__(self, location: LocationConfig):
        self._location = location
        self._info = LocationInfo(location.city, location.region, location.timezone,
                                  location.latitude, location.longitude)
        self._cache: dict[date, tuple[datetime, datetime] | None] = {}

    @property
    def location(self) -> LocationConfig:
        return self._location

    def sun_times(self, day: date) -> tuple[datetime, datetime] | None:
        """(sunrise, sunset) in the location's timezone, or ``None`` when the
        sun does not rise or set that day (polar regions)."""
        if day not in self._cache:
            try:
                s = sun(self._info.observer, date=day, tzinfo=self._location.tz)
                self._cache[day] = (s["sunrise"], s["sunset"])
            except ValueError as e:
                logger.warning("No sunrise/sunset on %s at %s: %s", day, self._location.city, e)
                self._cache[day] = None
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[day]


@functools.lru_cache(maxsize=1)
def list_locations() -> tuple[dict, ...]:
    """All cities in astral's database, sorted by name (static → cached)."""
    locations = []
    for group, entries in database().items():
        if not isinstance(entries, dict):
            continue
        for key, infos in entries.items():
            info = infos[0]
            locations.append({
                "name": f"{group} - {key}",
                "region": info.region,
                "timezone": info.timezone,
                "latitude": info.latitude,
                "longitude": info.longitude,
            })
    locations.sort(key=lambda x: (x["name"], x["region"]))
    return tuple(locations)
