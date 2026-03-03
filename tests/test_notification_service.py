"""Tests for services.notification_service."""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from services.notification_service import NotificationService


# ---------------------------------------------------------------------------
# Real WebPushException substitute (the pywebpush stub in conftest is a
# MagicMock and its attributes don't inherit from BaseException)
# ---------------------------------------------------------------------------


class _FakeWebPushException(Exception):
    """Minimal stand-in for pywebpush.WebPushException used in tests."""

    def __init__(self, msg: str = "", response=None) -> None:
        super().__init__(msg)
        self.response = response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_svc(gv, subscriptions_path: str) -> NotificationService:
    return NotificationService(gv, subscriptions_path)


# ---------------------------------------------------------------------------
# load_keys()
# ---------------------------------------------------------------------------


class TestLoadKeys:
    def test_loads_vapid_keys_from_secrets(self, gv, tmp_secrets) -> None:
        svc = _make_svc(gv, "/dev/null")
        svc.load_keys(tmp_secrets)
        assert gv.get_value("vapid_public_key") == "pub123"
        assert gv.get_value("vapid_private_key") == "priv456"

    def test_missing_secrets_file_logs_critical(self, gv, tmp_path, caplog) -> None:
        import logging

        missing = str(tmp_path / "nonexistent.yaml")
        svc = _make_svc(gv, "/dev/null")
        with caplog.at_level(logging.CRITICAL, logger="services.notification_service"):
            svc.load_keys(missing)
        assert "not found" in caplog.text.lower() or "disabled" in caplog.text.lower()
        # No key written to gv
        assert gv.get_value("vapid_private_key") is None

    def test_missing_secrets_file_does_not_raise(self, gv, tmp_path) -> None:
        missing = str(tmp_path / "nonexistent.yaml")
        svc = _make_svc(gv, "/dev/null")
        svc.load_keys(missing)  # must not raise


# ---------------------------------------------------------------------------
# register_subscription()
# ---------------------------------------------------------------------------


class TestRegisterSubscription:
    def test_creates_file_with_subscription(self, gv, tmp_subscriptions) -> None:
        svc = _make_svc(gv, tmp_subscriptions)
        sub = {"endpoint": "https://example.com/push/1", "keys": {"auth": "a", "p256dh": "b"}}
        svc.register_subscription(sub)

        with open(tmp_subscriptions, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert len(data["subscriptions"]) == 1
        assert data["subscriptions"][0]["endpoint"] == "https://example.com/push/1"

    def test_appends_to_existing_subscriptions(self, gv, tmp_subscriptions) -> None:
        existing = {"subscriptions": [{"endpoint": "https://example.com/push/0"}]}
        with open(tmp_subscriptions, "w", encoding="utf-8") as fh:
            json.dump(existing, fh)

        svc = _make_svc(gv, tmp_subscriptions)
        svc.register_subscription({"endpoint": "https://example.com/push/1"})

        with open(tmp_subscriptions, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert len(data["subscriptions"]) == 2


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


class TestSend:
    def test_skips_when_private_key_missing(self, gv, tmp_subscriptions, caplog) -> None:
        import logging

        svc = _make_svc(gv, tmp_subscriptions)
        with caplog.at_level(logging.CRITICAL, logger="services.notification_service"):
            svc.send("Title", "Body")  # vapid_private_key not set → critical log
        assert "vapid_private_key" in caplog.text.lower() or "skipping" in caplog.text.lower()

    def test_skips_when_subscriptions_file_missing(self, gv, tmp_subscriptions, caplog) -> None:
        import logging

        gv.set_value("vapid_private_key", "priv456")
        svc = _make_svc(gv, tmp_subscriptions)  # file doesn't exist yet
        with caplog.at_level(logging.WARNING, logger="services.notification_service"):
            svc.send("Title", "Body")
        # Should not raise

    def test_send_calls_webpush_for_each_subscription(self, gv, tmp_subscriptions) -> None:
        gv.set_value("vapid_private_key", "priv456")
        subs = {
            "subscriptions": [
                {"endpoint": "https://example.com/push/1"},
                {"endpoint": "https://example.com/push/2"},
            ]
        }
        with open(tmp_subscriptions, "w", encoding="utf-8") as fh:
            json.dump(subs, fh)

        svc = _make_svc(gv, tmp_subscriptions)

        with patch("services.notification_service.webpush") as mock_wp:
            svc.send("Test", "Hello")
        assert mock_wp.call_count == 2

    def test_expired_subscriptions_removed_via_finally(self, gv, tmp_subscriptions) -> None:
        """HTTP 410 subscriptions must be purged; file always rewritten (finally fix)."""
        gv.set_value("vapid_private_key", "priv456")
        subs = {
            "subscriptions": [
                {"endpoint": "https://example.com/push/expired"},
                {"endpoint": "https://example.com/push/valid"},
            ]
        }
        with open(tmp_subscriptions, "w", encoding="utf-8") as fh:
            json.dump(subs, fh)

        svc = _make_svc(gv, tmp_subscriptions)

        resp_mock = MagicMock()
        resp_mock.status_code = 410
        expired_exc = _FakeWebPushException("Gone", response=resp_mock)

        def _side_effect(*args, **kwargs):
            endpoint = args[0].get("endpoint", "")
            if "expired" in endpoint:
                raise expired_exc

        with patch("services.notification_service.webpush", side_effect=_side_effect):
            with patch("services.notification_service.WebPushException", _FakeWebPushException):
                svc.send("Title", "Body")

        with open(tmp_subscriptions, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        endpoints = [s["endpoint"] for s in data["subscriptions"]]
        assert "https://example.com/push/expired" not in endpoints
        assert "https://example.com/push/valid" in endpoints

    def test_subscriptions_file_always_rewritten_on_read_error(
        self, gv, tmp_subscriptions
    ) -> None:
        """Even when reading subscriptions raises, finally must rewrite the file."""
        gv.set_value("vapid_private_key", "priv456")
        # Write invalid JSON to trigger JSONDecodeError during open
        with open(tmp_subscriptions, "w", encoding="utf-8") as fh:
            fh.write("NOT JSON !!!")

        svc = _make_svc(gv, tmp_subscriptions)
        svc.send("Title", "Body")  # must not raise

        # File should now contain valid JSON {"subscriptions": []}
        with open(tmp_subscriptions, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert data == {"subscriptions": []}


# ---------------------------------------------------------------------------
# _send_one()
# ---------------------------------------------------------------------------


class TestSendOne:
    def test_returns_true_on_success(self) -> None:
        with patch("services.notification_service.webpush"):
            result = NotificationService._send_one(
                {"endpoint": "https://example.com/push/1"},
                {"title": "T", "body": "B"},
                "priv",
                {"sub": "mailto:test@test.com"},
            )
        assert result is True

    def test_returns_false_on_410(self) -> None:
        resp = MagicMock()
        resp.status_code = 410
        exc = _FakeWebPushException("Gone", response=resp)

        with patch("services.notification_service.webpush", side_effect=exc):
            with patch("services.notification_service.WebPushException", _FakeWebPushException):
                result = NotificationService._send_one(
                    {"endpoint": "https://example.com/push/expired"},
                    {"title": "T", "body": "B"},
                    "priv",
                    {"sub": "mailto:test@test.com"},
                )
        assert result is False

    def test_returns_true_on_other_webpush_exception(self) -> None:
        exc = _FakeWebPushException("Server Error", response=None)

        with patch("services.notification_service.webpush", side_effect=exc):
            with patch("services.notification_service.WebPushException", _FakeWebPushException):
                result = NotificationService._send_one(
                    {"endpoint": "https://example.com/push/1"},
                    {"title": "T", "body": "B"},
                    "priv",
                    {"sub": "mailto:test@test.com"},
                )
        assert result is True
