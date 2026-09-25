"""Shared fixtures for the coop test suite.

Nothing here touches real hardware, the network or the host system:

* :class:`DoorRig` — mock GPIO + ``DoorDriver`` + ``DoorController`` on a
  :class:`~coop.clock.FakeClock`, with helpers to press endstops / switches.
* ``make_app`` — a fully wired :class:`~coop.application.Application` with
  injected fakes (mock hardware, fake clock, mock Wi-Fi, recording notifier,
  system service that never changes the host), rooted in ``tmp_path``.
* ``client`` / ``sio`` — Flask and Socket.IO test clients for that app.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import datetime

import pytest
import pytz

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

from coop.clock import FakeClock  # noqa: E402
from coop.config import ConfigStore, Mode, Settings  # noqa: E402
from coop.door.controller import DoorController  # noqa: E402
from coop.door.driver import DoorDriver  # noqa: E402
from coop.door.model import DesiredState, DoorState  # noqa: E402
from coop.hardware import Hardware  # noqa: E402
from coop.hardware.camera import MockCamera  # noqa: E402
from coop.hardware.gpio import MockGpio  # noqa: E402
from coop.hardware.sensors import Reading, TemperatureSensor  # noqa: E402
from coop.paths import Paths  # noqa: E402
from coop.services.sun import SunCalculator  # noqa: E402
from coop.services.wifi import WifiManager  # noqa: E402

DENVER = pytz.timezone("America/Denver")
REF_MS = 10_000.0


def at(hour: int, minute: int = 0, day: int = 1, month: int = 6, year: int = 2025) -> datetime:
    return DENVER.localize(datetime(year, month, day, hour, minute))


# ─────────────────────────────── door rig ───────────────────────────────────

class DoorRig:
    """Driver + controller on mock GPIO with a fake clock."""

    def __init__(self, tmp_path, *, now: datetime | None = None, mode: Mode = Mode.MANUAL,
                 reference_ms: float | None = REF_MS, **settings):
        self.store = ConfigStore(str(tmp_path / "config.yaml"))
        self.store.load()
        self.store.update(mode=mode, reference_travel_ms=reference_ms, **settings)
        self.clock = FakeClock(now or at(12), DENVER)
        self.gpio = MockGpio()
        self.pins = self.store.settings.gpio
        self.driver = DoorDriver(self.gpio, self.pins, self.clock)
        self.notifications: list[tuple[str, str]] = []
        self.sun = SunCalculator(self.store.settings.location)
        self.controller = DoorController(self.driver, self.store, self.clock, lambda: self.sun,
                                         lambda t, b: self.notifications.append((t, b)))

    # ── time ──
    def step(self, n: int = 1, advance: float = 0.5) -> None:
        for _ in range(n):
            self.controller.step()
            self.clock.advance(advance)

    def run_for(self, seconds: float, advance: float = 0.5) -> None:
        self.step(int(seconds / advance), advance)

    # ── inputs ──
    def upper(self, active: bool = True, edge: bool = True) -> None:
        (self.gpio.trigger if edge else self.gpio.set_input)(self.pins.endstop_up, active)

    def lower(self, active: bool = True, edge: bool = True) -> None:
        (self.gpio.trigger if edge else self.gpio.set_input)(self.pins.endstop_down, active)

    def switch(self, position: str | None) -> None:
        self.gpio.set_input(self.pins.override_open, position == "open")
        self.gpio.set_input(self.pins.override_close, position == "close")

    # ── observations ──
    @property
    def state(self) -> DoorState:
        return self.driver.state

    @property
    def desired(self) -> DesiredState:
        return self.controller.desired

    @property
    def motor(self) -> tuple[bool, bool, bool]:
        p = self.pins
        return (self.gpio.read(p.motor_in1), self.gpio.read(p.motor_in2), self.gpio.read(p.motor_ena))

    def set_mode(self, mode: Mode) -> None:
        self.store.update(mode=mode)


@pytest.fixture
def rig_factory(tmp_path):
    def make(**kwargs) -> DoorRig:
        return DoorRig(tmp_path, **kwargs)
    return make


@pytest.fixture
def rig(rig_factory) -> DoorRig:
    return rig_factory()


# ─────────────────────────────── application ────────────────────────────────

class FixedSensor(TemperatureSensor):
    def __init__(self, name="fixed", temperature=None, humidity=None):
        self.name = name
        self.reading = Reading(temperature, humidity)

    def read(self) -> Reading:
        return self.reading


class RecordingNotifier:
    enabled = True

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))


class FakeSystem:
    """SystemService stand-in recording every host-changing action."""

    def __init__(self):
        self.actions: list[tuple] = []
        self.version_value = "abc1234"
        self.fail_time: Exception | None = None
        self.reboot_error: Exception | None = None

    def uptime(self) -> str:
        return "1 day(s), 2 hour(s), 3 minute(s), 4 second(s)"

    def metrics(self) -> dict:
        return {"cpu_percent": 12.34, "ram_used_mb": 512.4, "ram_total_mb": 1024.0, "ram_percent": 50.04,
                "disk_used_gb": 3.21, "disk_total_gb": 29.87, "disk_percent": 10.75}

    def version(self) -> str:
        return self.version_value

    def record_git_version(self) -> None:
        pass

    def set_time(self, value: str) -> None:
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        if self.fail_time:
            raise self.fail_time
        self.actions.append(("set_time", value))

    def reboot(self) -> None:
        if self.reboot_error:
            raise self.reboot_error
        self.actions.append(("reboot",))

    def start_update(self) -> None:
        self.actions.append(("update",))


class FakeWifi(WifiManager):
    def __init__(self):
        super().__init__(mock=True)
        self.ap_mode = False
        self.connect_result = True
        self.calls: list[tuple] = []

    def is_ap_mode_active(self) -> bool:
        return self.ap_mode

    def connect(self, ssid, password, timeout=30):
        self.calls.append(("connect", ssid, password))
        return self.connect_result

    def start_ap(self, ssid, password, ap_ip="10.42.0.1"):
        self.calls.append(("start_ap", ssid, password))
        return True


@pytest.fixture
def paths(tmp_path) -> Paths:
    return Paths(str(tmp_path), src_dir=os.path.abspath(SRC))


@pytest.fixture
def make_app(paths):
    from coop.application import Application
    from coop.logging_setup import LogBuffer

    created = []

    def make(config: dict | None = None, *, now: datetime | None = None, **overrides) -> Application:
        if config is not None:
            import ruamel.yaml as YAML
            with open(paths.config, "w") as f:
                YAML.YAML().dump(config, f)
        gpio = MockGpio()
        hardware = overrides.pop("hardware", None) or Hardware(
            gpio=gpio,
            indoor=FixedSensor("indoor", 21.5, 45.25),
            outdoor=FixedSensor("outdoor", 4.0, 80.0),
            cpu=FixedSensor("cpu", 51.23),
            camera_factory=MockCamera,
        )
        app = Application(
            paths,
            clock=overrides.pop("clock", None) or FakeClock(now or at(12), DENVER),
            hardware=hardware,
            wifi=overrides.pop("wifi", None) or FakeWifi(),
            notifier=overrides.pop("notifier", None) or RecordingNotifier(),
            system=overrides.pop("system", None) or FakeSystem(),
            log_buffer=LogBuffer(),
        )
        created.append(app)
        return app

    yield make
    for app in created:
        app.stop()


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def web(app):
    from coop.web import create_web
    flask_app, socketio = create_web(app, async_mode="threading")
    flask_app.config["TESTING"] = True
    return flask_app, socketio


@pytest.fixture
def client(web):
    return web[0].test_client()


@pytest.fixture
def sio(web):
    flask_app, socketio = web
    sc = socketio.test_client(flask_app)
    sc.get_received()
    yield sc
    if sc.is_connected():
        sc.disconnect()


def received(sc, event: str) -> list:
    return [m["args"] for m in sc.get_received() if m["name"] == event]


__all__ = ["DoorRig", "at", "DENVER", "REF_MS", "received", "FixedSensor", "Settings", "replace"]
