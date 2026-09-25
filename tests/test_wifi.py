"""WifiManager (nmcli) and the boot-time Wi-Fi watchdog."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from coop.config import WifiConfig
from coop.services.wifi import WifiManager, run_wifi_watchdog, split_nmcli_terse


class FakeNmcli:
    """Answers nmcli invocations; ``modes`` maps connection → wireless mode."""

    def __init__(self, active="", modes=None, scan="", devices="", fail=None):
        self.active, self.modes, self.scan, self.devices, self.fail = active, modes or {}, scan, devices, fail
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.fail:
            raise self.fail
        args = cmd[1:]
        if args[:2] == ["-g", "802-11-wireless.mode"]:
            if args[-1] not in self.modes:
                raise subprocess.CalledProcessError(10, cmd)
            return (self.modes[args[-1]] + "\n").encode()
        if "list" in args:
            return self.scan.encode()
        if args[-1] == "--active":
            return self.active.encode()
        if args[-1] == "device":
            return self.devices.encode()
        return b""


def manager(nmcli, **kw):
    return WifiManager(run=nmcli, shell=kw.pop("shell", mock.Mock()), **kw)


def test_split_nmcli_terse():
    assert split_nmcli_terse(r"a\:b:c\\d:") == ["a:b", "c\\d", ""]


class TestQueries:

    def test_scan(self):
        nm = FakeNmcli(scan="Home:40:WPA2\nOffice:90:WPA3\nHome:70:WPA2\n:55:WPA2\n--:50:\n"
                            "Weird--Net:80:\nCaf\\:e:n/a:\n")
        assert manager(nm).scan_networks() == [
            {"ssid": "Office", "signal": 90, "security": "WPA3"},
            {"ssid": "Weird--Net", "signal": 80, "security": ""},
            {"ssid": "Home", "signal": 40, "security": "WPA2"},
            {"ssid": "Caf:e", "signal": 0, "security": ""},
        ]

    def test_scan_failure(self):
        assert manager(FakeNmcli(fail=FileNotFoundError())).scan_networks() == []

    @pytest.mark.parametrize("active, modes, expected", [
        ("Hotspot:802-11-wireless:wlan0\n", {"Hotspot": "ap"}, True),
        ("MyAPARTMENT:802-11-wireless:wlan0\n", {"MyAPARTMENT": "infrastructure"}, False),
        ("Coop\\:AP:802-11-wireless:wlan0\n", {"Coop:AP": "ap"}, True),
        ("Hotspot:802-11-wireless:wlan1\n", {"Hotspot": "ap"}, False),
        ("Hotspot:802-11-wireless:wlan0\n", {}, True),  # mode unreadable → name fallback
        ("Wired:802-3-ethernet:eth0\n", {}, False),
    ])
    def test_ap_mode(self, active, modes, expected):
        assert manager(FakeNmcli(active, modes)).is_ap_mode_active() is expected

    def test_ap_mode_cached_including_failures(self):
        now = {"t": 0.0}
        nm = FakeNmcli(fail=OSError())
        m = manager(nm, monotonic=lambda: now["t"])
        assert m.is_ap_mode_active() is False
        m.is_ap_mode_active()
        assert len(nm.calls) == 1
        now["t"] = 31
        m.is_ap_mode_active()
        assert len(nm.calls) == 2

    def test_ethernet(self):
        assert manager(FakeNmcli(devices="wifi:connected\nethernet:connected\n")).is_ethernet_connected()
        assert not manager(FakeNmcli(devices="ethernet:unavailable\n")).is_ethernet_connected()
        assert not manager(FakeNmcli(fail=OSError())).is_ethernet_connected()

    def test_current_connection(self):
        nm = FakeNmcli("Wired:802-3-ethernet:eth0\nHome\\:Net:802-11-wireless:wlan0\n")
        assert manager(nm).get_current_connection() == {"ssid": "Home:Net"}
        assert manager(FakeNmcli("")).get_current_connection() is None
        assert manager(FakeNmcli(fail=OSError())).get_current_connection() is None


class TestActions:

    def test_connect(self):
        nm = FakeNmcli()
        assert manager(nm).connect("Home", "pw", timeout=12) is True
        assert nm.calls[-1] == ["nmcli", "dev", "wifi", "connect", "Home", "password", "pw"]
        manager(nm).connect("Open", None)
        assert nm.calls[-1] == ["nmcli", "dev", "wifi", "connect", "Open"]

    @pytest.mark.parametrize("exc", [subprocess.TimeoutExpired("nmcli", 1),
                                     subprocess.CalledProcessError(1, "nmcli", output=b"no network"),
                                     subprocess.CalledProcessError(1, "nmcli"), OSError("x")])
    def test_connect_failures(self, exc):
        assert manager(FakeNmcli(fail=exc)).connect("Home", "pw") is False

    def test_start_ap_sets_up_captive_portal(self):
        nm, shell = FakeNmcli(), mock.Mock()
        assert manager(nm, shell=shell).start_ap("COOP", "password1", "192.168.4.1") is True
        assert nm.calls[-1] == ["nmcli", "dev", "wifi", "hotspot", "ifname", "wlan0", "ssid", "COOP",
                                "password", "password1"]
        cmds = [c.args[0] for c in shell.call_args_list]
        tee = [c for c in shell.call_args_list if "tee" in c.args[0]][0]
        assert tee.kwargs["input"] == b"address=/#/192.168.4.1\n"
        iptables = [c for c in cmds if "iptables" in c]
        assert "-D" in iptables[0] and "-I" in iptables[1] and iptables[1][-1] == "5000"

    @pytest.mark.parametrize("exc", [subprocess.CalledProcessError(1, "nmcli", output=b"x"), OSError()])
    def test_start_ap_failure(self, exc):
        assert manager(FakeNmcli(fail=exc)).start_ap("COOP", "password1") is False

    def test_mock_mode_runs_nothing(self):
        nm = FakeNmcli()
        m = WifiManager(mock=True, run=nm, shell=nm)
        assert m.scan_networks() and m.connect("a", "b") and m.start_ap("a", "b")
        assert m.is_ap_mode_active() is False and m.is_ethernet_connected() is False
        assert m.get_current_connection() == {"ssid": "Mock-WiFi-1"}
        assert nm.calls == []


class TestWatchdog:

    def wifi(self, ap=False, connection=None, connect=True):
        w = mock.Mock()
        w.is_ap_mode_active.return_value = ap
        w.get_current_connection.return_value = connection
        w.connect.return_value = connect
        return w

    def test_nothing_to_do(self):
        for w in (self.wifi(ap=True), self.wifi(connection={"ssid": "x"})):
            run_wifi_watchdog(w, WifiConfig(ssid="Home"))
            w.connect.assert_not_called(); w.start_ap.assert_not_called()

    def test_connects(self):
        w = self.wifi()
        run_wifi_watchdog(w, WifiConfig(ssid="Home", password="pw", timeout=45))
        w.connect.assert_called_once_with("Home", "pw", timeout=45)
        w.start_ap.assert_not_called()

    def test_falls_back_to_ap(self):
        w = self.wifi(connect=False)
        run_wifi_watchdog(w, WifiConfig(ssid="Home", ap_ssid="COOP", ap_password="secret12"))
        w.start_ap.assert_called_once_with("COOP", "secret12", "10.42.0.1")

    def test_no_ssid_goes_straight_to_ap(self):
        w = self.wifi()
        run_wifi_watchdog(w, WifiConfig())
        w.connect.assert_not_called()
        w.start_ap.assert_called_once()
