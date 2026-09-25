"""pytest configuration for the coopDoorPython test suite.

Adds ``src/`` to ``sys.path`` so tests can import project modules without
installing the package.  The ``clean_state`` fixture runs automatically for
every test and resets all shared singleton / module-level state so tests
are fully isolated from each other.

The ``app_module`` / ``app_env`` fixtures give tests access to ``src/app.py``
(the Flask + Socket.IO application) with every side effect that would touch
the real machine redirected to a temporary directory or a fake.
"""

import sys
import os
from unittest import mock

# Make src/ importable before any test module is collected
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import mock_gpio                                   # noqa: E402  (needs src/ on path)
import door as door_module                         # noqa: E402
from protected_dict import protected_dict as gv   # noqa: E402


# Module-level configuration globals of door.py.  DOOR.__init__ and the
# /api/gpio-config endpoint overwrite these, so they are snapshotted once and
# restored around every test.
_DOOR_GLOBALS = (
    "in1", "in2", "ena", "end_up", "end_down", "o_pin", "c_pin",
    "invert_end_up", "invert_end_down", "referenceSequenceTimeout",
)
_DOOR_DEFAULTS = {name: getattr(door_module, name) for name in _DOOR_GLOBALS}


def _restore_door_globals():
    for name, value in _DOOR_DEFAULTS.items():
        setattr(door_module, name, value)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all shared state before (and after) every test.

    * ``mock_gpio.globalPins`` / ``mock_gpio.callbacks`` — cleared so
      callbacks registered by a previous test's DOOR instance don't fire
      during the current test.
    * ``protected_dict._dictionary`` — cleared so no key-value pairs from a
      previous test leak into the current one.
    * ``door`` module pin/timeout globals — restored to their defaults.
    """
    mock_gpio.MockGPIO.cleanup()
    gv.reset_for_testing()
    _restore_door_globals()
    yield
    mock_gpio.MockGPIO.cleanup()
    gv.reset_for_testing()
    _restore_door_globals()


# ---------------------------------------------------------------------------
# app.py fixtures
# ---------------------------------------------------------------------------

class FakeWifiManager:
    """Stand-in for :class:`wifi_manager.WifiManager` that never shells out."""

    def __init__(self):
        self.ap_mode = False
        self.ethernet = False
        self.connection = {"ssid": "Fake-WiFi"}
        self.networks = [{"ssid": "Net-A", "signal": 70, "security": "WPA2"}]
        self.connect_result = True
        self.start_ap_result = True
        self.connect_calls = []
        self.start_ap_calls = []

    def is_ap_mode_active(self):
        return self.ap_mode

    def is_ethernet_connected(self):
        return self.ethernet

    def get_current_connection(self):
        return self.connection

    def scan_networks(self):
        return self.networks

    def connect(self, ssid, password, timeout=30):
        self.connect_calls.append((ssid, password, timeout))
        return self.connect_result

    def start_ap(self, ssid, password):
        self.start_ap_calls.append((ssid, password))
        return self.start_ap_result


@pytest.fixture(scope="session")
def app_module():
    """Import ``src/app.py`` once, without its process-wide side effects.

    * ``gevent.monkey.patch_all`` is replaced by a no-op so the threading /
      time modules used by the other (thread based) tests stay untouched.
    * ``subprocess.run`` is stubbed during import so the module-level
      ``killall libgpiod_pulsein*`` calls never run on the test machine.
    """
    import gevent.monkey

    original_patch_all = gevent.monkey.patch_all
    gevent.monkey.patch_all = lambda *a, **k: None
    try:
        with mock.patch("subprocess.run"):
            import app
    finally:
        gevent.monkey.patch_all = original_patch_all
    return app


@pytest.fixture
def app_env(app_module, tmp_path, monkeypatch):
    """Per-test isolated environment for ``app.py``.

    * ``config.yaml`` / ``log/`` live in ``tmp_path`` (``root_path`` and
      ``config_filename`` are redirected).
    * The working directory is ``tmp_path`` (``.subscriptions.json`` and
      ``version.txt`` are resolved relative to the CWD).
    * ``wifi_mgr`` is a :class:`FakeWifiManager`.
    * Location globals, the cached VAPID key and the log buffer are reset.
    """
    app = app_module
    monkeypatch.setattr(app, "root_path", str(tmp_path))
    monkeypatch.setattr(app, "config_filename", str(tmp_path / "config.yaml"))
    monkeypatch.chdir(tmp_path)
    fake_wifi = FakeWifiManager()
    monkeypatch.setattr(app, "wifi_mgr", fake_wifi)
    monkeypatch.setattr(app, "vapid_private_key", None)
    monkeypatch.setattr(app, "boulder", app.boulder)
    monkeypatch.setattr(app, "timezone", app.timezone)
    app.log_buffer.clear()
    app.app.config["TESTING"] = True
    yield app
    app.log_buffer.clear()


@pytest.fixture
def fake_wifi(app_env):
    return app_env.wifi_mgr


@pytest.fixture
def client(app_env):
    return app_env.app.test_client()


@pytest.fixture
def sio_client(app_env):
    sc = app_env.socketio.test_client(app_env.app)
    sc.get_received()  # drain anything sent on connect
    yield sc
    if sc.is_connected():
        sc.disconnect()
