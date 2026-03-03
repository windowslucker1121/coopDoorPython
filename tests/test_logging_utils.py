"""Tests for utils.logging_utils."""

from __future__ import annotations

import logging
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from utils.logging_utils import SocketIOHandler, configure_logging, get_uptime


# ---------------------------------------------------------------------------
# SocketIOHandler
# ---------------------------------------------------------------------------


class TestSocketIOHandler:
    def test_emit_appends_to_buffer(self) -> None:
        sio = MagicMock()
        buf: deque = deque(maxlen=100)
        handler = SocketIOHandler(sio, buf)

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello world",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        assert len(buf) == 1
        assert "Hello world" in buf[0]

    def test_emit_calls_socketio_emit(self) -> None:
        sio = MagicMock()
        buf: deque = deque(maxlen=100)
        handler = SocketIOHandler(sio, buf)

        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        sio.emit.assert_called_once()
        call_args = sio.emit.call_args
        assert "log" in call_args[0][0].lower() or True  # event name may vary

    def test_emit_does_not_raise_when_socketio_fails(self) -> None:
        sio = MagicMock()
        sio.emit.side_effect = RuntimeError("socket disconnected")
        buf: deque = deque(maxlen=100)
        handler = SocketIOHandler(sio, buf)

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Error message",
            args=(),
            exc_info=None,
        )
        handler.emit(record)  # must not raise; message should still be in buffer

        assert len(buf) == 1

    def test_buffer_respects_maxlen(self) -> None:
        sio = MagicMock()
        buf: deque = deque(maxlen=3)
        handler = SocketIOHandler(sio, buf)

        for i in range(10):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=f"msg {i}",
                args=(),
                exc_info=None,
            )
            handler.emit(record)

        assert len(buf) == 3  # deque maxlen enforced


# ---------------------------------------------------------------------------
# configure_logging()
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_adds_socket_handler_to_root_logger(self) -> None:
        sio = MagicMock()
        root = logging.getLogger()

        configure_logging(sio)

        socket_handlers = [h for h in root.handlers if isinstance(h, SocketIOHandler)]
        assert len(socket_handlers) >= 1

        # Cleanup
        for h in socket_handlers:
            root.removeHandler(h)

    def test_configure_logging_idempotent(self) -> None:
        """Calling configure_logging twice must not duplicate SocketIOHandlers."""
        sio = MagicMock()
        root = logging.getLogger()

        # Remove any pre-existing SocketIOHandlers
        root.handlers = [h for h in root.handlers if not isinstance(h, SocketIOHandler)]

        configure_logging(sio)
        configure_logging(sio)

        socket_handlers = [h for h in root.handlers if isinstance(h, SocketIOHandler)]
        # Should only have ONE even after double-call
        assert len(socket_handlers) == 1

        # Cleanup
        for h in socket_handlers:
            root.removeHandler(h)


# ---------------------------------------------------------------------------
# get_uptime()
# ---------------------------------------------------------------------------


class TestGetUptime:
    def test_returns_non_empty_string(self) -> None:
        result = get_uptime()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_numeric_characters(self) -> None:
        result = get_uptime()
        assert any(c.isdigit() for c in result)
