"""HTTP route tests for ``src/app.py`` using Flask's test client.

Anything that would reboot the machine, change the system clock, relaunch
the process or touch NetworkManager is replaced by a fake.
"""

import json
import os
from unittest import mock

import pytest

from protected_dict import protected_dict as gv


# ── pages & static files ─────────────────────────────────────────────────────

class TestPages:

    def test_index_renders_dashboard(self, app_env, client):
        app_env.load_config()
        gv.instance().set_value("vapid_public_key", "PUBKEY123")
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Coop Door" in body
        assert "PUBKEY123" in body

    def test_debug_page(self, client):
        assert client.get("/debug").status_code == 200

    def test_mock_page_forbidden_off_windows(self, client):
        resp = client.get("/mock")
        assert resp.status_code == 403

    def test_favicon(self, client):
        resp = client.get("/favicon.ico")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"

    def test_manifest(self, client):
        resp = client.get("/manifest.json")
        assert resp.status_code == 200
        assert resp.mimetype == "application/manifest+json"
        assert json.loads(resp.get_data())["name"] == "Coop Door"

    def test_service_worker(self, client):
        resp = client.get("/sw.js")
        assert resp.status_code == 200
        assert resp.mimetype == "application/javascript"

    def test_static_file(self, client):
        assert client.get("/static/offline.html").status_code == 200

    def test_is_number_filter(self, app_env):
        assert app_env.is_number("1.5") is True
        assert app_env.is_number(3) is True
        assert app_env.is_number("abc") is False
        assert app_env.is_number(None) is False
        assert app_env.app.jinja_env.filters["is_number"] is app_env.is_number


# ── version ──────────────────────────────────────────────────────────────────

def test_version_unknown_without_file(client):
    assert client.get("/version").get_json() == {"version": "unknown"}


def test_version_reads_version_txt_from_root_path(client, tmp_path):
    (tmp_path / "version.txt").write_text("abc1234\n")
    assert client.get("/version").get_json() == {"version": "abc1234"}


# ── push subscription ────────────────────────────────────────────────────────

def test_subscribe_creates_and_appends(client, tmp_path):
    resp = client.post("/subscribe", json={"endpoint": "a"})
    assert resp.get_json() == {"message": "Subscription successful!"}
    client.post("/subscribe", json={"endpoint": "b"})
    saved = json.loads((tmp_path / ".subscriptions.json").read_text())
    assert saved == {"subscriptions": [{"endpoint": "a"}, {"endpoint": "b"}]}


def test_subscribe_replaces_same_endpoint(client, tmp_path):
    client.post("/subscribe", json={"endpoint": "a", "keys": {"v": 1}})
    client.post("/subscribe", json={"endpoint": "a", "keys": {"v": 2}})
    saved = json.loads((tmp_path / ".subscriptions.json").read_text())
    assert saved["subscriptions"] == [{"endpoint": "a", "keys": {"v": 2}}]


# ── captive portal ───────────────────────────────────────────────────────────

class TestCaptivePortal:

    def test_not_in_ap_mode_passes_through(self, client):
        resp = client.get("/version", headers={"Host": "example.com"})
        assert resp.status_code == 200

    @pytest.mark.parametrize("host", ["connectivitycheck.gstatic.com", "captive.apple.com:80"])
    def test_foreign_host_redirects_to_portal(self, client, fake_wifi, host):
        fake_wifi.ap_mode = True
        resp = client.get("/version", headers={"Host": host})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "http://10.42.0.1/"

    @pytest.mark.parametrize("host", ["10.42.0.1", "10.42.0.1:5000", "localhost", "dinky-coop",
                                      "dinkycoop", "coop.local"])
    def test_first_party_hosts_pass(self, client, fake_wifi, host):
        fake_wifi.ap_mode = True
        assert client.get("/version", headers={"Host": host}).status_code == 200

    def test_configured_allowed_host_passes(self, client, fake_wifi):
        fake_wifi.ap_mode = True
        gv.instance().set_value("wifi", {"ap_allowed_hosts": ["mycoop.lan"]})
        assert client.get("/version", headers={"Host": "mycoop.lan"}).status_code == 200

    def test_api_and_static_are_never_redirected(self, client, fake_wifi):
        fake_wifi.ap_mode = True
        assert client.get("/api/wifi-config", headers={"Host": "evil.com"}).status_code == 200
        assert client.get("/static/offline.html", headers={"Host": "evil.com"}).status_code == 200

    def test_custom_ap_ip(self, client, fake_wifi):
        fake_wifi.ap_mode = True
        gv.instance().set_value("wifi", {"ap_ip": "192.168.4.1"})
        resp = client.get("/", headers={"Host": "neverssl.com"})
        assert resp.headers["Location"] == "http://192.168.4.1/"

    @pytest.mark.parametrize("path", ["/generate_204", "/gen_204"])
    def test_android_probes_redirect(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "http://10.42.0.1/"

    @pytest.mark.parametrize("path", ["/hotspot-detect.html", "/success.html"])
    def test_apple_probes_return_meta_refresh(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200
        assert 'url=http://10.42.0.1/' in resp.get_data(as_text=True)

    def test_allowed_hosts_include_defaults(self, app_env):
        hosts = app_env.get_allowed_hosts()
        for h in ("localhost", "dinky-coop", "dinkycoop", os.uname().nodename):
            assert h in hosts


# ── log viewer API ───────────────────────────────────────────────────────────

@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "log"
    d.mkdir()
    return d


class TestLogApi:

    def test_no_log_dir(self, client):
        assert client.get("/api/logs").get_json() == []

    def test_lists_only_app_logs_newest_first(self, client, log_dir):
        names = ["app.log", "app.log.2025-01-02", "app_20240101_120000.log",
                 "app.log.bad", "other.log", "2025_01_01.csv"]
        for i, name in enumerate(names):
            p = log_dir / name
            p.write_text("x" * (i + 1))
            os.utime(p, (1000 + i, 1000 + i))
        files = client.get("/api/logs").get_json()
        assert [f["name"] for f in files] == ["app_20240101_120000.log", "app.log.2025-01-02", "app.log"]
        assert files[-1]["size"] == 1

    def test_no_cache_headers_on_api(self, client):
        resp = client.get("/api/logs")
        assert resp.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
        assert resp.headers["Pragma"] == "no-cache"
        assert resp.headers["Expires"] == "0"

    def test_no_cache_headers_only_on_api(self, client):
        assert "Pragma" not in client.get("/version").headers

    def test_parses_log_lines(self, client, log_dir):
        (log_dir / "app.log").write_text(
            "2025-01-01 10:00:00,000 - door - info - GPIO - Door opening\n"
            "\n"
            "Traceback (most recent call last):\n"
        )
        lines = client.get("/api/logs/app.log").get_json()
        assert lines == [
            {"t": "2025-01-01 10:00:00,000", "lg": "door", "lv": "INFO", "m": "GPIO - Door opening"},
            {"t": "", "lg": "", "lv": "RAW", "m": "Traceback (most recent call last):"},
        ]

    @pytest.mark.parametrize("name", ["other.log", "app.log.bad", "passwd"])
    def test_rejects_invalid_names(self, client, log_dir, name):
        assert client.get(f"/api/logs/{name}").status_code == 400

    @pytest.mark.parametrize("url", ["/api/logs/../../app.log", "/api/logs/..%2F..%2Fapp.log",
                                     "/api/logs/sub/app.log"])
    def test_path_traversal_is_neutralised(self, client, log_dir, tmp_path, url):
        (log_dir / "app.log").write_text("inside\n")
        (tmp_path / "app.log").write_text("outside\n")
        resp = client.get(url)
        # basename() strips the directories → only the file inside log/ is served
        assert resp.status_code == 200
        assert resp.get_json()[0]["m"] == "inside"

    def test_missing_file(self, client, log_dir):
        assert client.get("/api/logs/app.log").status_code == 404


# ── CSV viewer API ───────────────────────────────────────────────────────────

CSV_HEADER = "# time, os_timestamp, temp_in, hum_in, state, auto_mode, uptime\n"


class TestCsvApi:

    def test_no_log_dir(self, client):
        assert client.get("/api/csv").get_json() == []

    def test_lists_csv_files_by_name_desc(self, client, log_dir):
        for name in ["2025_01_01.csv", "2025_01_03.csv", "app.log", "2025_01_02.csv"]:
            (log_dir / name).write_text("x")
        names = [f["name"] for f in client.get("/api/csv").get_json()]
        assert names == ["2025_01_03.csv", "2025_01_02.csv", "2025_01_01.csv"]

    def test_parses_and_filters_columns(self, client, log_dir):
        (log_dir / "d.csv").write_text(
            CSV_HEADER
            + "10:00:00.123, 1700000000, 21.5°C, 40.0%, open, True, 1 day(s)\n"
            + "10:00:05.000, 1700000005, , 41.0%, closing, True, 1 day(s)\n"
            + "short, row\n"
            + "\n"
        )
        data = client.get("/api/csv/d.csv").get_json()
        assert data["count"] == data["total"] == 2
        assert data["rows"][0] == {"time": "10:00:00", "temp_in": 21.5, "hum_in": 40.0,
                                   "state": "open", "auto_mode": "True"}
        assert data["rows"][1]["temp_in"] is None

    def test_downsamples_to_600_rows(self, client, log_dir):
        rows = "".join(f"t{i}, 0, {i}°C, 1%, open, True, u\n" for i in range(1500))
        (log_dir / "big.csv").write_text(CSV_HEADER + rows)
        data = client.get("/api/csv/big.csv").get_json()
        assert data["total"] == 1500
        assert data["count"] == 600
        assert data["rows"][0]["time"] == "t0"
        assert data["rows"][1]["time"] == "t2"  # step = 2.5

    def test_invalid_extension(self, client):
        assert client.get("/api/csv/app.log").status_code == 400

    def test_missing_file(self, client, log_dir):
        assert client.get("/api/csv/nope.csv").status_code == 404


# ── GPIO config API ──────────────────────────────────────────────────────────

class TestGpioConfigApi:

    def test_get_defaults_when_unset(self, app_env, client):
        assert client.get("/api/gpio-config").get_json() == app_env.GPIO_DEFAULTS

    def test_post_updates_persists_and_applies_live(self, app_env, client, tmp_path):
        import door as door_module
        app_env.load_config()
        resp = client.post("/api/gpio-config", json={
            "motor_in1": "4", "invert_end_up": 1, "invert_end_down": 0, "reference_timeout": 90,
        })
        assert resp.status_code == 200
        gpio = resp.get_json()["gpio"]
        assert gpio["motor_in1"] == 4 and gpio["invert_end_up"] is True
        assert gv.instance().get_value("gpio")["motor_in1"] == 4
        assert "motor_in1: 4" in (tmp_path / "config.yaml").read_text()
        assert door_module.invert_end_up is True
        assert door_module.invert_end_down is False
        assert door_module.referenceSequenceTimeout == 90
        # Pin numbers are NOT applied live (restart required)
        assert door_module.in1 == 17

    @pytest.mark.parametrize("payload, fragment", [
        ({"motor_in1": 41}, "'motor_in1' must be 0–40"),
        ({"motor_in1": -1}, "'motor_in1' must be 0–40"),
        ({"endstop_up": "abc"}, "'endstop_up' must be an integer"),
        ({"reference_timeout": 4}, "'reference_timeout' must be 5–600 seconds"),
        ({"reference_timeout": 601}, "'reference_timeout' must be 5–600 seconds"),
        ({"reference_timeout": "x"}, "'reference_timeout' must be an integer"),
    ])
    def test_post_validation(self, client, payload, fragment):
        resp = client.post("/api/gpio-config", json=payload)
        assert resp.status_code == 400
        assert fragment in resp.get_json()["error"]
        assert gv.instance().get_value("gpio") is None  # nothing stored

    def test_post_collects_all_errors(self, client):
        resp = client.post("/api/gpio-config", json={"motor_in1": 99, "motor_in2": "x"})
        assert resp.get_json()["error"].count(";") == 1

    @pytest.mark.parametrize("body", [None, "[1, 2]"])
    def test_post_requires_json_object(self, client, body):
        resp = client.post("/api/gpio-config", data=body, content_type="application/json")
        assert resp.status_code == 400


# ── WiFi API ─────────────────────────────────────────────────────────────────

class TestWifiApi:

    def test_status(self, client, fake_wifi):
        fake_wifi.ethernet = True
        assert client.get("/api/wifi-status").get_json() == {
            "ethernet_connected": True, "ap_mode_active": False,
            "current_connection": {"ssid": "Fake-WiFi"},
        }

    def test_scan(self, client, fake_wifi):
        assert client.get("/api/wifi-scan").get_json() == fake_wifi.networks

    def test_ap_uses_configured_credentials(self, client, fake_wifi):
        gv.instance().set_value("wifi", {"ap_ssid": "MY-AP", "ap_password": "pw"})
        assert client.post("/api/wifi-ap").get_json() == {"success": True}
        assert fake_wifi.start_ap_calls == [("MY-AP", "pw")]

    def test_get_config_defaults(self, app_env, client):
        assert client.get("/api/wifi-config").get_json() == app_env.WIFI_DEFAULTS

    def test_post_config(self, app_env, client, tmp_path):
        app_env.load_config()
        resp = client.post("/api/wifi-config", json={
            "ssid": "Home", "password": 1234, "ap_ssid": "AP", "ap_password": "x" * 8, "timeout": "30",
            "ap_ip": "1.2.3.4",  # not accepted via this endpoint
        })
        assert resp.status_code == 200
        wifi = gv.instance().get_value("wifi")
        assert wifi["ssid"] == "Home" and wifi["password"] == "1234" and wifi["timeout"] == 30
        assert wifi["ap_ip"] == "10.42.0.1"
        assert "ssid: Home" in (tmp_path / "config.yaml").read_text()

    def test_post_config_bad_timeout(self, client):
        resp = client.post("/api/wifi-config", json={"timeout": "soon"})
        assert resp.status_code == 400

    def test_post_config_requires_json(self, client):
        assert client.post("/api/wifi-config").status_code == 400

    def test_connect_success(self, client, fake_wifi):
        resp = client.post("/api/wifi-connect", json={"ssid": "Home", "password": "pw"})
        assert resp.get_json() == {"success": True}
        assert fake_wifi.connect_calls == [("Home", "pw", 30)]
        assert fake_wifi.start_ap_calls == []

    def test_connect_failure_falls_back_to_ap(self, app_env, client, fake_wifi, monkeypatch):
        monkeypatch.setattr(app_env.time, "sleep", lambda s: None)
        fake_wifi.connect_result = False
        resp = client.post("/api/wifi-connect", json={"ssid": "Home"})
        assert resp.get_json() == {"success": False}
        assert fake_wifi.start_ap_calls == [("DINKY-COOP", "password")]

    def test_connect_requires_ssid(self, client):
        assert client.post("/api/wifi-connect", json={"password": "x"}).status_code == 400

    def test_connect_without_body_is_rejected(self, client, fake_wifi):
        assert client.post("/api/wifi-connect").status_code == 400
        assert fake_wifi.connect_calls == []


# ── system time ──────────────────────────────────────────────────────────────

class TestSystemTime:

    def test_requires_time(self, client):
        assert client.post("/api/system/time", json={}).status_code == 400

    def test_rejects_bad_format(self, app_env, client, monkeypatch):
        run = mock.Mock()
        monkeypatch.setattr(app_env.subprocess, "run", run)
        resp = client.post("/api/system/time", json={"time": "2025/01/01 10:00"})
        assert resp.status_code == 400
        run.assert_not_called()

    def test_sets_time_with_sudo_date(self, app_env, client, monkeypatch):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stderr=""))
        monkeypatch.setattr(app_env.subprocess, "run", run)
        resp = client.post("/api/system/time", json={"time": "2025-01-01 10:00:00"})
        assert resp.status_code == 200
        assert run.call_args[0][0] == ["sudo", "date", "-s", "2025-01-01 10:00:00"]

    def test_date_failure(self, app_env, client, monkeypatch):
        run = mock.Mock(return_value=mock.Mock(returncode=1, stderr="denied"))
        monkeypatch.setattr(app_env.subprocess, "run", run)
        resp = client.post("/api/system/time", json={"time": "2025-01-01 10:00:00"})
        assert resp.status_code == 500
        assert "denied" in resp.get_json()["error"]

    def test_unexpected_exception(self, app_env, client, monkeypatch):
        monkeypatch.setattr(app_env.subprocess, "run", mock.Mock(side_effect=OSError("no sudo")))
        resp = client.post("/api/system/time", json={"time": "2025-01-01 10:00:00"})
        assert resp.status_code == 500


# ── reboot / update ──────────────────────────────────────────────────────────

def test_restart_schedules_reboot_thread(app_env, client, monkeypatch):
    thread_cls = mock.Mock()
    monkeypatch.setattr(app_env, "Thread", thread_cls)
    resp = client.post("/api/restart")
    assert resp.get_json() == {"status": "rebooting device"}
    assert thread_cls.call_args.kwargs["daemon"] is True
    thread_cls.return_value.start.assert_called_once()

    # Run the scheduled function with the side effects faked
    popen = mock.Mock()
    monkeypatch.setattr(app_env.subprocess, "Popen", popen)
    monkeypatch.setattr(app_env.time, "sleep", lambda s: None)
    monkeypatch.setattr(app_env.os, "sync", lambda: None)
    thread_cls.call_args.kwargs["target"]()
    assert popen.call_args[0][0] == ["sudo", "systemctl", "reboot"]


def test_update_launches_helper_and_schedules_exit(app_env, client, monkeypatch):
    import subprocess
    import threading
    import sys
    popen = mock.Mock()
    thread_cls = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(threading, "Thread", thread_cls)
    monkeypatch.delenv("INVOCATION_ID", raising=False)

    resp = client.post("/update")

    assert resp.get_json() == {"status": "updating"}
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    cmd = popen.call_args[0][0]
    assert cmd[0] == sys.executable
    assert cmd[1].endswith(os.path.join("src", "update_script.py"))
    assert cmd[2].endswith(os.path.join("src", "app.py"))
    assert cmd[3] == str(os.getpid())
    assert len(cmd) == 4  # no systemd service name outside systemd
    thread_cls.return_value.start.assert_called_once()

    exit_mock = mock.Mock()
    monkeypatch.setattr(os, "_exit", exit_mock)
    monkeypatch.setattr("time.sleep", lambda s: None)
    thread_cls.call_args.kwargs["target"]()
    exit_mock.assert_called_once_with(0)
