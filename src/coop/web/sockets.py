"""Socket.IO event handlers."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from flask_socketio import SocketIO, emit

from ..config import ConfigError, LocationConfig, Mode, parse_hhmm, parse_int, MAX_SUN_OFFSET_MIN
from ..door.model import DesiredState
from ..services.datalog import csv_file_name
from .payloads import debug_payload
from .security import is_authorized

if TYPE_CHECKING:  # pragma: no cover
    from ..application import Application

logger = logging.getLogger(__name__)

OK = {"ok": True}


def _fail(message: str) -> dict:
    return {"ok": False, "error": message}


def register_socket_handlers(socketio: SocketIO, app: "Application") -> None:
    on = socketio.on

    @on("connect")
    def connect(auth=None):
        if not is_authorized(app):
            return False
        for line in list(app.log_buffer.lines):
            emit("log", {"message": line})  # to this client only
        return None

    # ── door commands ────────────────────────────────────────────────
    @on("open")
    def open_door():
        app.manual_command(DesiredState.OPEN)
        return OK

    @on("close")
    def close_door():
        app.manual_command(DesiredState.CLOSED)
        return OK

    @on("stop")
    def stop_door():
        app.manual_command(DesiredState.STOPPED)
        return OK

    @on("reference_endstops")
    def reference():
        logger.info("Reference sequence requested")
        app.controller.request_reference()
        return OK

    @on("clear_error")
    def clear_error():
        logger.info("Clearing error state")
        app.controller.clear_error()
        return OK

    @on("generate_error")
    def generate_error():
        logger.info("Test error requested")
        app.controller.inject_test_error()
        return OK

    # ── mode & schedule settings ─────────────────────────────────────
    def _toggle(mode: Mode, message) -> dict:
        if not isinstance(message, dict) or "toggle" not in message:
            logger.warning("Ignoring invalid toggle payload: %r", message)
            return _fail("Invalid request")
        try:
            app.set_mode(mode, bool(message["toggle"]))
        except (ValueError, ConfigError) as e:
            return _fail(str(e))
        return OK

    @on("toggle")
    def toggle_auto(message=None):
        return _toggle(Mode.AUTO, message)

    @on("toggle_timer")
    def toggle_timer(message=None):
        return _toggle(Mode.TIMER, message)

    @on("set_mode")
    def set_mode(message=None):
        """v2 UI: {"mode": "manual" | "auto" | "timer"}."""
        try:
            mode = Mode(str((message or {}).get("mode")))
        except (ValueError, AttributeError):
            return _fail("Unknown mode")
        if mode is Mode.MANUAL:
            current = app.settings.mode
            if current is not Mode.MANUAL:
                return _toggle(current, {"toggle": False})
            return OK
        return _toggle(mode, {"toggle": True})

    def _update(description: str, data, **builders) -> dict:
        try:
            changes = {key: build() for key, build in builders.items()}
            app.config.update(**changes)
        except (ConfigError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Ignoring invalid %s %r: %s", description, data, e)
            return _fail(str(e) if isinstance(e, ConfigError) else f"Invalid {description}")
        return OK

    @on("timer_times")
    def timer_times(data=None):
        data = data if isinstance(data, dict) else {}
        return _update("timer times", data,
                timer_open_time=lambda: parse_hhmm(data.get("timer_open_time", data.get("open_time")), "open time"),
                timer_close_time=lambda: parse_hhmm(data.get("timer_close_time", data.get("close_time")), "close time"))

    @on("auto_offsets")
    def auto_offsets(data=None):
        data = data if isinstance(data, dict) else {}
        return _update("sunrise/sunset offsets", data,
                sunrise_offset=lambda: parse_int(data["sunrise_offset"], "sunrise_offset",
                                                 -MAX_SUN_OFFSET_MIN, MAX_SUN_OFFSET_MIN),
                sunset_offset=lambda: parse_int(data["sunset_offset"], "sunset_offset",
                                                -MAX_SUN_OFFSET_MIN, MAX_SUN_OFFSET_MIN))

    @on("update_location")
    def update_location(data=None):
        return _update("location", data, location=lambda: LocationConfig.from_dict(data))

    # ── data requests (answered to the requesting client) ────────────
    @on("get_csv_data")
    def get_csv_data():
        path = csv_file_name(app.paths.log_dir, app.clock.now())
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                emit("csv_data", f.readlines())

    @on("get_debug_data")
    def get_debug_data():
        emit("debug_data", debug_payload(app))

    # ── mock hardware panel ──────────────────────────────────────────
    @on("mock_trigger_pin")
    def mock_trigger_pin(data=None):
        gpio = app.hardware.gpio
        if not gpio.is_mock or not isinstance(data, dict):
            return
        try:
            gpio.trigger(int(data["pin"]), data.get("state") == "HIGH")
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring invalid mock pin payload %r", data)

    @on("mock_get_outputs")
    def mock_get_outputs():
        gpio = app.hardware.gpio
        if not gpio.is_mock:
            return
        p = app.driver.pins
        emit("mock_update_outputs", {pin: "HIGH" if gpio.read(pin) else "LOW"
                                     for pin in (p.motor_in1, p.motor_in2, p.motor_ena)})
