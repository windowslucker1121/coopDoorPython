"""SocketIO data-broadcast background task.

Emits a ``data`` event to all connected clients once per second.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from protected_dict import protected_dict as global_vars

logger = logging.getLogger(__name__)


def data_update_task_main(socketio: Any, get_all_data_fn: Any) -> None:
    """Background loop — runs forever in a daemon thread.

    Parameters
    ----------
    socketio:
        The :class:`flask_socketio.SocketIO` instance.
    get_all_data_fn:
        Callable returning the telemetry dict (provided by the HTTP
        controller to avoid a circular import).
    """
    while True:
        try:
            to_send = get_all_data_fn()
            socketio.emit("data", to_send, namespace="/")
        except Exception as exc:  # noqa: BLE001
            logger.error("data_update_task: error building / emitting data: %s", exc)
        time.sleep(1.0)
