"""HTTP routes — Flask Blueprint.

All ``@app.route`` handlers extracted from ``app.py``.  Call
``init_http(app, config_service, astro_service)`` once at startup to register
the blueprint and wire the injected services.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, date, timedelta
from threading import Thread
from typing import Any

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from protected_dict import protected_dict as global_vars
from services.astro_service import AstroService
from services.config_service import ConfigService
from utils.logging_utils import get_uptime

logger = logging.getLogger(__name__)

http_bp = Blueprint("main", __name__, template_folder="../templates")

# Injected at init time
_astro: AstroService | None = None
_config: ConfigService | None = None
_socketio: Any = None


def init_http(
    app: Any,
    config_service: ConfigService,
    astro_service: AstroService,
    socketio_instance: Any,
) -> None:
    """Register the blueprint and inject service dependencies."""
    global _config, _astro, _socketio
    _config = config_service
    _astro = astro_service
    _socketio = socketio_instance
    app.register_blueprint(http_bp)
    app.jinja_env.filters["is_number"] = _is_number_filter


# ---------------------------------------------------------------------------
# Telemetry helper (used by routes AND data_update_task)
# ---------------------------------------------------------------------------


def get_all_data() -> dict[str, Any]:
    """Return a fully formatted telemetry dict for browser / CSV consumption."""
    assert _astro is not None, "init_http() has not been called"

    (
        temp_in, hum_in, temp_out, hum_out, state, override, cpu_temp,
        sunrise, sunset, sunrise_offset, sunset_offset,
        temp_in_min, temp_in_max, hum_in_min, hum_in_max,
        temp_out_min, temp_out_max, hum_out_min, hum_out_max,
        cpu_temp_min, cpu_temp_max, reference_door_endstops_ms,
        auto_mode, error_state, camera_enabled,
    ) = global_vars.instance().get_values(
        [
            "temp_in", "hum_in", "temp_out", "hum_out",
            "state", "override", "cpu_temp",
            "sunrise", "sunset", "sunrise_offset", "sunset_offset",
            "temp_in_min", "temp_in_max", "hum_in_min", "hum_in_max",
            "temp_out_min", "temp_out_max", "hum_out_min", "hum_out_max",
            "cpu_temp_min", "cpu_temp_max",
            "reference_door_endstops_ms", "auto_mode", "error_state", "enable_camera",
        ]
    )

    time_until_open_str: str | None = None
    time_until_close_str: str | None = None

    if auto_mode == "False":
        time_until_open_str = "disabled"
        time_until_close_str = "disabled"
    elif sunrise is not None and sunset is not None:
        current_time = _astro.get_current_time()
        time_until_open = sunrise + timedelta(minutes=sunrise_offset or 0) - current_time
        time_until_close = sunset + timedelta(minutes=sunset_offset or 0) - current_time

        time_until_open_str = (
            (datetime.min + time_until_open).strftime("%H:%M:%S")
            if time_until_open > timedelta(0)
            else "passed"
        )
        time_until_close_str = (
            (datetime.min + time_until_close).strftime("%H:%M:%S")
            if time_until_close > timedelta(0)
            else "passed"
        )

    def fmt_temp(temp: float | None, units: str = "F") -> str:
        return f"{temp:0.1f}\N{DEGREE SIGN}{units}" if temp is not None else ""

    def fmt_hum(hum: float | None) -> str:
        return f"{hum:0.1f}%" if hum is not None else ""

    return {
        "time": datetime.now().strftime("%I:%M:%S.%f %p")[:-3],
        "temp_in": fmt_temp(temp_in),
        "temp_in_min": fmt_temp(temp_in_min),
        "temp_in_max": fmt_temp(temp_in_max),
        "hum_in": fmt_hum(hum_in),
        "hum_in_min": fmt_hum(hum_in_min),
        "hum_in_max": fmt_hum(hum_in_max),
        "temp_out": fmt_temp(temp_out),
        "temp_out_min": fmt_temp(temp_out_min),
        "temp_out_max": fmt_temp(temp_out_max),
        "hum_out": fmt_hum(hum_out),
        "hum_out_min": fmt_hum(hum_out_min),
        "hum_out_max": fmt_hum(hum_out_max),
        "cpu_temp": fmt_temp(cpu_temp, "C"),
        "cpu_temp_min": fmt_temp(cpu_temp_min, "C"),
        "cpu_temp_max": fmt_temp(cpu_temp_max, "C"),
        "state": state or "",
        "override": state if (state is not None and override) else "off",
        "uptime": get_uptime(),
        "sunrise": sunrise.strftime("%I:%M:%S %p").lstrip("0") if sunrise else "",
        "sunset": sunset.strftime("%I:%M:%S %p").lstrip("0") if sunset else "",
        "tu_open": time_until_open_str or "",
        "tu_close": time_until_close_str or "",
        "reference_door_endstops_ms": (
            str(reference_door_endstops_ms)
            if reference_door_endstops_ms is not None
            else "Not set"
        ),
        "auto_mode": auto_mode,
        "errorstate": error_state,
        "camera_enabled": str(camera_enabled),
    }


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@http_bp.route("/")
def index() -> str:
    return render_template(
        "index_optimized_door.html",
        auto_mode=global_vars.instance().get_value("auto_mode"),
        sunrise_offset=global_vars.instance().get_value("sunrise_offset"),
        sunset_offset=global_vars.instance().get_value("sunset_offset"),
        location=global_vars.instance().get_value("location"),
        valid_locations=AstroService.get_valid_locations(),
        reference_door_endstops_ms=global_vars.instance().get_value(
            "reference_door_endstops_ms"
        ),
        vapid_public_key=global_vars.instance().get_value("vapid_public_key"),
        is_windows=(os.name == "nt"),
    )


@http_bp.route("/debug")
def debug_panel() -> str:
    return render_template("debug.html", is_windows=(os.name == "nt"))


@http_bp.route("/mock")
def mock_panel() -> Response | str:
    if os.name != "nt":
        return Response("Mock panel is only available on Windows.", status=403)
    return render_template("mock.html")


@http_bp.route("/manifest.json")
def serve_manifest() -> Response:
    return send_file("manifest.json", mimetype="application/manifest+json")


@http_bp.route("/sw.js")
def serve_sw() -> Response:
    return send_file("sw.js", mimetype="application/javascript")


@http_bp.route("/subscribe", methods=["POST"])
def subscribe() -> Response:
    subscription = request.json
    current: dict[str, Any] = {"subscriptions": []}

    if os.path.exists(".subscriptions.json"):
        try:
            with open(".subscriptions.json", "r", encoding="utf-8") as fh:
                current = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("subscribe: could not read subscriptions file: %s", exc)

    current["subscriptions"].append(subscription)

    try:
        with open(".subscriptions.json", "w", encoding="utf-8") as fh:
            json.dump(current, fh)
    except OSError as exc:
        logger.error("subscribe: could not write subscriptions: %s", exc)

    return jsonify({"message": "Subscription successful!"})


@http_bp.route("/update", methods=["POST"])
def update_app() -> Response:
    logger.info("Update requested — starting update script.")
    update_script_path = os.path.join(os.path.dirname(__file__), "..", "update_script.py")
    app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
    pid = str(os.getpid())

    if os.name == "nt":
        subprocess.Popen(
            [sys.executable, update_script_path, app_path, pid],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        subprocess.Popen(
            [sys.executable, update_script_path, app_path, pid],
            preexec_fn=os.setpgrp,  # type: ignore[attr-defined]
        )

    def _shutdown() -> None:
        time.sleep(1)
        os._exit(0)

    Thread(target=_shutdown).start()
    response = jsonify({"status": "updating"})
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response


# ---------------------------------------------------------------------------
# Jinja2 filter
# ---------------------------------------------------------------------------


def _is_number_filter(value: Any) -> bool:
    """Return ``True`` if *value* can be converted to a float."""
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
