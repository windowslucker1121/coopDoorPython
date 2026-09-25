"""Logging configuration.

* console (stdout)
* ``log/app.log`` rotated at midnight, 30 days kept (read by the log viewer)
* :class:`LogBuffer` — the last 100 formatted lines, streamed to the web UI
"""

from __future__ import annotations

import logging
import os
import sys
from collections import deque
from logging.handlers import TimedRotatingFileHandler
from typing import Callable

FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class LogBuffer(logging.Handler):
    """Keeps recent log lines and forwards each new one to a sink (Socket.IO)."""

    def __init__(self, maxlen: int = 100):
        super().__init__()
        self.lines: deque[str] = deque(maxlen=maxlen)
        self.sink: Callable[[str], None] | None = None
        self.setFormatter(logging.Formatter(FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            self.lines.append(line)
            if self.sink is not None:
                self.sink(line)
        except Exception:  # a UI problem must never break logging
            pass


def configure_logging(log_dir: str | None, level: str = "INFO", buffer: LogBuffer | None = None) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_coop", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(getattr(logging, level, logging.INFO))
    formatter = logging.Formatter(FORMAT)

    handlers: list[logging.Handler] = []
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    handlers.append(console)
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = TimedRotatingFileHandler(os.path.join(log_dir, "app.log"), when="midnight",
                                                    backupCount=30, encoding="utf-8")
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except OSError as e:
            print(f"Warning: file logging disabled ({e}). Fix with: sudo chown -R $USER {log_dir}")
    if buffer is not None:
        handlers.append(buffer)
    for handler in handlers:
        handler._coop = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    for noisy in ("geventwebsocket.handler", "urllib3", "engineio.server", "socketio.server", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
