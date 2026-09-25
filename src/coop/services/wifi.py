"""Wi-Fi management through NetworkManager (``nmcli``).

``WifiManager(mock=True)`` returns canned data and never runs commands
(development machines / mock hardware).
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Callable

logger = logging.getLogger(__name__)

Runner = Callable[..., bytes]


def split_nmcli_terse(line: str) -> list[str]:
    """Split one line of ``nmcli -t`` output (``\\:`` / ``\\\\`` escaped)."""
    fields, current = [], []
    chars = iter(line)
    for ch in chars:
        if ch == "\\":
            current.append(next(chars, ""))
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


class WifiManager:
    AP_CACHE_S = 30.0

    def __init__(self, mock: bool = False, *, run: Runner | None = None, shell: Callable | None = None,
                 monotonic: Callable[[], float] = time.monotonic, interface: str = "wlan0"):
        self.mock = mock
        self._check_output = run or subprocess.check_output
        self._run = shell or subprocess.run
        self._monotonic = monotonic
        self.interface = interface
        self._ap_cache: tuple[float, bool] | None = None

    def _nmcli(self, *args: str, timeout: float = 5) -> str:
        return self._check_output(["nmcli", *args], stderr=subprocess.STDOUT, timeout=timeout).decode("utf-8")

    # ── queries ──────────────────────────────────────────────────────
    def scan_networks(self) -> list[dict]:
        if self.mock:
            return [{"ssid": "My-Home-WiFi", "signal": 80, "security": "WPA2"},
                    {"ssid": "Guest-Network", "signal": 60, "security": "WPA2"}]
        try:
            output = self._nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", timeout=10)
        except Exception as e:
            logger.error("Wi-Fi scan failed: %s", e)
            return []
        networks, seen = [], set()
        for line in output.splitlines():
            parts = split_nmcli_terse(line)
            if len(parts) < 3:
                continue
            ssid = parts[0]
            if not ssid or ssid == "--" or ssid in seen:  # "--" = hidden network
                continue
            seen.add(ssid)
            try:
                signal = int(parts[1])
            except ValueError:
                signal = 0
            networks.append({"ssid": ssid, "signal": signal, "security": parts[2]})
        return sorted(networks, key=lambda n: n["signal"], reverse=True)

    def _active_wifi_connections(self) -> list[str]:
        names = []
        for line in self._nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active").splitlines():
            parts = split_nmcli_terse(line)
            if len(parts) >= 3 and parts[1] == "802-11-wireless" and parts[2] == self.interface:
                names.append(parts[0])
        return names

    def _connection_mode(self, name: str) -> str:
        try:
            return self._nmcli("-g", "802-11-wireless.mode", "connection", "show", name).strip().lower()
        except Exception as e:
            logger.debug("Could not read wireless mode of %r: %s", name, e)
            return "ap" if name == "Hotspot" else ""

    def is_ap_mode_active(self) -> bool:
        """True if the interface runs an access point (cached 30 s).

        Decided by the connection's ``802-11-wireless.mode``, never by its
        name (a home network may be called "MyAPARTMENT")."""
        if self.mock:
            return False
        now = self._monotonic()
        if self._ap_cache and now - self._ap_cache[0] < self.AP_CACHE_S:
            return self._ap_cache[1]
        try:
            active = any(self._connection_mode(n) == "ap" for n in self._active_wifi_connections())
        except Exception as e:
            logger.debug("AP mode check failed: %s", e)
            active = False
        self._ap_cache = (now, active)
        return active

    def is_ethernet_connected(self) -> bool:
        if self.mock:
            return False
        try:
            for line in self._nmcli("-t", "-f", "TYPE,STATE", "device").splitlines():
                parts = split_nmcli_terse(line)
                if len(parts) >= 2 and parts[0] == "ethernet" and parts[1] == "connected":
                    return True
        except Exception:
            pass
        return False

    def get_current_connection(self) -> dict | None:
        if self.mock:
            return {"ssid": "Mock-WiFi-1"}
        try:
            names = self._active_wifi_connections()
        except Exception:
            return None
        return {"ssid": names[0]} if names else None

    # ── actions ──────────────────────────────────────────────────────
    def connect(self, ssid: str, password: str | None, timeout: float = 30) -> bool:
        if self.mock:
            logger.info("Connecting to %s (mocked)", ssid)
            return True
        cmd = ["dev", "wifi", "connect", ssid] + (["password", password] if password else [])
        try:
            logger.info("Connecting to Wi-Fi %s", ssid)
            self._nmcli(*cmd, timeout=timeout)
            self._ap_cache = None
            return True
        except subprocess.TimeoutExpired:
            logger.error("Timeout while connecting to Wi-Fi %s", ssid)
        except subprocess.CalledProcessError as e:
            detail = e.output.decode("utf-8", "replace").strip() if e.output else str(e)
            logger.error("Error connecting to Wi-Fi %s: %s", ssid, detail)
        except Exception as e:
            logger.error("Error connecting to Wi-Fi %s: %s", ssid, e)
        return False

    def start_ap(self, ssid: str, password: str, ap_ip: str = "10.42.0.1") -> bool:
        if self.mock:
            logger.info("Starting AP %s (mocked)", ssid)
            return True
        try:
            self._setup_captive_portal(ap_ip)
            logger.info("Starting hotspot %s on %s", ssid, self.interface)
            self._nmcli("dev", "wifi", "hotspot", "ifname", self.interface, "ssid", ssid,
                        "password", password, timeout=30)
            self._ap_cache = None
            return True
        except subprocess.CalledProcessError as e:
            detail = e.output.decode("utf-8", "replace").strip() if e.output else str(e)
            logger.error("Error starting AP mode: %s", detail)
        except Exception as e:
            logger.error("Error starting AP mode: %s", e)
        return False

    def _setup_captive_portal(self, ap_ip: str) -> None:
        """Resolve every hostname to the AP and redirect port 80 to the app."""
        try:
            conf_dir = "/etc/NetworkManager/dnsmasq-shared.d"
            self._run(["sudo", "mkdir", "-p", conf_dir], check=False)
            self._run(["sudo", "tee", f"{conf_dir}/captive_portal.conf"],
                      input=f"address=/#/{ap_ip}\n".encode(), stdout=subprocess.DEVNULL, check=False)
            rule = ["PREROUTING", "-i", self.interface, "-p", "tcp", "--dport", "80",
                    "-j", "REDIRECT", "--to-port", "5000"]
            self._run(["sudo", "iptables", "-t", "nat", "-D", *rule], stderr=subprocess.DEVNULL, check=False)
            self._run(["sudo", "iptables", "-t", "nat", "-I", *rule], stderr=subprocess.DEVNULL, check=False)
        except Exception as e:
            logger.error("Captive portal setup failed: %s", e)


def run_wifi_watchdog(wifi: WifiManager, wifi_settings) -> None:
    """One-shot boot check: join the configured network or fall back to AP."""
    if wifi.is_ap_mode_active():
        logger.info("Wi-Fi watchdog: AP mode already active.")
        return
    current = wifi.get_current_connection()
    if current:
        logger.info("Wi-Fi watchdog: connected to %s.", current.get("ssid"))
        return
    logger.warning("Wi-Fi watchdog: no Wi-Fi connection after boot.")
    if wifi_settings.ssid and wifi.connect(wifi_settings.ssid, wifi_settings.password,
                                           timeout=wifi_settings.timeout):
        logger.info("Wi-Fi watchdog: connected to %s.", wifi_settings.ssid)
        return
    logger.warning("Wi-Fi watchdog: falling back to AP mode (%s).", wifi_settings.ap_ssid)
    wifi.start_ap(wifi_settings.ap_ssid, wifi_settings.ap_password, wifi_settings.ap_ip)
