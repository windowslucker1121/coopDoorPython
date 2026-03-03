"""pytest configuration for the coopDoorPython test suite.

Adds ``src/`` to ``sys.path`` so tests can import project modules without
installing the package.  The ``clean_state`` fixture runs automatically for
every test and resets all shared singleton / module-level state so tests
are fully isolated from each other.
"""

import sys
import os

# Make src/ importable before any test module is collected
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import mock_gpio                                   # noqa: E402  (needs src/ on path)
from protected_dict import protected_dict as gv   # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all shared state before (and after) every test.

    * ``mock_gpio.globalPins`` / ``mock_gpio.callbacks`` — cleared so
      callbacks registered by a previous test's DOOR instance don't fire
      during the current test.
    * ``protected_dict._dictionary`` — cleared so no key-value pairs from a
      previous test leak into the current one.
    """
    mock_gpio.MockGPIO.cleanup()
    gv.reset_for_testing()
    yield
    mock_gpio.MockGPIO.cleanup()
    gv.reset_for_testing()
