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


# ── release branches ─────────────────────────────────────────────────────────

GIT = ["git", "-c", "safe.directory=*"]


def test_parse_args_is_backward_compatible():
    assert update_script.parse_args(["/x/app.py", "12", "svc"]) == (None, ["/x/app.py", "12", "svc"], True)
    assert update_script.parse_args(["--branch", "claude/dev", "/x/app.py", "12"]) == \
        ("claude/dev", ["/x/app.py", "12"], True)
    assert update_script.parse_args(["--branch=main", "/x/app.py", "12"]) == ("main", ["/x/app.py", "12"], True)


@pytest.mark.parametrize("name", ["--upload-pack=x", "main;rm -rf /", "../x", "a..b", "-x", "a b"])
def test_parse_args_refuses_unsafe_branch(name):
    branch, rest, valid = update_script.parse_args(["--branch", name, "/x/app.py", "12"])
    assert branch is None and not valid and rest == ["/x/app.py", "12"]


def test_switch_branch_flow(fakes, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["update_script.py", "--branch", "claude/dev", "/x/src/app.py", "1234",
                                      "chicken.service"])
    update_script.main()
    fakes.kill.assert_called_once_with(1234, signal.SIGTERM)
    cmds = _run_cmds(fakes)
    fetch = GIT + ["fetch", "origin", "+refs/heads/claude/dev:refs/remotes/origin/claude/dev"]
    checkout = GIT + ["checkout", "-f", "-B", "claude/dev", "origin/claude/dev"]
    upstream = GIT + ["branch", "--set-upstream-to=origin/claude/dev", "claude/dev"]
    reset = GIT + ["reset", "--hard", "origin/claude/dev"]
    for c in (fetch, checkout, upstream, reset):
        assert c in cmds
    assert cmds.index(fetch) < cmds.index(checkout) < cmds.index(upstream) < cmds.index(reset)
    assert GIT + ["pull"] not in cmds and GIT + ["reset", "--hard", "@{u}"] not in cmds
    assert cmds[-1] == ["sudo", "systemctl", "restart", "chicken.service"]


def test_failed_checkout_still_restarts(fakes, monkeypatch):
    def run(cmd, check=False, **kw):
        if "checkout" in cmd and check:
            raise subprocess.CalledProcessError(1, cmd)
        return mock.Mock(returncode=0, stdout="")
    fakes.run.side_effect = run
    monkeypatch.setattr(sys, "argv", ["update_script.py", "--branch", "main", "/x/src/app.py", "1234"])
    update_script.main()
    assert not any("reset" in c for c in _run_cmds(fakes))
    fakes.Popen.assert_called_once()


def test_unsafe_branch_only_restarts(fakes, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["update_script.py", "--branch", "--upload-pack=x", "/x/src/app.py", "1234"])
    update_script.main()
    assert not any(c[0] == "git" for c in _run_cmds(fakes))
    args, _ = fakes.Popen.call_args
    assert args[0] == [sys.executable, "/x/src/app.py"]


def _git_fake(heads, changed):
    """run() answering rev-parse with successive heads and diff with ``changed``."""
    heads = list(heads)

    def run(cmd, **kw):
        if cmd[:3] == GIT and cmd[3:5] == ["rev-parse", "HEAD"]:
            return mock.Mock(returncode=0, stdout=heads.pop(0) + "\n")
        if cmd[:3] == GIT and cmd[3] == "diff":
            return mock.Mock(returncode=0, stdout=changed)
        return mock.Mock(returncode=0, stdout="")
    return run


def test_requirements_are_installed_when_changed(fakes, monkeypatch):
    fakes.run.side_effect = _git_fake(["aaa", "bbb"], "requirements.txt\n")
    monkeypatch.setattr(sys, "argv", ["update_script.py", "--branch", "main", "/x/src/app.py", "1234", "svc"])
    update_script.main()
    cmds = _run_cmds(fakes)
    assert GIT + ["diff", "--name-only", "aaa", "bbb", "--", "requirements.txt"] in cmds
    pip = [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    assert pip in cmds and cmds.index(pip) < cmds.index(["sudo", "systemctl", "restart", "svc"])


@pytest.mark.parametrize("heads,changed", [(["aaa", "aaa"], ""), (["aaa", "bbb"], "")])
def test_requirements_not_installed_when_unchanged(fakes, monkeypatch, heads, changed):
    fakes.run.side_effect = _git_fake(heads, changed)
    monkeypatch.setattr(sys, "argv", ["update_script.py", "/x/src/app.py", "1234"])
    update_script.main()
    assert not any(c[:2] == [sys.executable, "-m"] for c in _run_cmds(fakes))
    fakes.Popen.assert_called_once()


def test_failed_pip_install_still_restarts(fakes, monkeypatch):
    base = _git_fake(["aaa", "bbb"], "requirements.txt\n")

    def run(cmd, **kw):
        if cmd[:2] == [sys.executable, "-m"]:
            raise subprocess.TimeoutExpired(cmd, 900)
        return base(cmd, **kw)
    fakes.run.side_effect = run
    monkeypatch.setattr(sys, "argv", ["update_script.py", "/x/src/app.py", "1234"])
    update_script.main()
    fakes.Popen.assert_called_once()
