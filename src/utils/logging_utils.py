"""Cross-cutting utilities — logging infrastructure."""

from __future__ import annotations

import logging
import sys
from collections import deque
from datetime import datetime
from typing import Any

from protected_dict import protected_dict as global_vars

# Module-level shared log buffer consumed by the debug panel and SocketIO handler
log_buffer: deque[str] = deque(maxlen=100)


# ---------------------------------------------------------------------------
# System uptime
# ---------------------------------------------------------------------------

import psutil as _psutil

_uptime_datetime = datetime.fromtimestamp(_psutil.boot_time())


def get_uptime() -> str:
    """Return a human-readable system uptime string."""
    delta = datetime.now() - _uptime_datetime
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days} day(s), {hours} hour(s), {minutes} minute(s), {seconds} second(s)"


# ---------------------------------------------------------------------------
# SocketIO log handler
# ---------------------------------------------------------------------------


class SocketIOHandler(logging.Handler):
    """Logging handler that forwards records to the browser via SocketIO.

    Each emitted log entry is also appended to ``log_buffer`` so that newly
    connected clients can receive a backlog of recent messages.

    Parameters
    ----------
    socketio_instance:
        The :class:`flask_socketio.SocketIO` application instance.
    buffer:
        The shared :class:`collections.deque` to append entries to.
        Defaults to the module-level ``log_buffer``.
    """

    def __init__(
        self,
        socketio_instance: Any,
        buffer: deque[str] | None = None,
    ) -> None:
        super().__init__()
        self._sio = socketio_instance
        self._buffer = buffer if buffer is not None else log_buffer

    def emit(self, record: logging.LogRecord) -> None:
        log_entry = self.format(record)
        self._buffer.append(log_entry)
        try:
            self._sio.emit("log", {"message": log_entry}, namespace="/")
        except Exception:  # noqa: BLE001
            # Never let the logging handler itself raise — that would cause
            # recursive logging failures.
            pass


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------


def configure_logging(socketio_instance: Any) -> logging.Logger:
    """Configure root logger with console + SocketIO handlers.

    Also adds a file handler when ``consoleLogToFile`` is set in
    ``protected_dict``.

    Parameters
    ----------
    socketio_instance:
        The :class:`flask_socketio.SocketIO` instance used by
        :class:`SocketIOHandler`.

    Returns
    -------
    logging.Logger
        The root logger.
    """
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # SocketIO handler
    sio_handler = SocketIOHandler(socketio_instance)
    sio_handler.setFormatter(formatter)
    if not any(isinstance(h, SocketIOHandler) for h in root_logger.handlers):
        root_logger.addHandler(sio_handler)

    # Console handler
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root_logger.handlers
    ):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Optional file handler
    if global_vars.instance().get_value("consoleLogToFile"):
        if not any(isinstance(h, logging.FileHandler) for h in root_logger.handlers):
            file_handler = logging.FileHandler("log.txt", mode="w")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

    # Silence the very verbose gevent websocket access log
    logging.getLogger("geventwebsocket.handler").setLevel(logging.WARNING)

    return root_logger
