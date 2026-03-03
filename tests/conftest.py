"""Shared pytest fixtures and hardware mock setup.

All hardware-dependent modules (RPi.GPIO, board, gpiozero, adafruit_dht,
cv2) are stubbed out here so every test can import ``src/`` modules without
needing physical hardware or Linux.

The ``src/`` directory is added to ``sys.path`` via *pyproject.toml*
``pythonpath = ["src"]``.
"""

from __future__ import annotations

import sys
import types
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Hardware stubs — must be registered BEFORE any src imports
# ---------------------------------------------------------------------------

# RPi.GPIO
_gpio_mock = MagicMock()
_gpio_mock.BCM = 11
_gpio_mock.IN = 1
_gpio_mock.OUT = 0
_gpio_mock.PUD_DOWN = 21
_gpio_mock.PUD_UP = 22
_gpio_mock.HIGH = 1
_gpio_mock.LOW = 0
_gpio_mock.BOTH = 3
_gpio_mock.input.return_value = 0  # default LOW

_rpi_pkg = types.ModuleType("RPi")
_rpi_pkg.GPIO = _gpio_mock
sys.modules.setdefault("RPi", _rpi_pkg)
sys.modules.setdefault("RPi.GPIO", _gpio_mock)

# board
_board_mock = MagicMock()
_board_mock.D21 = 21
_board_mock.D16 = 16
sys.modules.setdefault("board", _board_mock)

# gpiozero
_gpiozero_mock = MagicMock()
_gpiozero_mock.CPUTemperature.return_value.temperature = 42.0
sys.modules.setdefault("gpiozero", _gpiozero_mock)

# lgpio / rpi-lgpio
sys.modules.setdefault("lgpio", MagicMock())

# Adafruit / circuitpython
for _mod in (
    "adafruit_dht",
    "adafruit_circuitpython_dht",
    "busio",
    "digitalio",
):
    sys.modules.setdefault(_mod, MagicMock())

# cv2 (OpenCV)
sys.modules.setdefault("cv2", MagicMock())

# pywebpush / py-vapid
sys.modules.setdefault("pywebpush", MagicMock())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_protected_dict():
    """Reset the protected_dict singleton before each test."""
    from protected_dict import protected_dict

    protected_dict._dictionary = {}
    if hasattr(protected_dict, "_instance"):
        del protected_dict._instance
    yield
    protected_dict._dictionary = {}
    if hasattr(protected_dict, "_instance"):
        del protected_dict._instance


@pytest.fixture()
def gv():
    """Return a fresh protected_dict instance."""
    from protected_dict import protected_dict

    return protected_dict.instance()


@pytest.fixture()
def mock_door():
    """Return a MagicMock that impersonates a DOOR hardware instance."""
    door = MagicMock()
    door.get_state.return_value = "stopped"
    door.get_override.return_value = False
    door.errorState = None
    door.reference_door_endstops_ms = None
    door.get_started_moving_time.return_value = None

    # ErrorState() returns truthy when errorState is set
    def _error_state(state=None, stopDoor=True):
        if state is not None and state != door.errorState:
            door.errorState = state
            return True
        return bool(door.errorState)

    door.ErrorState.side_effect = _error_state
    return door


@pytest.fixture()
def door_service(mock_door):
    """Return a DoorService wrapping the mock_door fixture."""
    from services.door_service import DoorService

    return DoorService(mock_door)


@pytest.fixture()
def log_buffer_fixture():
    return deque(maxlen=100)


@pytest.fixture()
def tmp_config(tmp_path):
    """Return a path to a temporary config.yaml."""
    return str(tmp_path / "config.yaml")


@pytest.fixture()
def tmp_subscriptions(tmp_path):
    """Return a path to a temporary .subscriptions.json."""
    return str(tmp_path / ".subscriptions.json")


@pytest.fixture()
def tmp_secrets(tmp_path):
    """Write a minimal .secrets.yaml and return its path."""
    secrets = tmp_path / ".secrets.yaml"
    secrets.write_text(
        "secrets:\n  vapid_public_key: pub123\n  vapid_private_key: priv456\n",
        encoding="utf-8",
    )
    return str(secrets)
