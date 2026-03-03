"""Push-notification service — VAPID Web Push delivery.

Extracted from ``app.py``'s ``send_push_notification()``,
``send_individual_push_notification()``, and ``load_notification_keys()``
functions.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import ruamel.yaml as YAML
from pywebpush import WebPushException, webpush

from protected_dict import protected_dict

logger = logging.getLogger(__name__)

_VAPID_CONTACT = "mailto:your-email@example.com"


class NotificationService:
    """Sends VAPID-signed Web Push notifications to all registered subscribers.

    Parameters
    ----------
    global_vars_instance:
        The singleton :class:`protected_dict`.  Used to read
        ``vapid_private_key``.
    subscriptions_path:
        Absolute path to ``.subscriptions.json``.
    """

    def __init__(
        self,
        global_vars_instance: protected_dict,
        subscriptions_path: str,
    ) -> None:
        self._gv = global_vars_instance
        self._subscriptions_path = subscriptions_path
        self._vapid_private_key: str | None = None

    # ------------------------------------------------------------------

    def load_keys(self, secrets_path: str) -> None:
        """Load VAPID keys from ``secrets_path`` into ``protected_dict``.

        Logs a ``CRITICAL`` message if the file is absent; the application
        continues running but push notifications will be silently skipped.
        """
        if not os.path.exists(secrets_path):
            logger.critical(
                "NotificationService.load_keys: secrets file '%s' not found — "
                "push notifications will be disabled.",
                secrets_path,
            )
            return

        try:
            with open(secrets_path, "r", encoding="utf-8") as fh:
                yaml = YAML.YAML()
                content = yaml.load(fh.read())
                if content and "secrets" in content:
                    self._gv.set_values(content["secrets"])
                    logger.info("Notification keys loaded from %s", secrets_path)
                else:
                    logger.error(
                        "NotificationService.load_keys: unexpected structure in '%s'",
                        secrets_path,
                    )
        except OSError as exc:
            logger.error("NotificationService.load_keys: %s", exc)

    # ------------------------------------------------------------------

    def register_subscription(self, subscription: dict[str, Any]) -> None:
        """Persist a new Web Push subscription to the subscriptions file."""
        current: dict[str, Any] = {"subscriptions": []}

        if os.path.exists(self._subscriptions_path):
            try:
                with open(self._subscriptions_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                    if isinstance(loaded, dict):
                        current = loaded
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("register_subscription: could not read subscriptions: %s", exc)

        current["subscriptions"].append(subscription)

        try:
            with open(self._subscriptions_path, "w", encoding="utf-8") as fh:
                json.dump(current, fh)
        except OSError as exc:
            logger.error("register_subscription: could not write subscriptions: %s", exc)

    # ------------------------------------------------------------------

    def send(self, title: str, body: str) -> None:
        """Broadcast a push notification to all registered subscribers.

        Expired / invalid subscriptions (HTTP 410) are automatically removed.
        The subscriptions file is always rewritten in the ``finally`` block —
        fixing the best-effort cleanup of the original implementation.
        """
        # Lazily resolve VAPID private key
        if self._vapid_private_key is None:
            self._vapid_private_key = self._gv.get_value("vapid_private_key")

        if not self._vapid_private_key:
            logger.critical(
                "NotificationService.send: vapid_private_key not set — skipping."
            )
            return

        if not os.path.exists(self._subscriptions_path):
            logger.warning(
                "NotificationService.send: subscriptions file '%s' not found — skipping.",
                self._subscriptions_path,
            )
            return

        json_content: dict[str, Any] = {"subscriptions": []}
        to_remove: list[dict[str, Any]] = []

        try:
            with open(self._subscriptions_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, dict):
                    json_content = loaded

            payload = {"title": title, "body": body}
            vapid_claims = {"sub": _VAPID_CONTACT}

            for subscription in json_content.get("subscriptions", []):
                valid = self._send_one(
                    subscription,
                    payload,
                    self._vapid_private_key,
                    vapid_claims,
                )
                if not valid:
                    to_remove.append(subscription)

            for stale in to_remove:
                json_content["subscriptions"].remove(stale)

        except (OSError, json.JSONDecodeError) as exc:
            logger.error("NotificationService.send: %s", exc)
        finally:
            # Always rewrite the file so stale subscriptions are purged
            try:
                with open(self._subscriptions_path, "w", encoding="utf-8") as fh:
                    json.dump(json_content, fh)
            except OSError as exc:
                logger.error(
                    "NotificationService.send: failed to rewrite subscriptions: %s", exc
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _send_one(
        subscription_info: dict[str, Any],
        payload: dict[str, str],
        vapid_private_key: str,
        vapid_claims: dict[str, str],
    ) -> bool:
        """Send a push notification to a single subscriber.

        Returns
        -------
        bool
            ``False`` if the subscription is no longer valid (HTTP 410) and
            should be removed, ``True`` otherwise.
        """
        try:
            webpush(
                subscription_info,
                data=json.dumps(payload),
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims,
                timeout=10,
            )
            return True
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 410:
                logger.debug("_send_one: subscription expired (410) — marking for removal.")
                return False
            logger.debug("_send_one: WebPushException — %s", exc)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("_send_one: unexpected error — %s", exc)
            return True
