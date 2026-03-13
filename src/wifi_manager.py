import subprocess
import os
import logging

logger = logging.getLogger(__name__)

class WifiManager:
    def __init__(self):
        self.is_windows = (os.name == 'nt')

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

    def start_ap(self, ssid, password):
        if self.is_windows:
            logger.info(f"Starting AP mode with SSID {ssid} and password {password} (mocked)")
            return True

        try:
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
        try:
            cmd = ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"]
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5).decode("utf-8")
            for line in output.split("\n"):
                if "802-11-wireless" in line and "wlan0" in line:
                    if "Hotspot" in line or "AP" in line:
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

