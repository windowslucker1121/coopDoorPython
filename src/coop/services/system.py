"""Operating-system level operations: metrics, version, time, reboot, update."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Callable

import psutil

from ..paths import Paths

logger = logging.getLogger(__name__)


class SystemService:
    def __init__(self, paths: Paths, *, allow_system_changes: bool = True,
                 run: Callable = subprocess.run, popen: Callable = subprocess.Popen,
                 spawn: Callable[[Callable[[], None]], None] | None = None,
                 exit_process: Callable[[int], None] = os._exit, sleep: Callable[[float], None] = time.sleep):
        self._paths = paths
        self._allow = allow_system_changes
        self._run = run
        self._popen = popen
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self._exit = exit_process
        self._sleep = sleep
        self._boot_time = datetime.fromtimestamp(psutil.boot_time())

    # ── information ──────────────────────────────────────────────────
    def uptime(self) -> str:
        delta = datetime.now() - self._boot_time
        hours, rem = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{delta.days} day(s), {hours} hour(s), {minutes} minute(s), {seconds} second(s)"

    def metrics(self) -> dict:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep) if os.name == "nt" else "/")
        return {
            "cpu_percent": psutil.cpu_percent(interval=0),
            "ram_used_mb": mem.used / (1024 * 1024),
            "ram_total_mb": mem.total / (1024 * 1024),
            "ram_percent": mem.percent,
            "disk_used_gb": disk.used / 1024 ** 3,
            "disk_total_gb": disk.total / 1024 ** 3,
            "disk_percent": disk.percent,
        }

    def version(self) -> str:
        try:
            with open(self._paths.version_file, encoding="utf-8") as f:
                return f.read().strip() or "unknown"
        except OSError:
            return "unknown"

    def record_git_version(self) -> None:
        """Write the current commit hash to ``version.txt``."""
        try:
            commit = subprocess.check_output(
                ["git", "-c", "safe.directory=*", "rev-parse", "--short", "HEAD"],
                cwd=self._paths.root, stderr=subprocess.DEVNULL).decode().strip()
            tmp = self._paths.version_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(commit)
            os.replace(tmp, self._paths.version_file)
            logger.info("Version %s", commit)
        except Exception as e:
            logger.warning("Could not determine git version: %s", e)

    # ── actions ──────────────────────────────────────────────────────
    def set_time(self, value: str) -> None:
        """Set the system clock (``YYYY-MM-DD HH:MM:SS``).

        Raises ``ValueError`` for a bad format and ``RuntimeError`` if the
        command fails."""
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        logger.info("[System Time] %s -> %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), value)
        if not self._allow:
            logger.warning("[System Time] not changed (mock hardware / non-Linux host)")
            return
        result = self._run(["sudo", "date", "-s", value], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "date failed")

    def reboot(self) -> None:
        if not self._allow:
            raise RuntimeError("Reboot is not supported on this host")
        logger.warning("Device reboot requested.")

        def do_reboot():
            self._sleep(1)
            try:
                os.sync()
            except Exception:
                pass
            self._popen(["sudo", "systemctl", "reboot"], start_new_session=True)

        self._spawn(do_reboot)

    def start_update(self) -> None:
        """Launch the detached update helper, then exit this process."""
        logger.info("Update requested. Starting update helper...")
        service = self._systemd_service_name()
        cmd = [sys.executable, self._paths.update_script, self._paths.app_entrypoint, str(os.getpid())]
        if service:
            cmd.append(service)
        if os.name == "nt":
            self._popen(cmd, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        else:
            self._popen(cmd, start_new_session=True)

        def shutdown():
            self._sleep(1)
            logger.info("Shutting down for update...")
            self._exit(0)

        self._spawn(shutdown)

    @staticmethod
    def _systemd_service_name() -> str:
        if os.name == "nt" or not os.environ.get("INVOCATION_ID"):
            return ""
        try:
            with open(f"/proc/{os.getpid()}/cgroup", encoding="utf-8") as f:
                for line in f:
                    if ".service" in line:
                        return line.strip().split("/")[-1]
        except OSError as e:
            logger.error("Failed to get service name: %s", e)
        return ""
