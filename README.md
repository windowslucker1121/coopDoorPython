# Dinky Coop

*Those chickens won't open the door themselves...*


|              ![`Coop App`](img/door.gif "door.gif")              |
| :----------------------------------------------------------------: |
| *Automatic door powered by linear actuator, video sped up 2.5x.* |

This is the [Raspberry Pi](https://www.raspberrypi.com) based controller software running my chicken coop. It exhibits the following capabilities:

1. Automatic open and closing of coop door based on sunrise and sunset time (and configurable offset)
2. Open and closing of the coop door via an external 3 position switch
3. Temperature and humidity sensing inside and outside the coop
4. Logging of all data to CSV files
5. A simple [Flask](https://flask.palletsprojects.com/en) web app to view temperature and humidity and command the door

## The Web App

Below is the simple UI for the coop controller. It works well on a PC browser or phone.

![`Coop App`](img/app.png "app.png")

## How it is Wired Up

This is how things are connected, drawn using [Fritzing](https://fritzing.org/).

![`Coop Wiring Diagram`](img/coop_bb.svg "coop_bb.svg")

## How to Install

On your Raspberry Pi, run the following:

```
$ git clone https://github.com/dinkelk/coop.git
$ cd coop
$ python3 -m venv venv
$ source venv/bin/activate
$ pip install --upgrade pip
$ pip install -r requirements.txt
$ python3 src/app.py
```

Now access the webserver with a browser at http://127.0.0.1:5000.

### Trying it without a Raspberry Pi

The controller runs on any computer with simulated hardware (virtual door,
sensors and camera):

```
$ echo "use_mock_hardware: true" > config.yaml
$ python3 src/app.py
```

Open http://127.0.0.1:5000, run the reference sequence and watch the simulated
door move. The mock panel (http://127.0.0.1:5000/mock) lets you toggle the
endstops by hand.

### Command line

Run these from the `src/` directory:

| Command | Purpose |
|---------|---------|
| `python -m coop` | run the controller (same as `python src/app.py`) |
| `python -m coop set-password` | protect the web interface with HTTP Basic auth |
| `python -m coop disable-auth` | remove the web password |
| `python -m coop check-config` | validate `config.yaml` |

### Development

```
$ pip install -r requirements.txt -r requirements-dev.txt
$ pytest
```

See [overview.md](overview.md) for the architecture of the `src/coop` package.

**Note:** Sometimes it is necessary to reset CircuitPython after encountering errors like `Unable to set line 21 to input` by running:

```
$ killall libgpiod_pulsein64
```

## Run Automatically at Startup

### Option A — systemd service (recommended)

Running as a systemd service gives you automatic restart on failure and proper integration with the built-in **Update** button in the web UI.

1. Create the service file:

```bash
sudo nano /etc/systemd/system/chicken.service
```

2. Paste the following content:

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

> **Note:** `KillMode=process` is required so that the update helper script (`update_script.py`) is not killed by systemd when the main process exits during an update. Without it the app will not restart after clicking the Update button.

3. Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable chicken
sudo systemctl start chicken
```

4. Check that it is running:

```bash
sudo systemctl status chicken
```

**Useful commands:**

| Action | Command |
|--------|---------|
| View live logs | `journalctl -u chicken -f` |
| Stop the service | `sudo systemctl stop chicken` |
| Restart the service | `sudo systemctl restart chicken` |
| Disable auto-start | `sudo systemctl disable chicken` |

### Option B — cron (legacy)

To start the controller automatically at boot via cron, run `crontab -e` and append the following entry:

```
@reboot /home/pi/coopDoorPython/cron_script.sh
```

> **Note:** The Update button in the web UI does **not** work reliably with this option because cron does not restart the process after an update. Use the systemd service (Option A) if you need the Update button to work.

## Network Monitoring

My Raspberry Pi is on a flaky network connection and sometimes it is necessary to periodically reset the Wi-Fi. A script is included to monitor the connection and reset it if necessary. To install this script run `sudo crontab -e` and append the following entry.

```
@reboot /home/pi/coop/check_network.sh 8.8.8.8
```

Replace `8.8.8.8` with the IP address of your router if you just want to check local network connectivity.

## Push Notifications

Door errors and blocked schedule moves are sent as Web Push notifications.
Create the VAPID keys once with `python3 src/generateVapidPair.py`; they are
stored in `.secrets.yaml` in the repository root.

Sidenote:

If you encounter any issues when booting up the App regarding

`RPi.GPIO RuntimeError: Failed to add edge detection` More Information [here](https://raspberrypi.stackexchange.com/questions/147332/rpi-gpio-runtimeerror-failed-to-add-edge-detection)

You may want to deinstall `RPi.GPIO` and install `rpi-lgpio` instead with

````
pip uninstall RPi.GPIO
pip install rpi-lgpio
````

