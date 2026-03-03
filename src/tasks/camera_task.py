"""Camera background task.

Captures MJPEG frames via OpenCV and emits base64-encoded JPEG strings
as ``camera`` SocketIO events at approximately 10 fps.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from protected_dict import protected_dict as global_vars

logger = logging.getLogger(__name__)


def camera_task_main(socketio: Any) -> None:
    """Background loop — runs forever in a daemon thread (or exits early if
    the camera is disabled or encounters a fatal error).

    Parameters
    ----------
    socketio:
        The :class:`flask_socketio.SocketIO` instance.
    """
    if not global_vars.instance().get_value("enable_camera"):
        logger.info("camera_task: camera disabled by configuration — task exiting.")
        return

    from camera import Camera  # local import avoids opencv import at module level

    camera_index: int = global_vars.instance().get_value("camera_index") or 0
    logger.debug("camera_task: starting with device index %d", camera_index)
    cam = Camera(device_index=camera_index)

    while True:
        try:
            frame = cam.get_frame()
            encoded = base64.b64encode(frame).decode("utf-8")
            socketio.emit("camera", encoded, namespace="/")
        except RuntimeError as exc:
            logger.critical("camera_task: %s — task exiting.", exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("camera_task: unexpected error — %s", exc)

        time.sleep(0.1)
