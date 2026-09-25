"""Composition root: builds, wires and runs all components."""

from __future__ import annotations

import base64
import logging
import sys
from typing import Callable

from .clock import Clock
from .config import ConfigStore, Mode, Settings
from .door.controller import DoorController
from .door.driver import DoorDriver
from .door.model import DesiredState
from .hardware import Hardware, build_hardware
from .hardware.camera import CameraError
from .logging_setup import LogBuffer, configure_logging
from .paths import Paths
from .services.datalog import CsvDataLogger
from .services.environment import EnvironmentMonitor
from .services.notifications import PushNotifier, SubscriptionStore, load_vapid_keys
from .services.sun import SunCalculator
from .services.system import SystemService
from .services.wifi import WifiManager, run_wifi_watchdog
from .workers import Worker, once

logger = logging.getLogger(__name__)

Emit = Callable[[str, object], None]


class Application:
    """Everything the web layer and the workers need, fully wired.

    Every collaborator can be injected, which is how the tests run the real
    application against mock hardware, a fake clock, a fake Wi-Fi manager…
    """

    WIFI_WATCHDOG_DELAY_S = 15.0
    WIFI_FALLBACK_DELAY_S = 5.0
    BROADCAST_INTERVAL_S = 1.0
    CAMERA_INTERVAL_S = 0.1
    SIMULATOR_INTERVAL_S = 0.05

    def __init__(self, paths: Paths | None = None, *, clock: Clock | None = None,
                 hardware: Hardware | None = None, wifi: WifiManager | None = None,
                 notifier: PushNotifier | None = None, system: SystemService | None = None,
                 log_buffer: LogBuffer | None = None):
        self.paths = paths or Paths.default()
        self.config = ConfigStore(self.paths.config)
        settings = self.config.load()

        self.clock = clock or Clock(lambda: self.config.settings.location.tz)
        self.log_buffer = log_buffer or LogBuffer()
        self.hardware = hardware or build_hardware(settings, lambda: self.config.settings)
        self._sun = SunCalculator(settings.location)

        self.vapid_public_key, vapid_private_key = load_vapid_keys(self.paths.secrets)
        self.subscriptions = SubscriptionStore(self.paths.subscriptions)
        self.notifier = notifier or PushNotifier(self.subscriptions, vapid_private_key)

        self.driver = DoorDriver(self.hardware.gpio, settings.gpio, self.clock)
        self.controller = DoorController(self.driver, self.config, self.clock, lambda: self._sun,
                                         self.notifier.notify)
        self.environment = EnvironmentMonitor(self.hardware.indoor, self.hardware.outdoor,
                                              self.hardware.cpu, self.clock)
        simulated_host = self.hardware.is_mock or not sys.platform.startswith("linux")
        self.wifi = wifi or WifiManager(mock=simulated_host)
        self.system = system or SystemService(self.paths, allow_system_changes=not simulated_host)

        self.csv_logger = CsvDataLogger(self.paths.log_dir, self._csv_row, self.clock.now)

        self.emit: Emit = lambda event, payload: None  # replaced by the web layer
        self._sim_last: float | None = None
        self.workers: list[Worker] = []
        self.config.subscribe(self._on_settings_changed)

    def _csv_row(self) -> dict:
        """Dashboard snapshot for the CSV log (scalar values only)."""
        from .web.payloads import dashboard_payload
        return {k: v for k, v in dashboard_payload(self).items() if not isinstance(v, (list, dict))}

    # ── accessors ────────────────────────────────────────────────────
    @property
    def settings(self) -> Settings:
        return self.config.settings

    @property
    def sun(self) -> SunCalculator:
        return self._sun

    # ── use cases (called by the web layer) ──────────────────────────
    def manual_command(self, desired: DesiredState) -> None:
        """UI open / close / stop: switch to (persisted) manual mode."""
        if self.settings.mode is not Mode.MANUAL:
            logger.info("[Manual Mode] %s requested - disabling auto/timer mode.", desired.value.upper())
            self.config.update(mode=Mode.MANUAL)
        self.controller.command(desired)

    def set_mode(self, mode: Mode, enabled: bool) -> None:
        """Toggle auto / timer mode (switching one on switches the other off).

        Raises ``ValueError`` when a schedule mode is enabled before the door
        has been calibrated (the controller could not supervise it)."""
        current = self.settings.mode
        if enabled and mode is not Mode.MANUAL and not self.settings.reference_travel_ms:
            raise ValueError("Calibrate the door first - schedules need the measured travel time.")
        if enabled:
            new = mode
        else:
            new = Mode.MANUAL if current is mode else current
        if new is not current:
            logger.info("Mode: %s -> %s", current.value, new.value)
            self.config.update(mode=new)

    # ── settings changes ─────────────────────────────────────────────
    def _on_settings_changed(self, old: Settings, new: Settings) -> None:
        if new.location != old.location:
            self._sun = SunCalculator(new.location)
            logger.info("Location updated to %s (%s)", new.location.city, new.location.timezone)
        if new.gpio != old.gpio:
            self.driver.apply_live_settings(new.gpio)
            if self.hardware.simulator:
                self.hardware.simulator.update_pins(self.driver.pins)
        if new.log_level != old.log_level:
            logging.getLogger().setLevel(new.log_level)

    # ── workers ──────────────────────────────────────────────────────
    def build_workers(self) -> list[Worker]:
        s = self.settings
        workers = [
            Worker("door", self.controller.step, on_error=lambda e: self.driver.stop()),
            Worker("environment", self.environment.poll),
            Worker("broadcast", self._broadcast),
        ]
        if s.csv_log:
            workers.append(Worker("csv-log", self.csv_logger.write_row))
        if s.enable_camera:
            workers.append(Worker("camera", CameraStreamer(self).step))
        if self.hardware.simulator:
            workers.append(Worker("door-simulator", self._simulate))
        if not self.wifi.mock:
            workers.append(Worker("wifi-watchdog", once(lambda: run_wifi_watchdog(self.wifi, self.settings.wifi)),
                                  initial_delay=self.WIFI_WATCHDOG_DELAY_S))
        return workers

    def start(self) -> None:
        self.workers = [w.start() for w in self.build_workers()]
        logger.info("Started workers: %s", ", ".join(w.name for w in self.workers))

    def stop(self) -> None:
        for w in self.workers:
            w.stop()
        self.driver.shutdown()
        self.hardware.gpio.cleanup()

    def _broadcast(self) -> float:
        from .web.payloads import dashboard_payload
        self.emit("data", dashboard_payload(self))
        return self.BROADCAST_INTERVAL_S

    def _simulate(self) -> float:
        now = self.clock.monotonic()
        dt = 0.0 if self._sim_last is None else now - self._sim_last
        self._sim_last = now
        self.hardware.simulator.tick(dt)
        return self.SIMULATOR_INTERVAL_S

    # ── process entry ────────────────────────────────────────────────
    def run(self, host: str = "0.0.0.0", port: int = 5000) -> None:  # pragma: no cover - needs a server
        from .web import create_web
        configure_logging(self.paths.log_dir, self.settings.log_level, self.log_buffer)
        logger.info("Starting Coop Controller (%s hardware)", "MOCK" if self.hardware.is_mock else "Raspberry Pi")
        self.system.record_git_version()
        flask_app, socketio = create_web(self)
        self.start()
        logger.info("Web interface on http://%s:%s", host, port)
        try:
            socketio.run(flask_app, host=host, port=port, debug=False)
        finally:
            self.stop()


class CameraStreamer:
    """Opens the camera lazily and emits base64 JPEG frames."""

    def __init__(self, app: Application):
        self._app = app
        self._camera = None

    def step(self) -> float | None:
        try:
            if self._camera is None:
                self._camera = self._app.hardware.camera_factory(self._app.settings.camera_index)
            frame = self._camera.get_frame()
        except (CameraError, OSError) as e:
            logger.critical("Camera stopped: %s", e)
            return None
        self._app.emit("camera", base64.b64encode(frame).decode("ascii"))
        return self._app.CAMERA_INTERVAL_S
