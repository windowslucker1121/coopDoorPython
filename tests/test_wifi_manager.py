"""Tests for :class:`wifi_manager.WifiManager` (NetworkManager / ``nmcli``).

``subprocess`` is replaced by fakes; nothing is executed on the host.
"""

import subprocess
from unittest import mock

import pytest

import wifi_manager as wm
from protected_dict import protected_dict as gv
from wifi_manager import WifiManager


@pytest.fixture
def mgr():
    m = WifiManager()
    m.is_windows = False
    return m


@pytest.fixture
def check_output(monkeypatch):
    fake = mock.Mock()
    monkeypatch.setattr(wm.subprocess, "check_output", fake)
    return fake


@pytest.fixture
def run(monkeypatch):
    fake = mock.Mock()
    monkeypatch.setattr(wm.subprocess, "run", fake)
    return fake


# ── Windows mock mode ────────────────────────────────────────────────────────

def test_windows_mode_returns_canned_values(check_output, run):
    m = WifiManager()
    m.is_windows = True
    assert [n["ssid"] for n in m.scan_networks()] == ["My-Home-WiFi", "Guest-Network"]
    assert m.connect("x", "y") is True
    assert m.start_ap("ap", "pw") is True
    assert m.is_ap_mode_active() is False
    assert m.is_ethernet_connected() is False
    assert m.get_current_connection() == {"ssid": "Mock-WiFi-1"}
    check_output.assert_not_called()
    run.assert_not_called()


# ── scan_networks ────────────────────────────────────────────────────────────

def test_scan_parses_dedupes_and_sorts(mgr, check_output):
    check_output.return_value = (
        b"Home:40:WPA2\n"
        b"Office:90:WPA2 WPA3\n"
        b"Home:70:WPA2\n"      # duplicate SSID → first entry kept
        b":55:WPA2\n"           # hidden network → skipped
        b"Weird--Net:80:\n"     # '--' in SSID → skipped
        b"Cafe:n/a:\n"          # non-numeric signal → 0
        b"\n"
    )
    nets = mgr.scan_networks()
    assert nets == [
        {"ssid": "Office", "signal": 90, "security": "WPA2 WPA3"},
        {"ssid": "Home", "signal": 40, "security": "WPA2"},
        {"ssid": "Cafe", "signal": 0, "security": ""},
    ]
    assert check_output.call_args[0][0] == ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]


def test_scan_error_returns_empty_list(mgr, check_output):
    check_output.side_effect = FileNotFoundError("nmcli")
    assert mgr.scan_networks() == []


# ── connect ──────────────────────────────────────────────────────────────────

def test_connect_with_password(mgr, check_output):
    assert mgr.connect("Home", "secret", timeout=12) is True
    args, kwargs = check_output.call_args
    assert args[0] == ["nmcli", "dev", "wifi", "connect", "Home", "password", "secret"]
    assert kwargs["timeout"] == 12


def test_connect_open_network(mgr, check_output):
    mgr.connect("Open", "")
    assert check_output.call_args[0][0] == ["nmcli", "dev", "wifi", "connect", "Open"]


@pytest.mark.parametrize("exc", [
    subprocess.TimeoutExpired(cmd="nmcli", timeout=1),
    subprocess.CalledProcessError(1, "nmcli", output=b"no network"),
    subprocess.CalledProcessError(1, "nmcli", output=None),
    OSError("boom"),
])
def test_connect_failures_return_false(mgr, check_output, exc):
    check_output.side_effect = exc
    assert mgr.connect("Home", "pw") is False


# ── start_ap / captive portal ────────────────────────────────────────────────

def test_start_ap_sets_up_captive_portal_and_hotspot(mgr, check_output, run, monkeypatch):
    monkeypatch.setattr(wm.os.path, "exists", lambda p: True)
    gv.instance().set_value("wifi", {"ap_ip": "192.168.4.1"})

    assert mgr.start_ap("COOP", "pw123456") is True

    hotspot_cmd = check_output.call_args[0][0]
    assert hotspot_cmd == ["nmcli", "dev", "wifi", "hotspot", "ifname", "wlan0",
                           "ssid", "COOP", "password", "pw123456"]
    run_cmds = [c[0][0] for c in run.call_args_list]
    assert any("address=/#/192.168.4.1" in " ".join(cmd) for cmd in run_cmds)
    iptables = [cmd for cmd in run_cmds if "iptables" in cmd]
    assert len(iptables) == 2
    assert "-D" in iptables[0] and "-I" in iptables[1]
    assert iptables[1][-2:] == ["--to-port", "5000"]


def test_captive_portal_defaults_ap_ip(mgr, check_output, run, monkeypatch):
    monkeypatch.setattr(wm.os.path, "exists", lambda p: True)
    mgr.start_ap("COOP", "pw")
    run_cmds = [" ".join(c[0][0]) for c in run.call_args_list]
    assert any("address=/#/10.42.0.1" in cmd for cmd in run_cmds)


def test_captive_portal_skips_dnsmasq_without_networkmanager(mgr, check_output, run, monkeypatch):
    monkeypatch.setattr(wm.os.path, "exists", lambda p: False)
    mgr.start_ap("COOP", "pw")
    run_cmds = [c[0][0] for c in run.call_args_list]
    assert all("iptables" in cmd for cmd in run_cmds)


@pytest.mark.parametrize("exc", [
    subprocess.CalledProcessError(1, "nmcli", output=b"fail"),
    OSError("boom"),
])
def test_start_ap_failures_return_false(mgr, check_output, run, exc):
    check_output.side_effect = exc
    assert mgr.start_ap("COOP", "pw") is False


# ── is_ap_mode_active ────────────────────────────────────────────────────────

def test_ap_mode_detected_and_cached(mgr, check_output, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(wm.time, "time", lambda: now["t"])
    check_output.return_value = b"Hotspot:802-11-wireless:wlan0\n"

    assert mgr.is_ap_mode_active() is True
    check_output.return_value = b""
    now["t"] += 29
    assert mgr.is_ap_mode_active() is True  # cached for 30 s
    assert check_output.call_count == 1
    now["t"] += 2
    assert mgr.is_ap_mode_active() is False
    assert check_output.call_count == 2


def test_ap_mode_requires_wlan0(mgr, check_output):
    check_output.return_value = b"Hotspot:802-11-wireless:wlan1\n"
    assert mgr.is_ap_mode_active() is False


def test_ap_mode_regular_wifi_is_not_ap(mgr, check_output):
    check_output.return_value = b"HomeNet:802-11-wireless:wlan0\n"
    assert mgr.is_ap_mode_active() is False


def test_ap_mode_error_returns_false(mgr, check_output):
    check_output.side_effect = OSError
    assert mgr.is_ap_mode_active() is False


# ── ethernet / current connection ────────────────────────────────────────────

def test_ethernet_connected(mgr, check_output):
    check_output.return_value = b"wifi:connected\nethernet:connected\n"
    assert mgr.is_ethernet_connected() is True
    check_output.return_value = b"ethernet:unavailable\n"
    assert mgr.is_ethernet_connected() is False
    check_output.side_effect = OSError
    assert mgr.is_ethernet_connected() is False


def test_current_connection(mgr, check_output):
    check_output.return_value = b"Wired:802-3-ethernet:eth0\nHomeNet:802-11-wireless:wlan0\n"
    assert mgr.get_current_connection() == {"ssid": "HomeNet"}


def test_current_connection_none(mgr, check_output):
    check_output.return_value = b"Wired:802-3-ethernet:eth0\n"
    assert mgr.get_current_connection() is None
    check_output.side_effect = OSError
    assert mgr.get_current_connection() is None
