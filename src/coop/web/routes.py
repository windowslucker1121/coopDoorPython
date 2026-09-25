"""HTTP routes (pages, JSON API)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory

from ..config import ConfigError
from ..services import datalog
from ..services.sun import list_locations
from .payloads import MASK, dashboard_payload

if TYPE_CHECKING:  # pragma: no cover
    from ..application import Application

logger = logging.getLogger(__name__)


def _json_body() -> dict | None:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def register_routes(flask_app: Flask, app: "Application") -> None:
    paths = app.paths

    # ── pages ────────────────────────────────────────────────────────
    @flask_app.route("/")
    def index():
        s = app.settings
        return render_template(
            "grid_dashboard.html",
            auto_mode="True" if s.auto_mode else "False",
            sunrise_offset=s.sunrise_offset,
            sunset_offset=s.sunset_offset,
            timer_open_time=s.timer_open_time,
            timer_close_time=s.timer_close_time,
            location=s.location.to_dict(),
            valid_locations=list_locations(),
            reference_door_endstops_ms=s.reference_travel_ms,
            vapid_public_key=app.vapid_public_key or "",
            is_windows=app.hardware.is_mock,
        )

    @flask_app.route("/debug")
    def debug_panel():
        return render_template("debug.html", is_windows=app.hardware.is_mock)

    @flask_app.route("/mock")
    def mock_panel():
        if not app.hardware.is_mock:
            return "Mock panel is only available with mock hardware.", 403
        return render_template("mock.html")

    @flask_app.route("/favicon.ico")
    def favicon():
        return send_from_directory(os.path.join(paths.src, "static"), "favicon_32.png", mimetype="image/png")

    @flask_app.route("/manifest.json")
    def manifest():
        return send_from_directory(paths.src, "manifest.json", mimetype="application/manifest+json")

    @flask_app.route("/sw.js")
    def service_worker():
        return send_from_directory(paths.src, "sw.js", mimetype="application/javascript")

    @flask_app.route("/version")
    def version():
        return jsonify({"version": app.system.version()})

    # ── status ───────────────────────────────────────────────────────
    @flask_app.route("/api/status")
    def api_status():
        return jsonify(dashboard_payload(app))

    @flask_app.route("/api/health")
    def api_health():
        workers = {w.name: w.alive for w in app.workers}
        healthy = all(workers.values())
        return jsonify({"healthy": healthy, "workers": workers,
                        "door_error": app.controller.status.error}), 200 if healthy else 503

    # ── push ─────────────────────────────────────────────────────────
    @flask_app.route("/subscribe", methods=["POST"])
    def subscribe():
        sub = _json_body()
        if not sub or not sub.get("endpoint"):
            return _error("Subscription with an endpoint required")
        app.subscriptions.add(sub)
        logger.info("Push subscription stored")
        return jsonify({"message": "Subscription successful!"})

    # ── log & data viewers ───────────────────────────────────────────
    @flask_app.route("/api/logs")
    def api_logs():
        return jsonify(datalog.list_app_logs(paths.log_dir))

    @flask_app.route("/api/logs/<path:filename>")
    def api_log(filename):
        name = os.path.basename(filename)
        if not datalog.is_app_log_name(name):
            return _error("Invalid file type")
        path = os.path.join(paths.log_dir, name)
        if not os.path.isfile(path):
            return _error("File not found", 404)
        try:
            return jsonify(datalog.read_app_log(path))
        except OSError as e:
            return _error(str(e), 500)

    @flask_app.route("/api/csv")
    def api_csv_files():
        return jsonify(datalog.list_csv_files(paths.log_dir))

    @flask_app.route("/api/csv/<path:filename>")
    def api_csv(filename):
        name = os.path.basename(filename)
        if not datalog.is_csv_name(name):
            return _error("Invalid file type")
        path = os.path.join(paths.log_dir, name)
        if not os.path.isfile(path):
            return _error("File not found", 404)
        try:
            return jsonify(datalog.read_csv(path))
        except OSError as e:
            logger.error("Error reading CSV file %s: %s", name, e)
            return _error(str(e), 500)

    # ── GPIO configuration ───────────────────────────────────────────
    @flask_app.route("/api/gpio-config", methods=["GET"])
    def get_gpio():
        return jsonify(app.settings.gpio.to_dict())

    @flask_app.route("/api/gpio-config", methods=["POST"])
    def set_gpio():
        data = _json_body()
        if data is None:
            return _error("JSON body required")
        try:
            gpio = app.settings.gpio.merged(data)
            app.config.update(gpio=gpio)
        except ConfigError as e:
            return _error(str(e))
        return jsonify({"message": "GPIO config saved. Restart required for pin changes to take effect.",
                        "gpio": gpio.to_dict()})

    # ── Wi-Fi ────────────────────────────────────────────────────────
    @flask_app.route("/api/wifi-status")
    def wifi_status():
        return jsonify({
            "ethernet_connected": app.wifi.is_ethernet_connected(),
            "ap_mode_active": app.wifi.is_ap_mode_active(),
            "current_connection": app.wifi.get_current_connection(),
        })

    @flask_app.route("/api/wifi-scan")
    def wifi_scan():
        return jsonify(app.wifi.scan_networks())

    @flask_app.route("/api/wifi-ap", methods=["POST"])
    def wifi_ap():
        w = app.settings.wifi
        return jsonify({"success": app.wifi.start_ap(w.ap_ssid, w.ap_password, w.ap_ip)})

    @flask_app.route("/api/wifi-config", methods=["GET"])
    def get_wifi():
        d = app.settings.wifi.to_dict()
        # Never send stored passwords back; the mask is ignored on save.
        d["password"] = MASK if d["password"] else ""
        d["ap_password"] = MASK if d["ap_password"] else ""
        return jsonify(d)

    @flask_app.route("/api/wifi-config", methods=["POST"])
    def set_wifi():
        data = _json_body()
        if data is None:
            return _error("JSON body required")
        data = {k: v for k, v in data.items() if not (k in ("password", "ap_password") and v == MASK)}
        data.pop("ap_ip", None)  # not editable through the UI
        try:
            wifi = app.settings.wifi.merged(data)
            app.config.update(wifi=wifi)
        except ConfigError as e:
            return _error(str(e))
        shown = dict(wifi.to_dict(), password=MASK if wifi.password else "",
                     ap_password=MASK if wifi.ap_password else "")
        return jsonify({"message": "WiFi config saved. Will attempt connection on next periodic check or reboot.",
                        "wifi": shown})

    @flask_app.route("/api/wifi-connect", methods=["POST"])
    def wifi_connect():
        data = _json_body()
        if data is None:
            return _error("JSON body required")
        ssid = data.get("ssid")
        if not ssid:
            return _error("SSID is required")
        success = app.wifi.connect(str(ssid), data.get("password") or None)
        if not success:
            logger.warning("Failed to connect to %s - starting AP mode in %.0f s to prevent lock-out.",
                           ssid, app.WIFI_FALLBACK_DELAY_S)
            app.clock.sleep(app.WIFI_FALLBACK_DELAY_S)
            w = app.settings.wifi
            app.wifi.start_ap(w.ap_ssid, w.ap_password, w.ap_ip)
        return jsonify({"success": success})

    # ── system ───────────────────────────────────────────────────────
    @flask_app.route("/api/system/time", methods=["POST"])
    def set_time():
        data = _json_body()
        if not data or "time" not in data:
            return _error("Time is required")
        try:
            app.system.set_time(str(data["time"]))
        except ValueError:
            return _error("Invalid date format. Required: YYYY-MM-DD HH:MM:SS")
        except Exception as e:
            logger.error("Setting system time failed: %s", e)
            return _error(f"Failed to set system time: {e}", 500)
        return jsonify({"message": "System time successfully updated."})

    @flask_app.route("/api/restart", methods=["POST"])
    def restart():
        try:
            app.system.reboot()
        except RuntimeError as e:
            return _error(str(e))
        return jsonify({"status": "rebooting device"})

    @flask_app.route("/update", methods=["POST"])
    def update():
        app.system.start_update()
        return jsonify({"status": "updating"})

    # ── captive-portal probes ────────────────────────────────────────
    @flask_app.route("/generate_204")
    @flask_app.route("/gen_204")
    def android_probe():
        return redirect(f"http://{app.settings.wifi.ap_ip}/", code=302)

    @flask_app.route("/hotspot-detect.html")
    @flask_app.route("/success.html")
    def apple_probe():
        target = f"http://{app.settings.wifi.ap_ip}/"
        return (f'<html><head><meta http-equiv="refresh" content="0;url={target}"></head>'
                f'<body><p>Redirecting to portal... If nothing happens, '
                f'<a href="{target}">click here</a>.</p></body></html>'), 200

    @flask_app.template_filter("is_number")
    def is_number(value):
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False

    @flask_app.after_request
    def no_cache_for_api(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
