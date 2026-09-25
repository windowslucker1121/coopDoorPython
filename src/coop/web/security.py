"""Request guards: optional HTTP Basic authentication and the captive portal."""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import TYPE_CHECKING

from flask import Response, redirect, request
from werkzeug.security import check_password_hash

if TYPE_CHECKING:  # pragma: no cover
    from ..application import Application

# Paths reachable without authentication: captive-portal probes (answered by
# phone operating systems) and the static PWA shell.
PUBLIC_PATHS = ("/generate_204", "/gen_204", "/hotspot-detect.html", "/success.html",
                "/manifest.json", "/sw.js", "/favicon.ico")


def is_authorized(app: "Application") -> bool:
    auth = app.settings.auth
    if not auth.enabled:
        return True
    creds = request.authorization
    return bool(creds and creds.username == auth.username and creds.password is not None
                and check_password_hash(auth.password_hash, creds.password))


def require_auth(app: "Application"):
    """``before_request`` hook: 401 + Basic challenge when auth is enabled."""
    if request.path.startswith("/static/") or request.path in PUBLIC_PATHS:
        return None
    if is_authorized(app):
        return None
    return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="Dinky Coop"'})


def allowed_hosts(app: "Application") -> set[str]:
    try:
        hostname = os.uname().nodename.lower()
    except AttributeError:  # Windows
        hostname = socket.gethostname().lower()
    return {*app.settings.wifi.ap_allowed_hosts, "localhost", hostname, "dinky-coop", "dinkycoop"}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def captive_portal(app: "Application"):
    """``before_request`` hook: in AP mode send every foreign host to the portal."""
    if request.path.startswith(("/static/", "/api/")):
        return None
    if not app.wifi.is_ap_mode_active():
        return None
    host_header = request.headers.get("Host", "").lower()
    portal = f"http://{app.settings.wifi.ap_ip}/"
    if not host_header:
        return redirect(portal, code=302)
    host = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else host_header.split("]")[0] + "]"
    if _is_ip(host) or host in allowed_hosts(app) or host.endswith(".local"):
        return None
    return redirect(portal, code=302)
