"""Operating-system level operations: metrics, version, time, reboot, update
and release (git branch) selection."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Callable

import psutil

from ..paths import Paths

logger = logging.getLogger(__name__)

#: The branch that holds stable releases ("Switch to stable" goes here).
STABLE_BRANCH = "main"
#: Remote whose branches are offered as releases.
REMOTE = "origin"

# Conservative subset of git's ref-name rules: ASCII words separated by single
# slashes, never starting with "-" (option injection) or "." (hidden / "..").
_BRANCH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*")


def is_safe_branch_name(name: object) -> bool:
    """True for a branch name that is safe to hand to git as an argument.

    The same rules are duplicated in ``src/update_script.py`` (which must run
    stand-alone)."""
    if not isinstance(name, str) or not 0 < len(name) <= 200:
        return False
    if not _BRANCH_RE.fullmatch(name):
        return False
    if ".." in name or name.endswith((".", ".lock")) or ".lock/" in name:
        return False
    return name != "HEAD"


def parse_branch_list(output: str) -> list[dict]:
    """Parse ``git for-each-ref`` output (NUL-separated full refname, short
    hash, ISO date and subject per line) into branch dicts, skipping
    ``HEAD`` and anything that is not a safe branch name."""
    prefix = f"refs/remotes/{REMOTE}/"
    branches = []
    for line in (output or "").splitlines():
        parts = line.split("\0")
        if len(parts) < 4 or not parts[0].startswith(prefix):
            continue
        name = parts[0][len(prefix):]
        if not is_safe_branch_name(name):
            continue
        branches.append({"name": name, "commit": parts[1], "date": parts[2], "subject": " ".join(parts[3:])})
    return branches


class GitError(RuntimeError):
    """A git command failed, timed out or git is not available."""


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
        # The code checkout is the parent of src/ - not paths.root, which
        # COOP_ROOT may relocate for config and logs.
        self.repo_dir = os.path.dirname(os.path.abspath(paths.src))
        self.stable_branch = STABLE_BRANCH

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

    # ── releases (git branches) ──────────────────────────────────────
    def _git(self, *args: str, timeout: float = 10) -> str:
        """Run git in the code checkout and return stdout.

        Arguments are passed as a list (never through a shell); callers only
        pass constants or names checked by :func:`is_safe_branch_name`."""
        cmd = ["git", "-c", "safe.directory=*", *args]
        try:
            result = self._run(cmd, cwd=self.repo_dir, capture_output=True, text=True, timeout=timeout,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C"))
        except FileNotFoundError:
            raise GitError("git is not installed") from None
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} timed out after {timeout:g} s") from None
        except OSError as e:
            raise GitError(f"git {args[0]} failed: {e}") from None
        if result.returncode != 0:
            err = (result.stderr if isinstance(result.stderr, str) else "").strip().splitlines()
            raise GitError(err[-1] if err else f"git {args[0]} failed (exit {result.returncode})")
        return result.stdout if isinstance(result.stdout, str) else ""

    def _current_branch(self) -> str | None:
        """Checked-out branch name, or ``None`` for a detached HEAD / no repo."""
        try:
            return self._git("symbolic-ref", "--short", "-q", "HEAD").strip() or None
        except GitError:
            return None

    def _is_git_checkout(self) -> bool:
        try:
            return self._git("rev-parse", "--is-inside-work-tree").strip() == "true"
        except GitError:
            return False

    def _fetch(self, *refspecs: str, prune: bool = False, timeout: float = 20) -> str | None:
        """Fetch from the release remote; returns an error message or ``None``."""
        try:
            self._git("fetch", "--quiet", *(["--prune"] if prune else []), REMOTE, *refspecs, timeout=timeout)
            return None
        except GitError as e:
            logger.warning("git fetch failed: %s", e)
            return str(e)

    def _remote_branches(self) -> list[dict]:
        out = self._git("for-each-ref", "--sort=-committerdate",
                        "--format=%(refname)%00%(objectname:short)%00%(committerdate:iso-strict)%00%(subject)",
                        f"refs/remotes/{REMOTE}/")
        return parse_branch_list(out)

    def update_info(self, check: bool = False) -> dict:
        """The running release: branch / channel and commit.  With ``check``
        the upstream is fetched first so ``behind`` is up to date."""
        info = {"supported": self._allow, "stable_branch": self.stable_branch, "git": False,
                "branch": None, "detached": False, "channel": None, "commit": None,
                "commit_date": None, "subject": None, "upstream": None, "behind": None,
                "checked": False, "error": None}
        try:
            head = self._git("log", "-1", "--format=%h%x00%cI%x00%s", "HEAD").strip().split("\0")
        except GitError as e:
            info["error"] = f"Release information unavailable: {e}"
            return info
        info["git"] = True
        info["commit"], info["commit_date"], info["subject"] = (head + ["", "", ""])[:3]
        branch = self._current_branch()
        info["branch"] = branch
        info["detached"] = branch is None
        info["channel"] = "stable" if branch == self.stable_branch else "dev"
        if branch is None:
            return info
        try:
            upstream = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").strip()
        except GitError:
            upstream = ""
        info["upstream"] = upstream or None
        if not upstream:
            return info
        if check:
            remote, _, name = upstream.partition("/")
            if remote == REMOTE and is_safe_branch_name(name):
                err = self._fetch(f"+refs/heads/{name}:refs/remotes/{REMOTE}/{name}", timeout=15)
                info["checked"] = err is None
                if err:
                    info["error"] = f"Could not check for updates: {err}"
        try:
            info["behind"] = int(self._git("rev-list", "--count", "HEAD..@{u}").strip() or 0)
        except (GitError, ValueError):
            pass
        return info

    def list_branches(self) -> dict:
        """Refresh (``git fetch --prune``) and list the remote's branches,
        newest commit first.  A failed fetch falls back to the known refs."""
        result = {"branches": [], "current": None, "stable_branch": self.stable_branch,
                  "refreshed": False, "warning": None, "error": None}
        err = self._fetch(prune=True)
        result["refreshed"] = err is None
        try:
            branches = self._remote_branches()
        except GitError as e:
            result["error"] = f"Branches unavailable: {e}"
            return result
        if err:
            result["warning"] = f"Could not refresh the branch list ({err}) - showing the last known branches."
        current = self._current_branch()
        for b in branches:
            b["current"] = b["name"] == current
            b["stable"] = b["name"] == self.stable_branch
        result["branches"] = branches
        result["current"] = current
        return result

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

    def start_update(self, branch: str | None = None) -> None:
        """Launch the detached update helper, then exit this process.

        Without ``branch`` the current branch is updated from its upstream;
        with ``branch`` the checkout switches to ``origin/<branch>``.
        Raises ``RuntimeError`` on hosts that must not change and
        ``ValueError`` for an unknown / unsafe branch."""
        if not self._allow:
            raise RuntimeError("Updating is not supported on this host (mock hardware)")
        if branch is not None:
            if not is_safe_branch_name(branch):
                raise ValueError("Invalid branch name")
            self._fetch(prune=True)
            try:
                known = {b["name"] for b in self._remote_branches()}
            except GitError as e:
                raise RuntimeError(f"Cannot switch release: {e}") from None
            if branch not in known:
                raise ValueError(f"Unknown branch: {branch}")
            logger.info("Switch to release branch %s requested. Starting update helper...", branch)
        else:
            if self._current_branch() is None and self._is_git_checkout():
                raise ValueError("The checkout is not on a branch (detached HEAD) - choose a release to switch to")
            logger.info("Update requested. Starting update helper...")
        service = self._systemd_service_name()
        cmd = [sys.executable, self._paths.update_script]
        if branch is not None:
            cmd += ["--branch", branch]
        cmd += [self._paths.app_entrypoint, str(os.getpid())]
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
