"""Web Push notifications (VAPID) and the subscription store."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Callable

import ruamel.yaml as YAML

logger = logging.getLogger(__name__)


def load_vapid_keys(path: str) -> tuple[str | None, str | None]:
    """(public, private) from ``.secrets.yaml`` (``secrets:`` section)."""
    if not os.path.exists(path):
        logger.critical("No %s found - push notifications are disabled. "
                        "Run src/generateVapidPair.py to create VAPID keys.", os.path.basename(path))
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            data = YAML.YAML(typ="safe").load(f)
        secrets = data.get("secrets") if isinstance(data, dict) else None
        if not isinstance(secrets, dict):
            raise ValueError("no 'secrets' section")
        return secrets.get("vapid_public_key"), secrets.get("vapid_private_key")
    except Exception as e:
        logger.critical("Could not read %s (%s) - push notifications are disabled.", path, e)
        return None, None


class SubscriptionStore:
    """Push subscriptions in a JSON file; one entry per endpoint.

    Tolerates a missing or corrupt file, writes atomically, and serialises
    all read-modify-write cycles with a lock.
    """

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()

    def all(self) -> list[dict]:
        with self._lock:
            return self._load()

    def add(self, subscription: dict) -> None:
        endpoint = subscription.get("endpoint")
        with self._lock:
            subs = [s for s in self._load() if s.get("endpoint") != endpoint]
            subs.append(subscription)
            self._save(subs)

    def remove(self, endpoints: set[str]) -> None:
        if not endpoints:
            return
        with self._lock:
            self._save([s for s in self._load() if s.get("endpoint") not in endpoints])

    def _load(self) -> list[dict]:
        try:
            with open(self._path, encoding="utf-8") as f:
                content = json.load(f)
            subs = content.get("subscriptions") if isinstance(content, dict) else None
            if isinstance(subs, list):
                return [s for s in subs if isinstance(s, dict)]
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logger.error("Could not read %s: %s", self._path, e)
        return []

    def _save(self, subs: list[dict]) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"subscriptions": subs}, f)
        os.replace(tmp, self._path)


class PushNotifier:
    """Sends notifications to all subscriptions.

    :meth:`notify` returns immediately; delivery (up to 10 s per push
    service) runs in a background thread so callers — above all the door
    control loop — never block.  Subscriptions answered with 404/410 are
    removed.
    """

    TIMEOUT_S = 10

    def __init__(self, store: SubscriptionStore, private_key: str | None, *,
                 contact: str = "mailto:admin@localhost", webpush=None,
                 run_async: bool = True, spawn: Callable[[Callable[[], None]], None] | None = None):
        self._store = store
        self._private_key = private_key
        self._contact = contact
        self._webpush = webpush
        self._run_async = run_async
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, name="push", daemon=True).start())

    @property
    def enabled(self) -> bool:
        return bool(self._private_key)

    def notify(self, title: str, body: str) -> None:
        if not self.enabled:
            logger.warning("Push notification not sent (no VAPID key): %s - %s", title, body)
            return
        if self._run_async:
            self._spawn(lambda: self.send(title, body))
        else:
            self.send(title, body)

    def send(self, title: str, body: str) -> int:
        """Deliver synchronously; returns the number of successful sends."""
        webpush = self._webpush
        if webpush is None:
            from pywebpush import webpush  # imported lazily (heavy)
        from pywebpush import WebPushException

        payload = json.dumps({"title": title, "body": body})
        expired: set[str] = set()
        delivered = 0
        for sub in self._store.all():
            try:
                webpush(sub, data=payload, vapid_private_key=self._private_key,
                        vapid_claims={"sub": self._contact}, timeout=self.TIMEOUT_S)
                delivered += 1
            except WebPushException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):
                    expired.add(sub.get("endpoint"))
                else:
                    logger.warning("Push to %s failed (%s): %s", sub.get("endpoint", "?")[:40], status, e)
            except Exception as e:
                logger.error("Push to %s failed: %s", sub.get("endpoint", "?")[:40], e)
        if expired:
            logger.info("Removing %d expired push subscription(s)", len(expired))
            self._store.remove(expired)
        return delivered
