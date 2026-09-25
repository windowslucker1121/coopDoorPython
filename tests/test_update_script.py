"""Tests for ``src/update_script.py`` — the self-update helper launched by
``POST /update``.  Every process / git / filesystem action is faked.
"""

import signal
import subprocess
import sys
from unittest import mock

import pytest

import update_script


@pytest.fixture
def fakes(monkeypatch):
    f = mock.Mock()
    monkeypatch.setattr(update_script.os, "kill", f.kill)
    monkeypatch.setattr(update_script.os, "chdir", f.chdir)
    monkeypatch.setattr(update_script.os, "name", "posix")
    monkeypatch.setattr(update_script.time, "sleep", f.sleep)
    monkeypatch.setattr(update_script.subprocess, "run", f.run)
    monkeypatch.setattr(update_script.subprocess, "Popen", f.Popen)
    return f


def _run_cmds(f):
    return [c[0][0] for c in f.run.call_args_list]


def test_systemd_flow(fakes, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["update_script.py", "/x/src/app.py", "1234", "chicken.service"])
    update_script.main()

    fakes.kill.assert_called_once_with(1234, signal.SIGTERM)
    cmds = _run_cmds(fakes)
    assert ["killall", "libgpiod_pulsein64"] in cmds
    assert ["find", ".git/objects/", "-type", "f", "-size", "0", "-delete"] in cmds
    assert ["git", "-c", "safe.directory=*", "fetch", "--all"] in cmds
    assert ["git", "-c", "safe.directory=*", "reset", "--hard", "@{u}"] in cmds
    assert ["git", "-c", "safe.directory=*", "pull"] in cmds
    assert cmds[-1] == ["sudo", "systemctl", "restart", "chicken.service"]
    fakes.Popen.assert_not_called()


def test_direct_relaunch_without_service(fakes, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["update_script.py", "/x/src/app.py", "1234"])
    update_script.main()
    args, kwargs = fakes.Popen.call_args
    assert args[0] == [sys.executable, "/x/src/app.py"]
    assert "preexec_fn" in kwargs
    fakes.chdir.assert_called_with("/x/src")


def test_already_exited_parent_is_tolerated(fakes, monkeypatch):
    fakes.kill.side_effect = ProcessLookupError
    monkeypatch.setattr(sys, "argv", ["update_script.py", "/x/src/app.py", "1234"])
    update_script.main()
    fakes.Popen.assert_called_once()


def test_failed_git_pull_still_restarts(fakes, monkeypatch):
    def run(cmd, check=False, **kw):
        if cmd[-1] == "pull" and check:
            raise subprocess.CalledProcessError(1, cmd)
    fakes.run.side_effect = run
    monkeypatch.setattr(sys, "argv", ["update_script.py", "/x/src/app.py", "1234"])
    update_script.main()
    fakes.Popen.assert_called_once()


def test_missing_killall_is_tolerated(fakes, monkeypatch):
    def run(cmd, **kw):
        if cmd[0] == "killall":
            raise FileNotFoundError
    fakes.run.side_effect = run
    monkeypatch.setattr(sys, "argv", ["update_script.py", "/x/src/app.py", "1234"])
    update_script.main()
    fakes.Popen.assert_called_once()
