# Setup

How to build, wire, install and run Dinky Coop on a Raspberry Pi.
Back to the [README](../README.md).

## Parts

- Raspberry Pi (with Wi-Fi) and power supply
- Linear actuator to move the door ([door in action](../img/door.gif), sped up 2.5x)
- H-bridge motor driver with `in1` / `in2` / `enable` inputs
- Two endstop switches (door up, door down)
- 3-position switch (open / off / close) as manual override on the coop
- DHT11 sensor inside the coop
- DHT22 sensor outside (optional - the outside values can come from Open-Meteo instead)
- USB webcam (optional)

## Wiring

Drawn with [Fritzing](https://fritzing.org/):

![Wiring diagram](../img/coop_bb.svg)

Default pins (BCM numbering), all changeable in *Door & hardware* or `config.yaml`:

| Function | GPIO |
|---|---|
| Motor `in1` / `in2` / `enable` | 17 / 27 / 22 |
| Endstop up / down | 23 / 24 |
| Override switch open / close | 5 / 6 |
| DHT11 data (coop) | 26 |
| DHT22 data / power (outside) | 21 / 20 |

- Endstops and the override switch are inputs with internal pull-downs: an
  input is *active* when it reads HIGH (3.3 V). An endstop that works the
  other way round can be flipped with `invert_end_up` / `invert_end_down`.
- Motor: open = `in1` LOW, `in2` LOW, `enable` HIGH; close = both HIGH,
  `enable` HIGH; stop = `enable` LOW.
- The DHT22 power pin lets the app power-cycle a hung sensor (set
  `dht22_power` to `null` if you power it directly).

## Install on the Pi

```bash
git clone https://github.com/windowslucker1121/coopDoorPython.git
cd coopDoorPython
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python3 src/app.py
```

Open `http://<pi-address>:5000`, then press **Calibrate now**: the door
closes, opens and remembers its travel time. Sun and Timer mode need this.

Command line (run from `src/`):

| Command | Purpose |
|---|---|
| `python -m coop` | run the controller (same as `python src/app.py`) |
| `python -m coop set-password` | protect the web app with a password |
| `python -m coop disable-auth` | remove the password |
| `python -m coop check-config` | validate `config.yaml` |

## Run as a service

### systemd (recommended)

Restarts on failure and works with the **Update** button.

`/etc/systemd/system/chicken.service`:

```ini
[Unit]
Description=Coop / Chicken Door Application
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/coopDoorPython/
ExecStart=/home/pi/coopDoorPython/venv/bin/python /home/pi/coopDoorPython/src/app.py
Restart=on-failure
KillMode=process

[Install]
WantedBy=multi-user.target
```

`KillMode=process` is required: without it systemd kills the update helper
when the app exits during an update, and the app does not come back.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chicken
sudo systemctl status chicken
journalctl -u chicken -f        # live logs
```

### cron (alternative)

`crontab -e`, then add:

```
@reboot /home/pi/coopDoorPython/cron_script.sh
```

Output goes to `log/app.log`. Caveat: nothing restarts the app after an
update, so the **Update** button does not work reliably - use systemd if you
want it.

### Flaky Wi-Fi (optional)

`check_network.sh` pings an address and resets Wi-Fi when it fails.
`sudo crontab -e`, then add (use your router's IP to check only the local network):

```
@reboot /home/pi/coopDoorPython/check_network.sh 8.8.8.8
```

## Configuration

Everything is set from the app; it is stored in `config.yaml` in the
repository root. The most useful keys:

```yaml
use_mock_hardware: false      # true = simulated door, sensors and camera
simulator_travel_s: 8         # simulated door travel time (mock only)
auto_mode: true               # Sun mode
timer_mode: false             # Timer mode (neither = Manual)
timer_open_time: '07:00'
timer_close_time: '20:00'
sunrise_offset: 0             # minutes
sunset_offset: 0
location: {city: Boulder, region: USA, timezone: America/Denver, latitude: 40.01499, longitude: -105.27055}
outdoor_sensor_type: dht22    # or "api" (Open-Meteo)
enable_camera: false
camera_index: 0
csvLog: true
gpio: {motor_in1: 17, motor_in2: 27, motor_ena: 22, endstop_up: 23, endstop_down: 24,
       override_open: 5, override_close: 6, dht11_data: 26, dht22_data: 21, dht22_power: 20,
       invert_end_up: false, invert_end_down: false, reference_timeout: 60}
wifi: {ssid: '', password: '', ap_ssid: DINKY-COOP, ap_password: password, ap_ip: 10.42.0.1}
log_level: INFO
```

Pin numbers, sensor type, camera and CSV logging take effect after a
restart; everything else applies immediately. Full reference:
[overview.md](../overview.md#4-configuration).

**Push notifications:** create the keys once with
`python3 src/generateVapidPair.py` (stored in `.secrets.yaml`), then turn
them on under *System & updates*.

**Use my location** only gets the exact position on `https://` pages (or
`localhost`); on plain `http://` it guesses from the time zone - check before
saving.

## Updating

*System & updates → Update & restart* pulls the latest version of the
installed branch and restarts the app (needs the systemd service above). The
card shows the channel (**Stable** = `main`, **Dev** = any other branch), the
commit and whether an update is available.

- **Switch to dev release** lists all branches on GitHub; pick one to install
  it. Dev releases may be unstable.
- **Switch to stable (main)** goes back.
- Changed `requirements.txt` is installed automatically during the update.

Manually: `git pull && pip install -r requirements.txt`, then
`sudo systemctl restart chicken`.

## Security

The web app is open by default. To require a password (HTTP Basic, also
guards the live connection):

```bash
cd src && python -m coop set-password
```

Remove it with `python -m coop disable-auth`. If the Pi loses Wi-Fi it opens
the `DINKY-COOP` hotspot - change `ap_password` on the *Network* page.

## Troubleshooting

- **`Unable to set line 21 to input`** (DHT sensor): a stale CircuitPython
  helper holds the pin. The app kills it at startup; if it persists, run
  `killall libgpiod_pulsein64` and restart.
- **`RPi.GPIO RuntimeError: Failed to add edge detection`**
  ([details](https://raspberrypi.stackexchange.com/questions/147332/rpi-gpio-runtimeerror-failed-to-add-edge-detection)):
  `pip uninstall RPi.GPIO && pip install rpi-lgpio`.
- **"Endstop not reached"**: the door took longer than the calibrated time
  plus a margin. Check the endstops on *Door & hardware* (live pins), then
  clear the error from the banner and recalibrate.
- **Both endstops active**: wiring fault or wrong `invert_end_*` setting.
- **App not reachable**: `sudo systemctl status chicken` and
  `journalctl -u chicken -f`, or the *Logs* page.
- **No Pi at hand**: `echo "use_mock_hardware: true" > config.yaml` runs
  everything simulated; *Door & hardware* has a simulator panel.
