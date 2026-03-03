"""Configuration service — load and persist ``config.yaml``.

Extracted from ``app.py``'s ``load_config()`` / ``save_config()`` functions.
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any

import ruamel.yaml as YAML

from protected_dict import protected_dict

logger = logging.getLogger(__name__)

# Keys that are read from / written to config.yaml
_CONFIG_KEYS: tuple[str, ...] = (
    "auto_mode",
    "sunrise_offset",
    "sunset_offset",
    "location",
    "consoleLogToFile",
    "csvLog",
    "enable_camera",
    "camera_index",
    "reference_door_endstops_ms",
)

_DEFAULTS: dict[str, Any] = {
    "auto_mode": "True",
    "sunrise_offset": 0,
    "sunset_offset": 0,
    "location": {
        "city": "Boulder",
        "region": "USA",
        "timezone": "America/Denver",
        "latitude": 40.01499,
        "longitude": -105.27055,
    },
    "consoleLogToFile": False,
    "csvLog": True,
    "enable_camera": False,
    "camera_index": 0,
    "reference_door_endstops_ms": None,
}


class ConfigService:
    """Loads and persists the application configuration.

    Parameters
    ----------
    config_path:
        Absolute path to ``config.yaml``.
    global_vars_instance:
        The singleton :class:`protected_dict` instance used as the shared
        state bus.
    """

    def __init__(
        self,
        config_path: str,
        global_vars_instance: protected_dict,
    ) -> None:
        self._config_path = config_path
        self._gv = global_vars_instance
        self._lock = Lock()

    # ------------------------------------------------------------------

    def load(self) -> None:
        """Read ``config.yaml`` into ``protected_dict``.

        If the file does not exist, defaults are written to ``protected_dict``
        and a new ``config.yaml`` is created.
        """
        config_to_set: dict[str, Any] = dict(_DEFAULTS)
        save_new = False

        with self._lock:
            if os.path.exists(self._config_path):
                try:
                    with open(self._config_path, "r", encoding="utf-8") as fh:
                        yaml = YAML.YAML()
                        loaded = yaml.load(fh.read())
                        if loaded and isinstance(loaded, dict):
                            config_to_set.update(loaded)
                except OSError as exc:
                    logger.error("ConfigService.load: cannot read %s: %s", self._config_path, exc)
            else:
                save_new = True

            self._gv.set_values(config_to_set)

        if save_new:
            logger.info("No config file found — writing defaults to %s", self._config_path)
            self.save()

    # ------------------------------------------------------------------

    def save(self) -> None:
        """Write the current ``protected_dict`` config keys to ``config.yaml``."""
        with self._lock:
            to_dump: dict[str, Any] = {k: self._gv.get_value(k) for k in _CONFIG_KEYS}
            try:
                with open(self._config_path, "w", encoding="utf-8") as fh:
                    yaml = YAML.YAML()
                    yaml.dump(to_dump, fh)
            except OSError as exc:
                logger.error(
                    "ConfigService.save: cannot write %s: %s", self._config_path, exc
                )
                raise
