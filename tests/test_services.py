"""Services: environment monitor, push notifications, data logging, system, workers, logging."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from unittest import mock

import pytest

from conftest import DENVER, FixedSensor, at
from coop.clock import FakeClock
from coop.hardware.sensors import Reading, TemperatureSensor
from coop.logging_setup import LogBuffer, configure_logging
from coop.paths import Paths
from coop.services import datalog
from coop.services.environment import EnvironmentMonitor, MetricTracker, MetricValue
from coop.services.notifications import PushNotifier, SubscriptionStore, load_vapid_keys
from coop.services.system import SystemService
from coop.workers import Worker, once


# ═══════════════════════════ environment ═════════════════════════════════════

class TestMetricTracker:

    def test_min_max(self):
        t = MetricTracker("t", 5)
        for v in (20, 22, 19):
            t.update(v)
        assert t.current == MetricValue(19, 19, 22)

    def test_none_ignored(self):
        t = MetricTracker("t", 5)
        t.update(20)
        t.update(None)
        assert t.current.value == 20

    def test_single_spike_rejected(self):
        t = MetricTracker("t", 3)
        for v in (20, 40, 21):
            t.update(v)
        assert t.current == MetricValue(21, 20, 21)

    def test_repeated_change_accepted(self):
        t = MetricTracker("t", 3, accept_after=3)
        for v in (20, 40, 40, 40):
            t.update(v)
        assert t.current.value == 40 and t.current.max == 40

    def test_reset_extremes_keeps_value(self):
        t = MetricTracker("t", 3)
        t.update(20)
        t.reset_extremes()
        assert t.current == MetricValue(20, None, None)


class ScriptSensor(TemperatureSensor):
    def __init__(self, readings):
        self.readings = list(readings)

    def read(self):
        return self.readings.pop(0) if self.readings else Reading()


class TestEnvironmentMonitor:

    def test_poll(self):
        clock = FakeClock(at(12), DENVER)
        m = EnvironmentMonitor(FixedSensor(temperature=21, humidity=40), FixedSensor(temperature=5, humidity=80),
                               FixedSensor(temperature=50), clock)
        assert m.poll() == EnvironmentMonitor.INTERVAL_S
        v = m.values
        assert (v["temp_in"].value, v["hum_in"].value, v["temp_out"].value, v["hum_out"].value,
                v["cpu_temp"].value) == (21, 40, 5, 80, 50)

    def test_daily_reset_leaves_blank_extremes_for_missing_sensor(self):
        """Regression: sentinels 500/-500 were shown as 260 °C / -296 °C."""
        clock = FakeClock(at(23, 59), DENVER)
        out = ScriptSensor([Reading(5, 80)])
        m = EnvironmentMonitor(FixedSensor(temperature=21), out, FixedSensor(), clock)
        m.poll()
        clock.set_now(at(0, 1, day=2))
        m.poll()
        v = m.values["temp_out"]
        assert (v.value, v.min, v.max) == (5, None, None)

    def test_sensor_exception_is_contained(self):
        class Broken(TemperatureSensor):
            def read(self):
                raise RuntimeError("gone")
        m = EnvironmentMonitor(Broken(), Broken(), Broken(), FakeClock())
        m.poll()
        assert m.values["temp_in"] == MetricValue()


# ═══════════════════════════ notifications ═══════════════════════════════════

class TestVapidKeys:

    def test_load(self, tmp_path):
        p = tmp_path / ".secrets.yaml"
        p.write_text("secrets:\n  vapid_public_key: PUB\n  vapid_private_key: PRIV\n")
        assert load_vapid_keys(str(p)) == ("PUB", "PRIV")

    @pytest.mark.parametrize("content", [None, "other: 1\n", "{bad", "- 1\n"])
    def test_missing_or_invalid(self, tmp_path, content):
        p = tmp_path / ".secrets.yaml"
        if content is not None:
            p.write_text(content)
        assert load_vapid_keys(str(p)) == (None, None)


class TestSubscriptionStore:

    def test_add_replaces_same_endpoint(self, tmp_path):
        s = SubscriptionStore(str(tmp_path / "subs.json"))
        s.add({"endpoint": "a", "v": 1})
        s.add({"endpoint": "b"})
        s.add({"endpoint": "a", "v": 2})
        assert s.all() == [{"endpoint": "b"}, {"endpoint": "a", "v": 2}]

    def test_remove(self, tmp_path):
        s = SubscriptionStore(str(tmp_path / "subs.json"))
        s.add({"endpoint": "a"}); s.add({"endpoint": "b"})
        s.remove({"a"})
        assert s.all() == [{"endpoint": "b"}]

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / "subs.json"
        p.write_text("{not json")
        s = SubscriptionStore(str(p))
        assert s.all() == []
        s.add({"endpoint": "x"})
        assert json.loads(p.read_text()) == {"subscriptions": [{"endpoint": "x"}]}


class TestPushNotifier:

    def notifier(self, tmp_path, webpush, subs=({"endpoint": "a"},), key="KEY", **kw):
        store = SubscriptionStore(str(tmp_path / "subs.json"))
        for s in subs:
            store.add(dict(s))
        return PushNotifier(store, key, webpush=webpush, run_async=False, **kw), store

    def test_sends_payload(self, tmp_path):
        push = mock.Mock()
        n, _ = self.notifier(tmp_path, push)
        assert n.send("Title", "Body") == 1
        kwargs = push.call_args.kwargs
        assert json.loads(kwargs["data"]) == {"title": "Title", "body": "Body"}
        assert kwargs["vapid_private_key"] == "KEY" and kwargs["timeout"] == 10

    def test_expired_subscriptions_removed(self, tmp_path):
        from pywebpush import WebPushException

        def push(sub, **kw):
            code = {"gone": 410, "missing": 404, "flaky": 500}.get(sub["endpoint"])
            if code:
                raise WebPushException("x", response=mock.Mock(status_code=code))

        subs = [{"endpoint": e} for e in ("ok", "gone", "missing", "flaky")]
        n, store = self.notifier(tmp_path, push, subs)
        n.notify("t", "b")
        assert [s["endpoint"] for s in store.all()] == ["ok", "flaky"]

    def test_file_untouched_without_expired(self, tmp_path):
        n, store = self.notifier(tmp_path, mock.Mock())
        path = tmp_path / "subs.json"
        os.utime(path, (1, 1))
        n.notify("t", "b")
        assert os.stat(path).st_mtime == 1

    def test_generic_errors_are_contained(self, tmp_path):
        n, store = self.notifier(tmp_path, mock.Mock(side_effect=RuntimeError("dns")))
        assert n.send("t", "b") == 0
        assert len(store.all()) == 1

    def test_disabled_without_key(self, tmp_path):
        push = mock.Mock()
        n, _ = self.notifier(tmp_path, push, key=None)
        assert not n.enabled
        n.notify("t", "b")
        push.assert_not_called()

    def test_async_uses_spawn(self, tmp_path):
        spawned = []
        store = SubscriptionStore(str(tmp_path / "s.json"))
        n = PushNotifier(store, "K", webpush=mock.Mock(), spawn=spawned.append)
        n.notify("t", "b")
        assert len(spawned) == 1


# ═══════════════════════════ data logging ════════════════════════════════════

class TestCsv:

    def test_logger_quotes_values_and_writes_header_once(self, tmp_path):
        now = at(12)
        row = {"time": "t", "uptime": "1 day(s), 2 hour(s)", "errorstate": "jam, bad"}
        lg = datalog.CsvDataLogger(str(tmp_path), lambda: row, lambda: now)
        lg.write_row(); lg.write_row()
        text = open(lg.current_file()).read()
        assert text.startswith("# time, uptime, errorstate\n")
        assert text.count('t,"1 day(s), 2 hour(s)","jam, bad"') == 2
        assert os.path.basename(lg.current_file()) == "2025_06_01.csv"

    def test_logger_survives_errors(self, tmp_path):
        lg = datalog.CsvDataLogger(str(tmp_path), mock.Mock(side_effect=RuntimeError), lambda: at(12))
        assert lg.write_row() == datalog.CsvDataLogger.INTERVAL_S

    def test_round_trip(self, tmp_path):
        row = {"time": "10:00:00.123", "temp_in": "21.5°C", "uptime": "0 day(s), 1 hour(s)", "state": "open",
               "auto_mode": "True", "errorstate": "a, b", "hum_in": ""}
        lg = datalog.CsvDataLogger(str(tmp_path), lambda: row, lambda: at(12))
        lg.write_row()
        data = datalog.read_csv(lg.current_file())
        assert data["rows"] == [{"time": "10:00:00", "temp_in": 21.5, "state": "open", "auto_mode": "True",
                                 "errorstate": "a, b", "hum_in": None}]

    def test_legacy_unquoted_rows_realigned(self, tmp_path):
        p = tmp_path / "old.csv"
        p.write_text("# time, temp_in, uptime, auto_mode, errorstate\n"
                     "10:00:00.000, 21.5°C, 0 day(s), 1 hour(s), 2 minute(s), True, \n"
                     "short, row\n")
        assert datalog.read_csv(str(p))["rows"] == [{"time": "10:00:00", "temp_in": 21.5,
                                                     "auto_mode": "True", "errorstate": ""}]

    def test_downsampling(self, tmp_path):
        p = tmp_path / "big.csv"
        p.write_text("# time, temp_in\n" + "".join(f"t{i}, {i}°C\n" for i in range(1500)))
        data = datalog.read_csv(str(p))
        assert (data["count"], data["total"]) == (600, 1500)
        assert [r["time"] for r in data["rows"][:2]] == ["t0", "t2"]

    def test_listing(self, tmp_path):
        for name in ("2025_01_01.csv", "2025_01_03.csv", "app.log", "x.txt"):
            (tmp_path / name).write_text("x")
        assert [f["name"] for f in datalog.list_csv_files(str(tmp_path))] == ["2025_01_03.csv", "2025_01_01.csv"]
        assert datalog.list_csv_files(str(tmp_path / "missing")) == []

    @pytest.mark.parametrize("name, ok", [("a.csv", True), ("../a.csv", False), (".hidden.csv", False),
                                          ("a.txt", False)])
    def test_csv_names(self, name, ok):
        assert datalog.is_csv_name(name) is ok


class TestAppLogs:

    @pytest.mark.parametrize("name, ok", [("app.log", True), ("app.log.2025-01-02", True),
                                          ("app_20240101_120000.log", True), ("app.log.bad", False),
                                          ("other.log", False)])
    def test_names(self, name, ok):
        assert datalog.is_app_log_name(name) is ok

    def test_listing_newest_first(self, tmp_path):
        for i, name in enumerate(("app.log", "app.log.2025-01-02", "other.log")):
            p = tmp_path / name
            p.write_text("x")
            os.utime(p, (100 + i, 100 + i))
        assert [f["name"] for f in datalog.list_app_logs(str(tmp_path))] == ["app.log.2025-01-02", "app.log"]

    def test_parse(self, tmp_path):
        p = tmp_path / "app.log"
        p.write_text("2025-01-01 10:00:00,000 - door - info - Door opening - fast\n\nTraceback\n")
        assert datalog.read_app_log(str(p)) == [
            {"t": "2025-01-01 10:00:00,000", "lg": "door", "lv": "INFO", "m": "Door opening - fast"},
            {"t": "", "lg": "", "lv": "RAW", "m": "Traceback"},
        ]


# ═══════════════════════════ system ══════════════════════════════════════════

class TestSystem:

    def service(self, tmp_path, **kw):
        self.popen = mock.Mock()
        self.run = mock.Mock(return_value=mock.Mock(returncode=0, stderr=""))
        self.spawned = []
        self.exits = []
        return SystemService(Paths(str(tmp_path)), run=self.run, popen=self.popen, spawn=self.spawned.append,
                             exit_process=self.exits.append, sleep=lambda s: None, **kw)

    def test_uptime_and_metrics(self, tmp_path):
        s = self.service(tmp_path)
        assert s.uptime().endswith("second(s)")
        assert set(s.metrics()) == {"cpu_percent", "ram_used_mb", "ram_total_mb", "ram_percent",
                                    "disk_used_gb", "disk_total_gb", "disk_percent"}

    def test_version(self, tmp_path):
        s = self.service(tmp_path)
        assert s.version() == "unknown"
        (tmp_path / "version.txt").write_text("abc\n")
        assert s.version() == "abc"

    def test_set_time(self, tmp_path):
        s = self.service(tmp_path)
        s.set_time("2025-01-01 10:00:00")
        assert self.run.call_args[0][0] == ["sudo", "date", "-s", "2025-01-01 10:00:00"]
        with pytest.raises(ValueError):
            s.set_time("2025/01/01")
        self.run.return_value = mock.Mock(returncode=1, stderr="denied")
        with pytest.raises(RuntimeError, match="denied"):
            s.set_time("2025-01-01 10:00:00")

    def test_set_time_not_allowed_on_mock_host(self, tmp_path):
        s = self.service(tmp_path, allow_system_changes=False)
        s.set_time("2025-01-01 10:00:00")
        self.run.assert_not_called()

    def test_reboot(self, tmp_path):
        s = self.service(tmp_path)
        s.reboot()
        self.spawned[0]()
        assert self.popen.call_args[0][0] == ["sudo", "systemctl", "reboot"]
        with pytest.raises(RuntimeError):
            self.service(tmp_path, allow_system_changes=False).reboot()

    def test_update(self, tmp_path, monkeypatch):
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        s = self.service(tmp_path)
        s.start_update()
        cmd = self.popen.call_args[0][0]
        assert cmd[1].endswith(os.path.join("src", "update_script.py"))
        assert cmd[2].endswith(os.path.join("src", "app.py"))
        assert cmd[3] == str(os.getpid()) and len(cmd) == 4
        self.spawned[0]()
        assert self.exits == [0]

    def test_update_refused_on_mock_host(self, tmp_path):
        s = self.service(tmp_path, allow_system_changes=False)
        with pytest.raises(RuntimeError, match="not supported"):
            s.start_update()
        self.popen.assert_not_called()
        assert self.spawned == []

    def test_systemd_service_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INVOCATION_ID", "x")
        real_open = open

        def fake_open(path, *a, **k):
            if str(path).endswith("/cgroup"):
                import io
                return io.StringIO("0::/system.slice/chicken.service\n")
            return real_open(path, *a, **k)
        monkeypatch.setattr("builtins.open", fake_open)
        assert SystemService._systemd_service_name() == "chicken.service"


# ═══════════════════════════ releases (git branches) ═════════════════════════

REFS = "\n".join([
    "refs/remotes/origin/HEAD\x00abc1234\x002026-09-03T10:00:00+00:00\x00Stable release",
    "refs/remotes/origin/claude/dev\x00bbb2222\x002026-09-02T10:00:00+00:00\x00Dev work",
    "refs/remotes/origin/main\x00abc1234\x002026-09-01T10:00:00+00:00\x00Stable release",
    "refs/remotes/origin/-evil\x00ccc3333\x002026-08-01T10:00:00+00:00\x00Bad name",
    "refs/remotes/upstream/other\x00ddd4444\x002026-08-01T10:00:00+00:00\x00Other remote",
    "garbage line",
]) + "\n"


class FakeGit:
    """``run`` stand-in answering the git commands SystemService issues."""

    def __init__(self, branch="main", upstream="origin/main", behind="2", fetch_error=None,
                 refs=REFS, missing=False):
        self.branch, self.upstream, self.behind = branch, upstream, behind
        self.fetch_error, self.refs, self.missing = fetch_error, refs, missing
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] != "git":
            return mock.Mock(returncode=0, stdout="", stderr="")
        assert cmd[1:3] == ["-c", "safe.directory=*"] and "shell" not in kw and kw.get("timeout")
        if self.missing:
            raise FileNotFoundError("git")
        args = cmd[3:]

        def ok(out=""):
            return mock.Mock(returncode=0, stdout=out, stderr="")

        def fail(err="fatal: nope"):
            return mock.Mock(returncode=1, stdout="", stderr=err)

        sub = args[0]
        if sub == "fetch":
            if isinstance(self.fetch_error, BaseException):
                raise self.fetch_error
            return fail(self.fetch_error) if self.fetch_error else ok()
        if sub == "log":
            return ok("abc1234\x002026-09-01T10:00:00+00:00\x00Stable release\n")
        if sub == "symbolic-ref":
            return ok(self.branch + "\n") if self.branch else fail("")
        if sub == "rev-parse" and "@{u}" in args:
            return ok(self.upstream + "\n") if self.upstream else fail("fatal: no upstream")
        if sub == "rev-parse":
            return ok("true\n")
        if sub == "rev-list":
            return ok(self.behind + "\n")
        if sub == "for-each-ref":
            return ok(self.refs)
        raise AssertionError(f"unexpected git call {cmd}")

    def subcommands(self):
        return [c[3] for c in self.calls if c[0] == "git"]


class TestReleases:

    def service(self, tmp_path, git: FakeGit, **kw):
        self.popen = mock.Mock()
        self.spawned = []
        self.exits = []
        return SystemService(Paths(str(tmp_path)), run=git, popen=self.popen, spawn=self.spawned.append,
                             exit_process=self.exits.append, sleep=lambda s: None, **kw)

    @pytest.mark.parametrize("name", ["main", "dev", "claude/epic-albattani-otolau", "feat/data_classes",
                                      "v1.2.3", "release-2026.09", "a/b/c"])
    def test_safe_branch_names(self, name):
        from coop.services.system import is_safe_branch_name
        assert is_safe_branch_name(name)

    @pytest.mark.parametrize("name", [
        "", " ", "--upload-pack=x", "-x", "main;rm -rf /", "main && reboot", "$(reboot)", "`id`",
        "../x", "a/../b", "a..b", "a b", "a\nb", "a\tb", "a\x00b", "/main", "main/", "a//b", ".hidden",
        "a/.b", "x.lock", "a.lock/b", "main.", "a@{u}", "a~1", "a^", "a:b", "a?", "a*", "a[b", "a\\b",
        "HEAD", "é", "x" * 201, None, 5, ["main"],
    ])
    def test_unsafe_branch_names(self, name):
        from coop.services.system import is_safe_branch_name
        assert not is_safe_branch_name(name)

    def test_update_script_uses_the_same_rules(self):
        import update_script
        from coop.services.system import is_safe_branch_name
        for name in ("main", "claude/x", "--upload-pack=x", "a..b", "x.lock", "HEAD", "a b", "../x"):
            assert update_script.is_safe_branch_name(name) == is_safe_branch_name(name)

    def test_parse_branch_list(self):
        from coop.services.system import parse_branch_list
        branches = parse_branch_list(REFS)
        assert [b["name"] for b in branches] == ["claude/dev", "main"]
        assert branches[0] == {"name": "claude/dev", "commit": "bbb2222",
                               "date": "2026-09-02T10:00:00+00:00", "subject": "Dev work"}
        assert parse_branch_list("") == [] and parse_branch_list(None) == []

    def test_info_on_stable_branch(self, tmp_path):
        git = FakeGit()
        info = self.service(tmp_path, git).update_info()
        assert info["git"] and info["supported"]
        assert info["branch"] == "main" and info["channel"] == "stable" and not info["detached"]
        assert info["commit"] == "abc1234" and info["subject"] == "Stable release"
        assert info["commit_date"] == "2026-09-01T10:00:00+00:00"
        assert info["upstream"] == "origin/main" and info["behind"] == 2
        assert info["stable_branch"] == "main" and not info["checked"]
        assert "fetch" not in git.subcommands()

    def test_git_runs_in_the_code_checkout_not_the_data_root(self, tmp_path):
        s = SystemService(Paths(str(tmp_path / "data"), src_dir=str(tmp_path / "code" / "src")))
        assert s.repo_dir == str(tmp_path / "code")

    def test_info_check_fetches_the_upstream_branch(self, tmp_path):
        git = FakeGit(branch="claude/dev", upstream="origin/claude/dev")
        info = self.service(tmp_path, git, allow_system_changes=False).update_info(check=True)
        assert info["channel"] == "dev" and info["checked"] and not info["supported"]
        fetch = next(c for c in git.calls if c[3] == "fetch")
        assert fetch[-2:] == ["origin", "+refs/heads/claude/dev:refs/remotes/origin/claude/dev"]

    def test_info_check_failure_is_reported(self, tmp_path):
        git = FakeGit(fetch_error="fatal: unable to access")
        info = self.service(tmp_path, git).update_info(check=True)
        assert not info["checked"] and "unable to access" in info["error"]
        assert info["behind"] == 2   # still computed from the known refs

    def test_info_detached_head(self, tmp_path):
        info = self.service(tmp_path, FakeGit(branch="")).update_info(check=True)
        assert info["detached"] and info["branch"] is None and info["channel"] == "dev"
        assert info["commit"] == "abc1234" and info["upstream"] is None

    def test_info_without_git(self, tmp_path):
        info = self.service(tmp_path, FakeGit(missing=True)).update_info()
        assert not info["git"] and "git is not installed" in info["error"]

    def test_info_without_repository(self, tmp_path):
        # Real git on a directory that is not a checkout (tmp_path).
        info = SystemService(Paths(str(tmp_path))).update_info()
        assert not info["git"] and info["error"]
        assert SystemService(Paths(str(tmp_path))).list_branches()["branches"] == []

    def test_list_branches(self, tmp_path):
        git = FakeGit(branch="claude/dev")
        res = self.service(tmp_path, git).list_branches()
        assert res["refreshed"] and res["warning"] is None and res["error"] is None
        assert res["current"] == "claude/dev" and res["stable_branch"] == "main"
        assert [(b["name"], b["current"], b["stable"]) for b in res["branches"]] == [
            ("claude/dev", True, False), ("main", False, True)]
        fetch = next(c for c in git.calls if c[3] == "fetch")
        assert fetch[3:] == ["fetch", "--quiet", "--prune", "origin"]

    @pytest.mark.parametrize("error", ["fatal: could not resolve host", subprocess.TimeoutExpired("git", 20)])
    def test_list_branches_falls_back_when_fetch_fails(self, tmp_path, error):
        res = self.service(tmp_path, FakeGit(fetch_error=error)).list_branches()
        assert not res["refreshed"] and "Could not refresh" in res["warning"]
        assert [b["name"] for b in res["branches"]] == ["claude/dev", "main"]

    def test_list_branches_without_git(self, tmp_path):
        res = self.service(tmp_path, FakeGit(missing=True)).list_branches()
        assert res["branches"] == [] and "git is not installed" in res["error"]

    def test_update_current_branch(self, tmp_path, monkeypatch):
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        s = self.service(tmp_path, FakeGit())
        s.start_update()
        cmd = self.popen.call_args[0][0]
        assert "--branch" not in cmd and len(cmd) == 4
        self.spawned[0]()
        assert self.exits == [0]

    def test_update_to_branch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INVOCATION_ID", "x")
        monkeypatch.setattr(SystemService, "_systemd_service_name", staticmethod(lambda: "coop.service"))
        s = self.service(tmp_path, FakeGit())
        s.start_update(branch="claude/dev")
        cmd = self.popen.call_args[0][0]
        assert cmd[0] == sys.executable and cmd[1].endswith("update_script.py")
        assert cmd[2:4] == ["--branch", "claude/dev"]
        assert cmd[4].endswith("app.py") and cmd[5] == str(os.getpid()) and cmd[6] == "coop.service"
        assert len(self.spawned) == 1

    def test_update_to_stable(self, tmp_path):
        s = self.service(tmp_path, FakeGit(branch="claude/dev"))
        s.start_update(branch="main")
        assert self.popen.call_args[0][0][2:4] == ["--branch", "main"]

    @pytest.mark.parametrize("name", ["--upload-pack=x", "main;rm -rf /", "../x", "a..b", "", "-evil"])
    def test_update_rejects_unsafe_branch_before_calling_git(self, tmp_path, name):
        git = FakeGit()
        with pytest.raises(ValueError, match="Invalid branch"):
            self.service(tmp_path, git).start_update(branch=name)
        assert git.calls == [] and not self.popen.called and self.spawned == []

    def test_update_rejects_unknown_branch(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown branch"):
            self.service(tmp_path, FakeGit()).start_update(branch="does-not-exist")
        assert not self.popen.called

    def test_update_to_branch_refused_on_mock_host(self, tmp_path):
        git = FakeGit()
        with pytest.raises(RuntimeError, match="not supported"):
            self.service(tmp_path, git, allow_system_changes=False).start_update(branch="main")
        assert git.calls == [] and not self.popen.called

    def test_update_to_branch_without_git(self, tmp_path):
        with pytest.raises(RuntimeError, match="git is not installed"):
            self.service(tmp_path, FakeGit(missing=True)).start_update(branch="main")
        assert not self.popen.called

    def test_update_on_detached_head_needs_a_branch(self, tmp_path):
        s = self.service(tmp_path, FakeGit(branch=""))
        with pytest.raises(ValueError, match="detached"):
            s.start_update()
        s.start_update(branch="main")
        assert self.popen.called


# ═══════════════════════════ workers ═════════════════════════════════════════

class TestWorker:

    def test_runs_until_none(self):
        calls = []

        def step():
            calls.append(1)
            return None if len(calls) == 3 else 0
        w = Worker("w", step).start()
        w._thread.join(2)
        assert len(calls) == 3 and not w.alive

    def test_errors_back_off_and_call_handler(self):
        handled = []
        w = Worker("w", mock.Mock(side_effect=RuntimeError("x")), on_error=handled.append)
        assert w.run_once() == Worker.ERROR_BACKOFF_S
        assert w.errors == 1 and isinstance(handled[0], RuntimeError)

    def test_stop(self):
        w = Worker("w", lambda: 10).start()
        w.stop()
        assert not w.alive

    def test_once(self):
        fn = mock.Mock()
        assert once(fn)() is None
        fn.assert_called_once()


# ═══════════════════════════ logging ═════════════════════════════════════════

def test_log_buffer_and_configuration(tmp_path):
    buf = LogBuffer(maxlen=2)
    sunk = []
    buf.sink = sunk.append
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging(str(tmp_path / "log"), "INFO", buf)
        configure_logging(str(tmp_path / "log"), "DEBUG", buf)  # idempotent
        coop_handlers = [h for h in root.handlers if getattr(h, "_coop", False)]
        assert len(coop_handlers) == 3
        for i in range(3):
            logging.getLogger("x").info("msg %d", i)
        assert len(buf.lines) == 2 and buf.lines[-1].endswith("msg 2")
        assert len(sunk) == 3
        assert (tmp_path / "log" / "app.log").exists()
        buf.sink = mock.Mock(side_effect=RuntimeError)
        logging.getLogger("x").warning("sink failure must not raise")
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
