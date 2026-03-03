"""SocketIO event handlers.

All ``@socketio.on`` handlers extracted from ``app.py``.  Call
``init_socket_handlers(socketio, config_service, astro_service,
notification_service, log_buffer)`` once at startup to register them.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import deque
from datetime import datetime
from typing import Any

from protected_dict import protected_dict as global_vars
from services.astro_service import AstroService
from services.config_service import ConfigService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# Injected at init time
_sio: Any = None
_config: ConfigService | None = None
_astro: AstroService | None = None
_notification: NotificationService | None = None
_log_buffer: deque[str] = deque(maxlen=100)
_get_log_file_fn: Any = None  # callable(root_path) -> str


def init_socket_handlers(
    socketio_instance: Any,
    config_service: ConfigService,
    astro_service: AstroService,
    notification_service: NotificationService,
    log_buffer: deque[str],
    get_log_file_fn: Any,
) -> None:
    """Register all SocketIO handlers with *socketio_instance*.

    Parameters
    ----------
    socketio_instance:
        The :class:`flask_socketio.SocketIO` instance.
    config_service:
        For persisting config changes.
    astro_service:
        For reloading location data after ``update_location`` events.
    notification_service:
        Reserved for future use by socket handlers.
    log_buffer:
        The shared :class:`deque` used to backfill logs for new connections.
    get_log_file_fn:
        Callable that returns the current CSV log file path.
    """
    global _sio, _config, _astro, _notification, _log_buffer, _get_log_file_fn
    _sio = socketio_instance
    _config = config_service
    _astro = astro_service
    _notification = notification_service
    _log_buffer = log_buffer
    _get_log_file_fn = get_log_file_fn

    _register(_sio)


def _register(sio: Any) -> None:  # noqa: PLR0912, C901 — register all handlers

    # ------------------------------------------------------------------
    @sio.on("connect")
    def handle_connect() -> None:
        for entry in _log_buffer:
            sio.emit("log", {"message": entry}, namespace="/")

    # ------------------------------------------------------------------
    @sio.on("disconnect")
    def handle_disconnect() -> None:
        pass

    # ------------------------------------------------------------------
    @sio.on("open")
    def handle_open() -> None:
        logger.debug("SocketIO 'open' — disabling auto mode")
        global_vars.instance().set_value("auto_mode", "False")
        global_vars.instance().set_value("desired_door_state", "open")

    # ------------------------------------------------------------------
    @sio.on("close")
    def handle_close() -> None:
        logger.debug("SocketIO 'close' — disabling auto mode")
        global_vars.instance().set_value("auto_mode", "False")
        global_vars.instance().set_value("desired_door_state", "closed")

    # ------------------------------------------------------------------
    @sio.on("stop")
    def handle_stop() -> None:
        logger.debug("SocketIO 'stop' — disabling auto mode")
        global_vars.instance().set_value("auto_mode", "False")
        global_vars.instance().set_value("desired_door_state", "stopped")

    # ------------------------------------------------------------------
    @sio.on("toggle")
    def handle_toggle(message: dict[str, Any]) -> None:
        toggle_value: bool = message.get("toggle", False)
        new_mode = "True" if toggle_value else "False"
        global_vars.instance().set_value("auto_mode", new_mode)
        logger.info("Auto mode %s", "enabled" if toggle_value else "disabled")
        assert _config is not None
        _config.save()

    # ------------------------------------------------------------------
    @sio.on("auto_offsets")
    def handle_auto_offsets(data: dict[str, Any]) -> None:
        global_vars.instance().set_values(
            {
                "sunrise_offset": int(data["sunrise_offset"]),
                "sunset_offset": int(data["sunset_offset"]),
            }
        )
        assert _config is not None
        _config.save()

    # ------------------------------------------------------------------
    @sio.on("update_location")
    def handle_update_location(location_data: dict[str, Any]) -> None:
        new_location = {
            "city": location_data.get("city"),
            "region": location_data.get("region"),
            "timezone": location_data.get("timezone"),
            "latitude": location_data.get("latitude"),
            "longitude": location_data.get("longitude"),
        }
        global_vars.instance().set_value("location", new_location)
        assert _config is not None
        _config.save()
        assert _astro is not None
        _astro.reload_location()
        logger.info("Location updated to: %s", new_location)

    # ------------------------------------------------------------------
    @sio.on("reference_endstops")
    def handle_reference_endstops() -> None:
        logger.debug("SocketIO 'reference_endstops'")
        global_vars.instance().set_value("toggle_reference_of_endstops", True)

    # ------------------------------------------------------------------
    @sio.on("clear_error")
    def handle_clear_error() -> None:
        logger.info("SocketIO 'clear_error'")
        global_vars.instance().set_value("clear_error_state", True)

    # ------------------------------------------------------------------
    @sio.on("generate_error")
    def handle_generate_error() -> None:
        logger.debug("SocketIO 'generate_error' (debug)")
        global_vars.instance().set_value("debug_error", True)

    # ------------------------------------------------------------------
    @sio.on("get_csv_data")
    def handle_get_csv_data() -> None:
        assert _get_log_file_fn is not None
        log_file = _get_log_file_fn()
        csv_data: list[str] = []
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as fh:
                csv_data = fh.readlines()
        sio.emit("csv_data", csv_data, namespace="/")

    # ------------------------------------------------------------------
    @sio.on("mock_trigger_pin")
    def handle_mock_trigger_pin(data: dict[str, Any]) -> None:
        if os.name != "nt":
            return
        from mock_gpio import GPIO  # type: ignore[import]

        pin: int = data["pin"]
        state = GPIO.HIGH if data["state"] == "HIGH" else GPIO.LOW
        GPIO.trigger_event(pin, state)

    # ------------------------------------------------------------------
    @sio.on("mock_get_outputs")
    def handle_mock_get_outputs() -> None:
        if os.name != "nt":
            return
        from mock_gpio import GPIO  # type: ignore[import]

        pins = GPIO.get_all_pins()
        outputs = {
            17: pins.get(17, {}).get("state", "LOW"),
            27: pins.get(27, {}).get("state", "LOW"),
            22: pins.get(22, {}).get("state", "LOW"),
        }
        sio.emit("mock_update_outputs", outputs, namespace="/")

    # ------------------------------------------------------------------
    @sio.on("get_debug_data")
    def handle_get_debug_data() -> None:
        import door as door_module  # type: ignore[import]

        pin_meta = {
            17: {"name": "in1",          "purpose": "Motor UP",               "direction": "OUT"},
            27: {"name": "in2",          "purpose": "Motor DOWN",             "direction": "OUT"},
            22: {"name": "ena",          "purpose": "Motor Enable",           "direction": "OUT"},
            23: {"name": "end_up",       "purpose": "Endstop UP",             "direction": "IN"},
            24: {"name": "end_down",     "purpose": "Rope-slack sensor DOWN", "direction": "IN"},
            5:  {"name": "o_pin",        "purpose": "Manual Open Switch",     "direction": "IN"},
            6:  {"name": "c_pin",        "purpose": "Manual Close Switch",    "direction": "IN"},
            21: {"name": "data_pin_out", "purpose": "DHT22 Outdoor Data",     "direction": "IN"},
            16: {"name": "data_pin_in",  "purpose": "DHT22 Indoor Data",      "direction": "IN"},
            20: {"name": "power_pin_out","purpose": "DHT22 Outdoor Power",    "direction": "OUT"},
            26: {"name": "power_pin_in", "purpose": "DHT22 Indoor Power",     "direction": "OUT"},
        }

        pins_data: list[dict[str, Any]] = []
        if os.name == "nt":
            from mock_gpio import GPIO as _MockGPIO  # type: ignore[import]

            all_pins = _MockGPIO.get_all_pins()
            for pin_num, meta in sorted(pin_meta.items()):
                pin_info = all_pins.get(pin_num, {})
                pins_data.append(
                    {
                        "pin": pin_num,
                        "name": meta["name"],
                        "purpose": meta["purpose"],
                        "direction": meta["direction"],
                        "state": pin_info.get("state", "N/A"),
                        "mode": pin_info.get("mode", "N/A"),
                    }
                )
        else:
            import RPi.GPIO as RealGPIO  # type: ignore[import]

            for pin_num, meta in sorted(pin_meta.items()):
                try:
                    state_str = "HIGH" if RealGPIO.input(pin_num) else "LOW"
                except Exception:  # noqa: BLE001
                    state_str = "N/A"
                pins_data.append(
                    {
                        "pin": pin_num,
                        "name": meta["name"],
                        "purpose": meta["purpose"],
                        "direction": meta["direction"],
                        "state": state_str,
                        "mode": meta["direction"],
                    }
                )

        door_constants = {
            "referenceSequenceTimeout": door_module.referenceSequenceTimeout,
            "invert_end_up": door_module.invert_end_up,
            "invert_end_down": door_module.invert_end_down,
        }

        secrets_keys = {"vapid_private_key", "vapid_public_key"}
        all_globals: dict[str, Any] = {}
        for k, v in global_vars.instance().get_all().items():
            if k in secrets_keys:
                all_globals[k] = "***" if v else None
            elif isinstance(v, datetime):
                all_globals[k] = v.strftime("%Y-%m-%d %H:%M:%S %Z")
            else:
                all_globals[k] = v

        import psutil

        cpu_percent = psutil.cpu_percent(interval=0)
        mem = psutil.virtual_memory()
        system_info = {
            "os_name": os.name,
            "platform": sys.platform,
            "python_version": sys.version,
            "uptime": _get_uptime_str(),
            "cpu_percent": cpu_percent,
            "memory_total_mb": round(mem.total / (1024 * 1024), 1),
            "memory_used_mb": round(mem.used / (1024 * 1024), 1),
            "memory_percent": mem.percent,
        }

        threads_data = [
            {"name": t.name, "daemon": t.daemon, "alive": t.is_alive()}
            for t in threading.enumerate()
        ]

        debug_payload = {
            "pins": pins_data,
            "door_constants": door_constants,
            "global_vars": all_globals,
            "system": system_info,
            "threads": threads_data,
            "logs": list(_log_buffer),
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        }
        sio.emit("debug_data", debug_payload, namespace="/")


def _get_uptime_str() -> str:
    from utils.logging_utils import get_uptime

    return get_uptime()
