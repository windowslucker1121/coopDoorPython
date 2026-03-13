import subprocess
import os
import logging

import time

logger = logging.getLogger(__name__)

class WifiManager:
    def __init__(self):
        self.is_windows = (os.name == 'nt')
        self._ap_mode_cache = False
        self._ap_mode_cache_time = 0

    def scan_networks(self):
        if self.is_windows:
            return [
                {"ssid": "My-Home-WiFi", "signal": 80, "security": "WPA2"},
                {"ssid": "Guest-Network", "signal": 60, "security": "WPA2"}
            ]
        try:
            cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10).decode("utf-8")
            networks = []
            seen = set()
            for line in output.split("\n"):
                if not line.strip():
                    continue
                parts = line.split(":")
                if len(parts) >= 3:
                    ssid = parts[0].replace("\\:", ":")
                    if not ssid or ssid in seen or "--" in ssid:
                        continue
                    seen.add(ssid)
                    try:
                        signal = int(parts[1])
                    except:
                        signal = 0
                    networks.append({
                        "ssid": ssid,
                        "signal": signal,
                        "security": parts[2]
                    })
            return sorted(networks, key=lambda x: x["signal"], reverse=True)
        except Exception as e:
            logger.error(f"Error scanning WiFi: {e}")
            return []

    def connect(self, ssid, password, timeout=30):
        if self.is_windows:
            logger.info(f"Connecting to {ssid} with password {password} for {timeout}s (mocked)")
            return True

        try:
            if password:
                cmd = ["nmcli", "dev", "wifi", "connect", ssid, "password", password]
            else:
                cmd = ["nmcli", "dev", "wifi", "connect", ssid]
            
            logger.info(f"Issuing connect command for {ssid}")
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout while connecting to WiFi {ssid}")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Error connecting to WiFi {ssid}: {e.output.decode('utf-8').strip() if e.output else str(e)}")
            return False
        except Exception as e:
            logger.error(f"Exception in connect: {e}")
            return False

    def _setup_captive_portal(self):
        if self.is_windows:
            return
        
        try:
            logger.info("Setting up Captive Portal routing...")
            # 1. Provide a dnsmasq config so ALL domains resolve to local 10.42.0.1
            # NetworkManager reads this when starting a shared connection's dnsmasq.
            dnsmasq_dir = "/etc/NetworkManager/dnsmasq-shared.d"
            if os.path.exists("/etc/NetworkManager"):
                subprocess.run(["sudo", "mkdir", "-p", dnsmasq_dir], check=False)
                conf_path = os.path.join(dnsmasq_dir, "captive_portal.conf")
                subprocess.run(["sudo", "bash", "-c", f"echo 'address=/#/10.42.0.1' > {conf_path}"], check=False)

            # 2. Redirect port 80 to 5000 using iptables to catch HTTP checks
            subprocess.run(["sudo", "iptables", "-t", "nat", "-D", "PREROUTING", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "REDIRECT", "--to-port", "5000"], stderr=subprocess.DEVNULL)
            subprocess.run(["sudo", "iptables", "-t", "nat", "-I", "PREROUTING", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "REDIRECT", "--to-port", "5000"], stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.error(f"Error setting up captive portal: {e}")

    def start_ap(self, ssid, password):
        if self.is_windows:
            logger.info(f"Starting AP mode with SSID {ssid} and password {password} (mocked)")
            return True

        try:
            self._setup_captive_portal()
            
            # We use "nmcli dev wifi hotspot"
            # In nmcli, the hotspot usually gets connection name "Hotspot"
            cmd = ["nmcli", "dev", "wifi", "hotspot", "ifname", "wlan0", "ssid", ssid, "password", password]
            logger.info(f"Bringing up Hotspot on wlan0: {ssid}")
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=30)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error starting AP mode: {e.output.decode('utf-8').strip() if e.output else str(e)}")
            return False
        except Exception as e:
            logger.error(f"Exception in start_ap: {e}")
            return False

    def is_ap_mode_active(self):
        if self.is_windows:
            return False
            
        # simple 30 second cache to prevent slowing down the web app during captive portal checks
        if time.time() - self._ap_mode_cache_time < 30:
            return self._ap_mode_cache
            
        try:
            cmd = ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"]
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5).decode("utf-8")
            is_active = False
            for line in output.split("\n"):
                if "802-11-wireless" in line and "wlan0" in line:
                    if "Hotspot" in line or "AP" in line:
                        is_active = True
                        break
            
            self._ap_mode_cache = is_active
            self._ap_mode_cache_time = time.time()
            return is_active
        except:
            return False

    def is_ethernet_connected(self):
        if self.is_windows:
            return False
        try:
            cmd = ["nmcli", "-t", "-f", "TYPE,STATE", "device"]
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5).decode("utf-8")
            for line in output.split("\n"):
                if "ethernet:connected" in line:
                    return True
            return False
        except:
            return False

    def get_current_connection(self):
        if self.is_windows:
            return {"ssid": "Mock-WiFi-1"}
        try:
            cmd = ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"]
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5).decode("utf-8")
            for line in output.split("\n"):
                if "wireless" in line or "802-11-wireless" in line:
                    parts = line.split(":")
                    if len(parts) >= 3 and parts[2] == "wlan0":
                        return {"ssid": parts[0]}
            return None
        except:
            return None

