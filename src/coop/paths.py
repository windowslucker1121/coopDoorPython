"""Filesystem locations used by the application.

Everything is resolved against the repository root (never the process's
working directory), so the app behaves the same whether it is started by
systemd, cron or from a shell in another directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def default_root() -> str:
    # src/coop/paths.py → repository root is two levels above src/coop
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


@dataclass(frozen=True)
class Paths:
    root: str
    src_dir: str | None = None  # code/templates location (defaults to <root>/src)

    @classmethod
    def default(cls) -> "Paths":
        return cls(default_root())

    @property
    def src(self) -> str:
        return self.src_dir or os.path.join(self.root, "src")

    @property
    def config(self) -> str:
        return os.path.join(self.root, "config.yaml")

    @property
    def secrets(self) -> str:
        return os.path.join(self.root, ".secrets.yaml")

    @property
    def subscriptions(self) -> str:
        return os.path.join(self.root, ".subscriptions.json")

    @property
    def version_file(self) -> str:
        return os.path.join(self.root, "version.txt")

    @property
    def log_dir(self) -> str:
        return os.path.join(self.root, "log")

    @property
    def app_entrypoint(self) -> str:
        return os.path.join(self.src, "app.py")

    @property
    def update_script(self) -> str:
        return os.path.join(self.src, "update_script.py")
