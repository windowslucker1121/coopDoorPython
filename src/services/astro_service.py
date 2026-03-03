"""Astronomy service — sunrise / sunset calculations.

Extracted from ``app.py``'s ``get_sunrise_and_sunset()``,
``reload_location_data()``, and ``get_current_time()`` functions.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pytz
from astral import LocationInfo
from astral.geocoder import LocationDatabase, database
from astral.sun import sun
from pytz.tzinfo import BaseTzInfo

from protected_dict import protected_dict

logger = logging.getLogger(__name__)

# Fall-back location used before config is loaded
_DEFAULT_LOCATION = LocationInfo(
    "Boulder", "USA", "America/Denver", 40.01499, -105.27055
)
_DEFAULT_TIMEZONE = pytz.timezone("America/Denver")


class AstroService:
    """Provides sunrise / sunset times and timezone-aware current time.

    Parameters
    ----------
    global_vars_instance:
        The singleton :class:`protected_dict`.  Used by
        :meth:`reload_location` to read the ``location`` dict.
    """

    def __init__(self, global_vars_instance: protected_dict) -> None:
        self._gv = global_vars_instance
        self._location: LocationInfo = _DEFAULT_LOCATION
        self._timezone: BaseTzInfo = _DEFAULT_TIMEZONE

    # ------------------------------------------------------------------

    def reload_location(self) -> None:
        """Re-read the ``location`` key from ``protected_dict`` and rebuild
        the :class:`astral.LocationInfo` and :class:`pytz.BaseTzInfo`
        objects used for all subsequent calculations.

        Should be called once at startup (after config is loaded) and again
        whenever a ``update_location`` SocketIO event is received.
        """
        location: dict[str, object] | None = self._gv.get_value("location")
        if not location:
            logger.warning("AstroService.reload_location: no location in global_vars, using default")
            return

        try:
            self._location = LocationInfo(
                name=str(location["city"]),
                region=str(location["region"]),
                timezone=str(location["timezone"]),
                latitude=float(location["latitude"]),  # type: ignore[arg-type]
                longitude=float(location["longitude"]),  # type: ignore[arg-type]
            )
            self._timezone = pytz.timezone(str(location["timezone"]))
            logger.info(
                "AstroService: location updated to %s / %s (tz=%s)",
                location["city"],
                location["region"],
                location["timezone"],
            )
        except (KeyError, ValueError) as exc:
            logger.error("AstroService.reload_location: invalid location data — %s", exc)

    # ------------------------------------------------------------------

    def get_sunrise_and_sunset(self) -> tuple[datetime, datetime]:
        """Return today's (sunrise, sunset) as timezone-aware datetimes."""
        s = sun(self._location.observer, date=date.today(), tzinfo=self._location.timezone)
        sunrise = s["sunrise"].astimezone(self._timezone)
        sunset = s["sunset"].astimezone(self._timezone)
        return sunrise, sunset

    def get_current_time(self) -> datetime:
        """Return the current time as a timezone-aware datetime."""
        return self._timezone.localize(datetime.now())

    # ------------------------------------------------------------------

    @staticmethod
    def get_valid_locations() -> list[dict[str, object]]:
        """Return a sorted list of all locations known to the astral database.

        Each entry is a dict with keys: ``name``, ``region``, ``timezone``,
        ``latitude``, ``longitude``.
        """
        locations: list[dict[str, object]] = []
        location_database: LocationDatabase = database()
        for location_name, location_info in location_database.items():
            if isinstance(location_info, dict):
                for sub_name, sub_info in location_info.items():
                    locations.append(
                        {
                            "name": f"{location_name} - {sub_name}",
                            "region": sub_info[0].region,
                            "timezone": sub_info[0].timezone,
                            "latitude": sub_info[0].latitude,
                            "longitude": sub_info[0].longitude,
                        }
                    )
        locations.sort(key=lambda x: (str(x["name"]), str(x["region"])))
        return locations
