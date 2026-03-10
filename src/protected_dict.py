"""Thread-safe singleton state store backed by a typed :class:`~app_state.AppState`.

The ``protected_dict`` class provides the same ``get_value`` / ``set_value`` /
``get_values`` / ``set_values`` / ``get_all`` interface that the rest of the
codebase already uses, but now routes all reads and writes through an
:class:`~app_state.AppState` dataclass instance instead of a plain ``dict``.

Benefits over the old plain-dict approach
------------------------------------------
* Every field has a declared Python type → IDE autocompletion and mypy work.
* ``auto_mode`` is now a proper ``bool`` (was the string ``"True"``/``"False"``).
* All defaults live in ``AppState`` — no more hardcoded fallbacks scattered
  across ``load_config`` and module-level initialisations.
* Unknown keys (e.g. from old config.yaml versions) are accepted without error
  and stored in a separate ``_extras`` dict so no information is lost.

Backward compatibility
-----------------------
Legacy callers that write ``"True"`` / ``"False"`` strings to boolean fields
(e.g. from old config.yaml files or not-yet-updated call-sites) have their
values silently coerced to ``bool`` by :func:`_coerce`.  All other types pass
through unchanged.
"""

import threading
import copy
import dataclasses
import logging

from app_state import AppState

logger = logging.getLogger(__name__)


def _coerce(current, value):
    """Coerce *value* to ``bool`` when *current* is ``bool`` and *value* is a string.

    This handles legacy ``"True"`` / ``"False"`` strings coming from old
    ``config.yaml`` files or call-sites that haven't been updated yet.
    All other type combinations are passed through unchanged.
    """
    if isinstance(current, bool) and isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return value


class protected_dict:
    _instance_lock = threading.Lock()
    # Class-level AppState holds all typed runtime state (shared singleton).
    _state: AppState = AppState()
    # Overflow dict for any keys not declared on AppState (forward compat).
    _extras: dict = {}

    @classmethod
    def instance(cls):
        if not hasattr(cls, "_instance"):
            with cls._instance_lock:
                if not hasattr(cls, "_instance"):
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Internal helpers (caller must already hold _instance_lock)
    # ------------------------------------------------------------------

    def _write(self, key: str, value) -> None:
        """Write one key/value pair to AppState or extras (lock already held)."""
        if hasattr(self._state, key):
            current = getattr(self._state, key)
            setattr(self._state, key, _coerce(current, value))
        else:
            self._extras[key] = value

    def _read(self, key: str):
        """Read one key from AppState or extras (lock already held)."""
        if hasattr(self._state, key):
            return getattr(self._state, key)
        return self._extras.get(key)

    # ------------------------------------------------------------------
    # Public API (unchanged interface)
    # ------------------------------------------------------------------

    def set_value(self, key: str, value) -> None:
        with self._instance_lock:
            self._write(key, copy.deepcopy(value))

    def get_value(self, key: str):
        with self._instance_lock:
            return copy.deepcopy(self._read(key))

    def set_values(self, values_dict: dict) -> None:
        with self._instance_lock:
            for key, value in values_dict.items():
                self._write(key, copy.deepcopy(value))

    def get_values(self, keys: list) -> list:
        with self._instance_lock:
            return [copy.deepcopy(self._read(key)) for key in keys]

    def get_all(self) -> dict:
        with self._instance_lock:
            d = dataclasses.asdict(self._state)
            if self._extras:
                d.update(copy.deepcopy(self._extras))
            return d

    @classmethod
    def reset_for_testing(cls) -> None:
        """Reset all state to ``AppState`` defaults.

        Recreates the ``AppState`` instance and clears the extras dict so
        every test starts with a clean, fully typed slate.
        Intended for use in test fixtures only.
        """
        with cls._instance_lock:
            cls._state = AppState()
            cls._extras = {}


if __name__ == "__main__":
    singleton_dict = protected_dict.instance()
    singleton_dict.set_value("auto_mode", True)
    print(singleton_dict.get_value("auto_mode"))          # True (bool)
    singleton_dict.set_values({"sunrise_offset": 5, "sunset_offset": 10})
    print(singleton_dict.get_values(["sunrise_offset", "sunset_offset"]))
    # Legacy string coercion still works:
    singleton_dict.set_value("auto_mode", "False")
    print(singleton_dict.get_value("auto_mode"))          # False (bool)
