"""HTTP API contract (used by the single-page app and the service worker)."""

from __future__ import annotations

import base64
import json
from unittest import mock

import pytest
from werkzeug.security import generate_password_hash

from coop.config import AuthConfig, Mode


def basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


# ── pages ────────────────────────────────────────────────────────────────────

class TestPages:

    def test_index(self, app, client):
        app.vapid_public_key = "PUBKEY123"
        body = client.get("/").get_data(as_text=True)
        assert "Dinky Coop" in body and '"PUBKEY123"' in body and "app/app.js" in body
        assert "hardwareMock: true" in body and "abc1234" in body

    def test_index_on_real_hardware(self, app, client):
        app.hardware.gpio.is_mock = False
        assert "hardwareMock: false" in client.get("/").get_data(as_text=True)

    def test_debug_redirects_to_hardware_page(self, client):
        resp = client.get("/debug")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/#/hardware")

    def test_mock_panel_only_with_mock_hardware(self, app, client):
        resp = client.get("/mock")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/#/hardware")
        app.hardware.gpio.is_mock = False
        assert client.get("/mock").status_code == 403

    @pytest.mark.parametrize("path", ["app/app.js", "app/app.css", "fonts/figtree.woff2",
                                      "fonts/bricolage-grotesque.woff2", "js/chart.umd.js", "js/socket.io.js"])
    def test_shell_assets(self, client, path):
        assert client.get(f"/static/{path}").status_code == 200

    @pytest.mark.parametrize("url, mimetype", [("/favicon.ico", "image/png"),
                                                ("/manifest.json", "application/manifest+json"),
                                                ("/sw.js", "application/javascript"),
                                                ("/static/offline.html", "text/html")])
    def test_assets(self, client, url, mimetype):
        resp = client.get(url)
        assert resp.status_code == 200 and resp.mimetype == mimetype

    def test_version(self, client):
        assert client.get("/version").get_json() == {"version": "abc1234"}


# ── status ───────────────────────────────────────────────────────────────────

def test_status_endpoint(client):
    data = client.get("/api/status").get_json()
    assert data["state"] == "stopped" and "temp_in" in data


def test_settings_endpoint(app, client):
    app.config.update(mode=Mode.MANUAL, sunrise_offset=15, timer_open_time="06:30")
    data = client.get("/api/settings").get_json()
    assert data["mode"] == "manual" and data["sunrise_offset"] == 15 and data["timer_open_time"] == "06:30"
    assert data["hardware_mock"] is True and data["auth_enabled"] is False and data["reference_travel_ms"] is None
    assert set(data["location"]) >= {"city", "latitude", "longitude", "timezone"}


def test_settings_endpoint_hides_no_secrets(app, client):
    body = client.get("/api/settings").get_data(as_text=True)
    assert "password" not in body


def test_locations_endpoint(client):
    data = client.get("/api/locations").get_json()
    assert isinstance(data, list) and len(data) > 10
    assert any("berlin" in str(x).lower() for x in data)


def test_health(app, client):
    assert client.get("/api/health").get_json()["healthy"] is True
    app.start()
    app.workers[0].stop()
    resp = client.get("/api/health")
    assert resp.status_code == 503 and resp.get_json()["workers"]["door"] is False


def test_api_no_cache_headers(client):
    resp = client.get("/api/csv")
    assert resp.headers["Cache-Control"].startswith("no-store")
    assert "Pragma" not in client.get("/version").headers


# ── push ─────────────────────────────────────────────────────────────────────

def test_subscribe(app, client, tmp_path):
    assert client.post("/subscribe", json={"endpoint": "a"}).get_json() == {"message": "Subscription successful!"}
    client.post("/subscribe", json={"endpoint": "a", "keys": 1})
    assert json.loads((tmp_path / ".subscriptions.json").read_text()) == {"subscriptions": [{"endpoint": "a", "keys": 1}]}
    assert client.post("/subscribe", json={"x": 1}).status_code == 400
    assert client.post("/subscribe").status_code == 400


# ── log / csv viewers ────────────────────────────────────────────────────────

@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "log"
    d.mkdir()
    return d


class TestViewers:

    def test_logs(self, client, log_dir, tmp_path):
        (log_dir / "app.log").write_text("2025-01-01 10:00:00,000 - door - INFO - hi\n")
        (tmp_path / "app.log").write_text("outside\n")
        assert [f["name"] for f in client.get("/api/logs").get_json()] == ["app.log"]
        assert client.get("/api/logs/app.log").get_json()[0]["m"] == "hi"
        for url in ("/api/logs/../../app.log", "/api/logs/..%2Fapp.log", "/api/logs/sub/app.log"):
            assert client.get(url).get_json()[0]["m"] == "hi"
        assert client.get("/api/logs/other.log").status_code == 400
        assert client.get("/api/logs/app.log.2020-01-01").status_code == 404

    def test_csv(self, client, log_dir):
        (log_dir / "2025_01_01.csv").write_text('# time, temp_in, state\n10:00:00.1,"21.5°C",open\n')
        assert [f["name"] for f in client.get("/api/csv").get_json()] == ["2025_01_01.csv"]
        data = client.get("/api/csv/2025_01_01.csv").get_json()
        assert data["rows"] == [{"time": "10:00:00", "temp_in": 21.5, "state": "open"}]
        assert client.get("/api/csv/app.log").status_code == 400
        assert client.get("/api/csv/none.csv").status_code == 404


# ── GPIO config ──────────────────────────────────────────────────────────────

class TestGpioApi:

    def test_get(self, client):
        assert client.get("/api/gpio-config").get_json()["motor_in1"] == 17

    def test_post_saves_and_applies_live_flags(self, app, client, tmp_path):
        resp = client.post("/api/gpio-config", json={"motor_in1": "4", "invert_end_up": "true",
                                                     "reference_timeout": 90})
        assert resp.status_code == 200 and resp.get_json()["gpio"]["motor_in1"] == 4
        assert "motor_in1: 4" in (tmp_path / "config.yaml").read_text()
        assert app.driver.pins.invert_end_up is True and app.driver.pins.reference_timeout == 90
        assert app.driver.pins.motor_in1 == 17  # restart required for pins

    @pytest.mark.parametrize("payload", [{"motor_in1": 41}, {"endstop_up": "x"}, {"endstop_up": 17},
                                         {"invert_end_up": "maybe"}, {"reference_timeout": 4}])
    def test_post_validation(self, app, client, payload):
        resp = client.post("/api/gpio-config", json=payload)
        assert resp.status_code == 400 and resp.get_json()["error"]
        assert app.settings.gpio.motor_in1 == 17 and app.settings.gpio.endstop_up == 23

    @pytest.mark.parametrize("body", [None, "[1]", "nope"])
    def test_post_requires_object(self, client, body):
        assert client.post("/api/gpio-config", data=body, content_type="application/json").status_code == 400


# ── Wi-Fi ────────────────────────────────────────────────────────────────────

class TestWifiApi:

    def test_status_and_scan(self, client):
        assert client.get("/api/wifi-status").get_json() == {"ethernet_connected": False, "ap_mode_active": False,
                                                              "current_connection": {"ssid": "Mock-WiFi-1"}}
        assert client.get("/api/wifi-scan").get_json()[0]["ssid"] == "My-Home-WiFi"

    def test_get_masks_passwords(self, app, client):
        app.config.update(wifi=app.settings.wifi.merged({"password": "homepass"}))
        data = client.get("/api/wifi-config").get_json()
        assert data["password"] == "********" and data["ap_password"] == "********"

    def test_post_keeps_masked_passwords(self, app, client):
        app.config.update(wifi=app.settings.wifi.merged({"password": "homepass"}))
        resp = client.post("/api/wifi-config", json={"ssid": "Home", "password": "********",
                                                     "ap_password": "********", "timeout": 30, "ap_ip": "1.2.3.4"})
        assert resp.status_code == 200
        w = app.settings.wifi
        assert (w.ssid, w.password, w.ap_password, w.timeout, w.ap_ip) == ("Home", "homepass", "password", 30, "10.42.0.1")
        assert "homepass" not in resp.get_data(as_text=True)

    @pytest.mark.parametrize("payload", [{"ap_password": "short"}, {"timeout": None}, {"timeout": "x"},
                                         {"ap_ssid": ""}])
    def test_post_validation(self, client, payload):
        assert client.post("/api/wifi-config", json=payload).status_code == 400

    def test_connect(self, app, client):
        assert client.post("/api/wifi-connect", json={"ssid": "Home", "password": "pw"}).get_json() == {"success": True}
        assert app.wifi.calls == [("connect", "Home", "pw")]

    def test_connect_failure_falls_back_to_ap(self, app, client):
        app.wifi.connect_result = False
        assert client.post("/api/wifi-connect", json={"ssid": "Home"}).get_json() == {"success": False}
        assert app.wifi.calls[-1] == ("start_ap", "DINKY-COOP", "password")
        assert app.clock.sleeps == [app.WIFI_FALLBACK_DELAY_S]

    @pytest.mark.parametrize("password", [None, "", "********"])
    def test_connect_to_saved_network_uses_saved_password(self, app, client, password):
        app.config.update(wifi=app.settings.wifi.merged({"ssid": "Home", "password": "secret"}))
        client.post("/api/wifi-connect", json={"ssid": "Home", "password": password})
        client.post("/api/wifi-connect", json={"ssid": "Other", "password": password})
        assert app.wifi.calls == [("connect", "Home", "secret"), ("connect", "Other", None)]

    @pytest.mark.parametrize("payload", [None, {"password": "x"}])
    def test_connect_validation(self, client, payload):
        assert client.post("/api/wifi-connect", json=payload).status_code == 400

    def test_ap(self, app, client):
        assert client.post("/api/wifi-ap").get_json() == {"success": True}
        assert app.wifi.calls == [("start_ap", "DINKY-COOP", "password")]


# ── system ───────────────────────────────────────────────────────────────────

class TestSystemApi:

    def test_time(self, app, client):
        assert client.post("/api/system/time", json={"time": "2025-01-01 10:00:00"}).status_code == 200
        assert app.system.actions == [("set_time", "2025-01-01 10:00:00")]
        assert client.post("/api/system/time", json={}).status_code == 400
        assert client.post("/api/system/time", json={"time": "2025/01/01"}).status_code == 400
        app.system.fail_time = RuntimeError("denied")
        resp = client.post("/api/system/time", json={"time": "2025-01-01 10:00:00"})
        assert resp.status_code == 500 and "denied" in resp.get_json()["error"]

    def test_restart(self, app, client):
        assert client.post("/api/restart").get_json() == {"status": "rebooting device"}
        app.system.reboot_error = RuntimeError("not supported")
        assert client.post("/api/restart").status_code == 400

    def test_update(self, app, client):
        assert client.post("/update").get_json() == {"status": "updating"}
        assert app.system.actions == [("update",)]

    def test_update_refused(self, app, client):
        app.system.update_error = RuntimeError("Updating is not supported on this host (mock hardware)")
        resp = client.post("/update")
        assert resp.status_code == 400 and "not supported" in resp.get_json()["error"]
        resp = client.post("/update", json={"branch": "main"})
        assert resp.status_code == 400 and "not supported" in resp.get_json()["error"]

    def test_update_to_branch(self, app, client):
        resp = client.post("/update", json={"branch": "claude/dev-feature"})
        assert resp.get_json() == {"status": "updating", "branch": "claude/dev-feature"}
        client.post("/update", json={"branch": "main"})
        client.post("/update", json={})  # empty body -> current branch
        assert app.system.actions == [("update", "claude/dev-feature"), ("update", "main"), ("update",)]

    @pytest.mark.parametrize("body", [{"branch": ""}, {"branch": 5}, {"branch": ["main"]}, {"branch": "nope"}])
    def test_update_to_bad_branch(self, app, client, body):
        resp = client.post("/update", json=body)
        assert resp.status_code == 400 and resp.get_json()["error"]
        assert app.system.actions == []

    def test_update_invalid_branch_name_from_real_service(self, app, client):
        from coop.services.system import SystemService
        app.system = SystemService(app.paths, run=mock.Mock(side_effect=AssertionError("git must not run")),
                                   popen=mock.Mock(side_effect=AssertionError("must not spawn")))
        for name in ("--upload-pack=touch /tmp/x", "main;rm -rf /", "../x", "a..b"):
            resp = client.post("/update", json={"branch": name})
            assert resp.status_code == 400 and resp.get_json()["error"] == "Invalid branch name"

    def test_update_info(self, app, client):
        info = client.get("/api/update/info").get_json()
        assert info["branch"] == "main" and info["channel"] == "stable" and info["commit"] == "abc1234"
        assert client.get("/api/update/info?check=1").get_json()["checked"] is True
        assert app.system.actions == [("update_info", False), ("update_info", True)]
        assert "no-store" in client.get("/api/update/info").headers["Cache-Control"]

    def test_update_branches(self, client):
        res = client.get("/api/update/branches").get_json()
        assert [b["name"] for b in res["branches"]] == ["claude/dev-feature", "main"]
        assert res["stable_branch"] == "main" and res["current"] == "main"

    def test_update_routes_on_host_without_git(self, app, client):
        from coop.services.system import SystemService
        app.system = SystemService(app.paths, run=mock.Mock(side_effect=FileNotFoundError("git")))
        info = client.get("/api/update/info").get_json()
        assert info["git"] is False and "git is not installed" in info["error"]
        res = client.get("/api/update/branches")
        assert res.status_code == 200 and res.get_json()["branches"] == []


# ── captive portal ───────────────────────────────────────────────────────────

class TestCaptivePortal:

    def test_inactive_outside_ap_mode(self, client):
        assert client.get("/version", headers={"Host": "example.com"}).status_code == 200

    @pytest.mark.parametrize("host", ["connectivitycheck.gstatic.com", "captive.apple.com:80"])
    def test_redirects_foreign_hosts(self, app, client, host):
        app.wifi.ap_mode = True
        resp = client.get("/", headers={"Host": host})
        assert resp.status_code == 302 and resp.headers["Location"] == "http://10.42.0.1/"

    @pytest.mark.parametrize("host", ["10.42.0.1", "10.42.0.1:5000", "localhost", "dinky-coop", "coop.local",
                                      "[fe80::1]:5000"])
    def test_first_party_hosts(self, app, client, host):
        app.wifi.ap_mode = True
        assert client.get("/version", headers={"Host": host}).status_code == 200

    def test_configured_hosts_and_api(self, app, client):
        app.wifi.ap_mode = True
        app.config.update(wifi=app.settings.wifi.merged({"ap_allowed_hosts": ["MyCoop.lan"]}))
        assert client.get("/version", headers={"Host": "mycoop.lan"}).status_code == 200
        assert client.get("/api/status", headers={"Host": "evil.com"}).status_code == 200

    @pytest.mark.parametrize("path", ["/generate_204", "/gen_204"])
    def test_android_probes(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"] == "http://10.42.0.1/"

    @pytest.mark.parametrize("path", ["/hotspot-detect.html", "/success.html"])
    def test_apple_probes(self, client, path):
        assert "url=http://10.42.0.1/" in client.get(path).get_data(as_text=True)


# ── authentication ───────────────────────────────────────────────────────────

class TestAuth:

    @pytest.fixture
    def secured(self, app):
        app.config.update(auth=AuthConfig("admin", generate_password_hash("s3cret-pass")))
        return app

    def test_disabled_by_default(self, client):
        assert client.get("/").status_code == 200

    def test_challenge(self, secured, client):
        resp = client.get("/api/status")
        assert resp.status_code == 401 and resp.headers["WWW-Authenticate"].startswith("Basic")
        assert client.get("/", headers=basic("admin", "wrong")).status_code == 401
        assert client.get("/", headers=basic("root", "s3cret-pass")).status_code == 401

    def test_valid_credentials(self, secured, client):
        assert client.get("/api/status", headers=basic("admin", "s3cret-pass")).status_code == 200

    def test_update_routes_require_auth(self, secured, client):
        for path in ("/api/update/info", "/api/update/branches"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=basic("admin", "s3cret-pass")).status_code == 200
        assert client.post("/update", json={"branch": "main"}).status_code == 401
        assert secured.system.actions == [("update_info", False)]

    @pytest.mark.parametrize("path", ["/static/offline.html", "/manifest.json", "/sw.js", "/generate_204",
                                      "/hotspot-detect.html", "/favicon.ico"])
    def test_public_paths(self, secured, client, path):
        assert client.get(path).status_code in (200, 302)

    def test_socket_requires_auth(self, secured, web):
        flask_app, socketio = web
        assert not socketio.test_client(flask_app).is_connected()
        sc = socketio.test_client(flask_app, headers=basic("admin", "s3cret-pass"))
        assert sc.is_connected()
        sc.disconnect()
