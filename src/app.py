# ============================================================================
# CRITICAL: `door` must be imported BEFORE gevent monkey-patches `time.sleep`.
# `door.py` captures the real OS sleep at import time via `_hw_sleep = time.sleep`
# so that RPi.GPIO interrupt callbacks (which run in native C threads) use the
# un-patched sleep.  Moving this import *after* monkey.patch_all() breaks
# endstop detection on the Pi.
# ============================================================================
from door import DOOR  # noqa: E402 -- must be first
from gevent import monkey

monkey.patch_all()

# --- Standard library ---
import logging
import os
import sys
from functools import partial
from threading import Thread

# --- Flask / SocketIO ---
from flask import Flask
from flask_socketio import SocketIO

# --- Project ---
from protected_dict import protected_dict as global_vars
from services.astro_service import AstroService
from services.config_service import ConfigService
from services.door_service import DoorService
from services.notification_service import NotificationService
from controllers.http_controller import get_all_data, init_http
from controllers.socket_controller import init_socket_handlers
from tasks.camera_task import camera_task_main
from tasks.data_log_task import data_log_task_main, get_log_file_name
from tasks.data_update_task import data_update_task_main
from tasks.door_task import door_task_main
from tasks.temperature_task import temperature_task_main
from utils.logging_utils import configure_logging, log_buffer

logger = logging.getLogger(__name__)

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CONFIG_PATH = os.path.join(ROOT_PATH, "config.yaml")
SECRETS_PATH = os.path.join(ROOT_PATH, ".secrets.yaml")
SUBSCRIPTIONS_PATH = os.path.join(ROOT_PATH, ".subscriptions.json")

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = "secret_key"
socketio = SocketIO(app, async_mode="gevent")


def _build_services() -> tuple:
    config_service = ConfigService(CONFIG_PATH, global_vars.instance())
    astro_service = AstroService(global_vars.instance())
    notification_service = NotificationService(global_vars.instance(), SUBSCRIPTIONS_PATH)
    door_hardware = DOOR()
    door_service = DoorService(door_hardware)
    return config_service, astro_service, notification_service, door_service


if __name__ == "__main__":
    # Bootstrap
    global_vars.instance().set_value("desired_door_state", "stopped")

    config_service, astro_service, notification_service, door_service = _build_services()

    config_service.load()
    configure_logging(socketio)
    logger.info("Starting Coop Controller")

    notification_service.load_keys(SECRETS_PATH)
    astro_service.reload_location()

    # Register controllers
    init_http(app, config_service, astro_service, socketio)
    init_socket_handlers(
        socketio,
        config_service,
        astro_service,
        notification_service,
        log_buffer,
        get_log_file_fn=partial(get_log_file_name, ROOT_PATH),
    )

    # Start background threads
    Thread(
        target=door_task_main,
        args=(door_service, notification_service, astro_service),
        daemon=True,
        name="door_task",
    ).start()

    Thread(
        target=temperature_task_main,
        daemon=True,
        name="temperature_task",
    ).start()

    Thread(
        target=data_update_task_main,
        args=(socketio, get_all_data),
        daemon=True,
        name="data_update_task",
    ).start()

    if global_vars.instance().get_value("csvLog"):
        Thread(
            target=data_log_task_main,
            args=(ROOT_PATH, get_all_data),
            daemon=True,
            name="data_log_task",
        ).start()

    Thread(
        target=camera_task_main,
        args=(socketio,),
        daemon=True,
        name="camera_task",
    ).start()

    # Run Flask
    host = "0.0.0.0"
    port = 5000
    logger.info("Flask app starting on %s:%s", host, port)
    socketio.run(app, debug=False, host=host, port=port)