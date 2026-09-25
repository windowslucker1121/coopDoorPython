"""Flask + Socket.IO web interface."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from flask import Flask
from flask_socketio import SocketIO

from .routes import register_routes
from .security import captive_portal, require_auth
from .sockets import register_socket_handlers

if TYPE_CHECKING:  # pragma: no cover
    from ..application import Application


def create_web(app: "Application", async_mode: str | None = "gevent") -> tuple[Flask, SocketIO]:
    """Create the Flask app and Socket.IO server bound to *app*.

    ``async_mode=None`` lets Flask-SocketIO pick (used by the tests, which
    run without gevent monkey-patching)."""
    flask_app = Flask(__name__, root_path=app.paths.src,
                      template_folder=os.path.join(app.paths.src, "templates"),
                      static_folder=os.path.join(app.paths.src, "static"))
    flask_app.config["SECRET_KEY"] = os.urandom(32)
    flask_app.config["JSON_SORT_KEYS"] = False
    socketio = SocketIO(flask_app, async_mode=async_mode)

    flask_app.before_request(lambda: require_auth(app))
    flask_app.before_request(lambda: captive_portal(app))
    register_routes(flask_app, app)
    register_socket_handlers(socketio, app)

    def emit(event: str, payload) -> None:
        socketio.emit(event, payload, namespace="/")

    app.emit = emit
    app.log_buffer.sink = lambda line: socketio.emit("log", {"message": line}, namespace="/")
    flask_app.extensions["coop_app"] = app
    return flask_app, socketio
