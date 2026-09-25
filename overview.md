# Dinky Coop — Project Overview

This document describes how the application works today: every module, the
runtime model, shared state, configuration, the HTTP / Socket.IO API, the door
control state machine, and a list of known quirks and bugs. It is meant as
the baseline for the upcoming refactoring, together with the test suite in
`tests/` (see [Testing](#10-testing)).

---

## 1. What the application does

A Raspberry Pi controller for a chicken-coop door, plus a web dashboard:

* Opens and closes the coop door with a DC motor (linear actuator or winch)
  driven through an H-bridge. Two endstop switches mark the fully open and
  fully closed positions.
* **Auto mode** opens and closes the door at sunrise and sunset for a
  configured location, with configurable offsets.
* **Timer mode** opens and closes the door at fixed times (`HH:MM`).
* **Manual mode**: open, close or stop from the web UI, or with a physical
  3-position override switch.
* Detects stuck doors and false endstop triggers (a chicken pushing the door
  up while it closes), then retries or raises an error state and sends a
  Web Push notification.
* Measures indoor temperature and humidity (DHT11) and outdoor temperature
  and humidity (DHT22, or the Open-Meteo API), plus the CPU temperature.
* Streams live data, logs and an optional webcam feed to the browser over
  Socket.IO, and logs data to daily CSV files.
* Handles Wi-Fi: joins the configured network or falls back to its own
  access point with a captive portal.
* Can update itself from the web UI (`git pull` and restart) and reboot the Pi.

---

## 2. Repository layout

```
coopDoorPython/
├── src/
│   ├── app.py                       # Flask + Socket.IO app, config, background tasks, all endpoints (~1700 lines)
│   ├── door.py                      # DOOR class: GPIO motor/endstop/switch driver + reference sequence
│   ├── door_task_runner.py          # DoorTaskRunner: one iteration of the door control loop (state machine)
│   ├── protected_dict.py            # Thread-safe singleton key/value store (the "global_vars")
│   ├── wifi_manager.py              # nmcli wrapper: scan/connect/hotspot/captive-portal setup
│   ├── update_script.py             # Detached helper: kill app → git pull → restart
│   ├── temperature_sensor.py        # Abstract TemperatureSensor interface
│   ├── dht11.py / dht22.py          # Adafruit DHT wrappers (retry, re-init, DHT22 power cycling)
│   ├── location_temperature_sensor.py  # Open-Meteo outdoor sensor (cached, 5 min)
│   ├── camera.py                    # OpenCV webcam wrapper
│   ├── mock_gpio.py                 # MockGPIO (RPi.GPIO replacement, supports triggering edges)
│   ├── MockDHT11.py / MockDHT22.py  # Random-walk sensor mocks
│   ├── mock_temperatur.py           # Mock CPUTemperature
│   ├── mock_board.py / mock_camera.py
│   ├── generateVapidPair.py         # CLI: create VAPID keys → .secrets.yaml (interactive)
│   ├── generateIcons.py             # CLI: render PWA icons with OpenCV
│   ├── sw.js / manifest.json        # PWA service worker + manifest (served from /sw.js, /manifest.json)
│   ├── templates/
│   │   ├── grid_dashboard.html      # Main UI (served at /)
│   │   ├── debug.html               # Debug panel (/debug): pins, globals, threads, GPIO & WiFi config
│   │   ├── mock.html                # Windows-only pin simulator (/mock)
│   │   ├── index.html               # LEGACY – not referenced by any route
│   │   └── index_optimized_door.html# LEGACY – not referenced by any route
│   └── static/                      # images, icons, socket.io.js, chart.js, offline.html
├── tests/                           # pytest suite (see §10)
├── cron_script.sh                   # legacy autostart via cron
├── check_network.sh                 # ping watchdog: restart wlan0 / reboot (skips in hotspot mode)
├── requirements.txt / requirements-dev.txt
└── img/                             # README images (wiring diagram, GIF)
```

Files created at runtime (all git-ignored): `config.yaml`, `.secrets.yaml`,
`.subscriptions.json`, `version.txt`, `log/` (`app.log*`, `YYYY_MM_DD.csv`).

---

## 3. Runtime model

```
                    ┌───────────────────── app.py (__main__) ─────────────────────┐
                    │ load_config → configure_logging → load_notification_keys     │
                    │ → reload_location_data → start daemon threads → socketio.run │
                    └──────────────────────────────────────────────────────────────┘
      threads (gevent greenlets after monkey.patch_all)
      ├── door_task          DoorTaskRunner.step() every 0.5 s  ── drives DOOR ── GPIO
      ├── temperature_task   read sensors every 2.5 s (+ sensor delays)
      ├── data_log_task      append get_all_data() to log/YYYY_MM_DD.csv every 5 s (if csvLog)
      ├── data_update_task   socketio.emit('data', get_all_data()) every 1 s
      ├── camera_task        emit base64 JPEG every 0.1 s (if enable_camera)
      └── wifi_watchdog_task one-shot, 15 s after boot: connect or start AP
      GPIO edge callbacks (RPi.GPIO C thread): DOOR.endstop_hit / DOOR.switch_activated
```

* **All communication between threads goes through `protected_dict`**
  (imported as `global_vars`). It is a singleton dict protected by a lock.
  Every get and set **deep-copies** the value.
* Flask runs through Flask-SocketIO with `async_mode='gevent'`.
  `gevent.monkey.patch_all()` is called in `app.py` right after importing
  `door` and `door_task_runner`, so their module-level `time` references are
  captured first. `door.py` keeps `_hw_sleep` (the original `time.sleep`) for
  use inside GPIO callbacks, which run in native threads.
* **Import-time side effects of `app.py`**: it deletes `.lgd-nfy*` files, runs
  `killall -9 libgpiod_pulsein*` (on non-Windows systems), reads `config.yaml`
  to decide between real and mock hardware, creates the Flask/SocketIO app,
  and instantiates `WifiManager`.
* **Hardware selection**:
  * `door.py`: Windows → `MockGPIO`. Otherwise `use_mock_hardware: true` in
    `config.yaml` → `MockGPIO`. Otherwise it tries `RPi.GPIO` and falls back to
    `MockGPIO` if the import fails.
  * `app.py`: the same rule for DHT11/DHT22, `board`, `CPUTemperature` and
    `Camera`. If any hardware import fails, *all* of them fall back to mocks.

---

## 4. Shared state (`global_vars` keys)

| Key | Written by | Read by | Notes |
|---|---|---|---|
| `auto_mode`, `timer_mode` | config, socket `toggle`/`toggle_timer`/`open`/`close`, runner (disables itself when there is no reference) | runner, UI | **strings** `"True"` / `"False"` |
| `timer_open_time`, `timer_close_time` | config, socket `timer_times` | runner | `"HH:MM"` |
| `sunrise_offset`, `sunset_offset` | config, socket `auto_offsets` | runner, `get_all_data` | minutes (int) |
| `location` | config, socket `update_location` | `reload_location_data`, API sensor | `{city, region, timezone, latitude, longitude}` |
| `desired_door_state` | UI (`open`/`close`/`stop`), runner (modes, retries, reconciliation) | runner | `"open"`, `"closed"` or `"stopped"` (initialised to `"stopped"` in `__main__`) |
| `state`, `override`, `error_state`, `door_position_estimate`, `sunrise`, `sunset` | runner (end of every step) | `get_all_data` → UI | `error_state` is `""` when there is no error; the position is 0..1, or -1 when unknown |
| `reference_door_endstops_ms` | runner after a successful reference run | runner, UI | **not persisted** (see §11) |
| `toggle_reference_of_endstops`, `clear_error_state`, `debug_error` | socket events | runner (consumed and reset to `False`) | one-shot flags |
| `temp_in/out`, `hum_in/out`, `cpu_temp` + `_min`/`_max` | temperature_task | `get_all_data` | temperatures stored in **°F** (CPU in °C); min/max reset daily |
| `gpio`, `wifi` | config, `/api/gpio-config`, `/api/wifi-config` | DOOR, temperature_task, wifi code | merged with `GPIO_DEFAULTS` / `WIFI_DEFAULTS` on load |
| `enable_camera`, `camera_index`, `csvLog`, `outdoor_sensor_type`, `use_mock_hardware`, `consoleLogToFile` | config | tasks | `consoleLogToFile` is unused |
| `vapid_public_key`, `vapid_private_key` | `.secrets.yaml` | index template, push | the private key is cached in the module global `vapid_private_key` |

---

## 5. Configuration (`config.yaml`, repository root)

`load_config()` starts from built-in defaults, then applies the YAML file
(`gpio` and `wifi` sub-dicts are merged key by key with their defaults). It
**writes the file only if it does not exist yet**. Unknown keys in the file
are loaded into `global_vars` too. `save_config()` writes a fixed set of keys:

```yaml
use_mock_hardware: false
auto_mode: 'True'            # string!
timer_mode: 'False'          # string!
timer_open_time: '07:00'
timer_close_time: '20:00'
sunrise_offset: 0            # minutes
sunset_offset: 0
location: {city: Boulder, region: USA, timezone: America/Denver, latitude: 40.01499, longitude: -105.27055}
consoleLogToFile: false      # unused
csvLog: true                 # start data_log_task
enable_camera: false
camera_index: 0
outdoor_sensor_type: dht22   # or "api" (Open-Meteo)
gpio:
  motor_in1: 17   motor_in2: 27   motor_ena: 22
  endstop_up: 23  endstop_down: 24
  override_open: 5  override_close: 6
  dht11_data: 26  dht22_data: 21  dht22_power: 20
  invert_end_up: false  invert_end_down: false
  reference_timeout: 60      # seconds, reference-sequence timeout
wifi:
  ssid: ''  password: ''  timeout: 60
  ap_ssid: DINKY-COOP  ap_password: password  ap_ip: 10.42.0.1  ap_allowed_hosts: []
```

`.secrets.yaml` holds `secrets: {vapid_public_key, vapid_private_key}` and is
created by `src/generateVapidPair.py`.

---

## 6. Door hardware layer — `door.py`

**Wiring.** Motor direction is set by `in1`/`in2` and the motor is powered by
`ena`:

| Action | in1 | in2 | ena |
|---|---|---|---|
| open (up) | LOW | LOW | HIGH |
| close (down) | HIGH | HIGH | HIGH |
| stop | LOW | LOW | LOW |

An endstop counts as "hit" when its pin reads HIGH, or LOW if
`invert_end_*` is set. Physically, the *lower* endstop sits at the motor and
fires when the rope goes slack, which is why a chicken lifting the door can
trigger it too early.

**Module globals.** The pin numbers, invert flags and
`referenceSequenceTimeout` are module-level variables. `DOOR.__init__`
overwrites them from `global_vars["gpio"]`, and `/api/gpio-config` overwrites
the invert flags and the timeout at runtime.

**`DOOR` states** (`self.state`): `stopped`, `opening`, `closing`, `open`,
`closed`. When an error is raised, the state is set to the error message
itself (see §11). Other attributes: `override` (physical switch active),
`errorState`, `startedMovingTime`, `reference_door_endstops_ms`,
`reference_door_active`.

| Method | Behaviour |
|---|---|
| `open()` / `close()` | Does nothing in error state. If the destination endstop is already hit, it only calls `stop(state="open"/"closed")`. Otherwise it drives the motor, sets the state to `opening`/`closing`, and sets `startedMovingTime` when the state changes. |
| `stop(state="stopped")` | Turns all outputs LOW and sets the state (logged only when the state changes). |
| `ErrorState(msg=None, stopDoor=True)` | With a new message: stops the door and stores the error. Returns `True` while an error is stored. |
| `clear_errorState()` | Clears the error. |
| `endstop_hit(channel)` | Edge callback. Ignored during a reference run or in error state. Ignores the lower endstop while opening and the upper one while closing. Otherwise it stops the door as `open` or `closed`. |
| `check_endstops()` | Endstop polling safety net, called by every runner step. Uses the same direction rules. Returns `True` if an endstop is active. |
| `switch_activated(channel)` | Physical switch callback: 50 ms debounce, then if exactly one of the open/close inputs is HIGH it sets `override=True` and opens or closes. |
| `check_if_switch_neutral(nuetral_state)` | If both switch inputs are equal (neutral): sets `override=False` and stops as `open`/`closed` when at an endstop, otherwise with the given state. |
| `reference_endstops()` | Blocking. Refuses if an error is set or any endstop is already active. Closes until the lower endstop is hit (confirmed twice, 0.1 s apart), then opens until the upper endstop is hit, and measures the **closed→open travel time in ms**. On timeout (`referenceSequenceTimeout` per leg) it raises an error. |

---

## 7. Door control loop — `door_task_runner.py`

`app.door_task()` builds a `DoorTaskRunner(door, get_sunrise_sunset,
get_current_time, send_notification)` and calls `step()` forever. It sleeps
0.5 s after each step, except when `step()` returns `False` (failed reference
run). The dependencies are injected so tests can control time and capture
notifications.

**One `step()`:**

1. **Flags.** `debug_error` → `ErrorState("Test Error")`. `clear_error_state`
   → clears the error, the notification flags and all retry bookkeeping.
2. **Reference run.** If `toggle_reference_of_endstops` is set, it runs
   `door.reference_endstops()` (blocking), stores
   `reference_door_endstops_ms` in `global_vars`, and **skips the rest of the
   step**. Returns `False` on failure.
3. Reads the door state, the override flag, and the `global_vars` values
   (`desired_door_state`, modes, reference, timer times). Calls
   `door.set_auto_mode(auto or timer)`.
4. **Error short-circuit.** If the door is in error: sends a single "Door
   Error" push, resets the move counter and retry state, publishes
   state/override/sunrise/sunset/error_state, and returns. This avoids
   oscillation and log spam while in error.
5. When the desired state changes, `door_move_count` is reset (the change is
   logged in auto/timer mode).
6. **Mode blocks** (only one runs; auto has priority over timer):
   * *auto, no override*: with no reference, it sets `auto_mode="False"`.
     Otherwise `open_time = sunrise + offset` and
     `close_time = sunset + offset`. On the first iteration it sets the
     desired state to `open` or `closed` depending on the time of day. Within
     1 minute after the open time it sets `open`. Within 1 minute after the
     close time it sets `closed`, unless a retry is pending.
   * *auto with override*: if a window is active (or it is the first
     iteration), it sends one "Manual Override Active" push. The flag re-arms
     when the override is released.
   * *timer, no override / with override*: the same logic using today's
     `HH:MM` times. If the times cannot be parsed, both default to "now",
     which results in `closed`.
   * The mode blocks write `desired_door_state` into `global_vars`, but this
     step keeps using the value read in step 3. **The motor therefore reacts
     one step later.**
7. `door.check_endstops()` runs as a polling safety net.
8. **Premature lower-endstop detection** (auto or timer mode, no override).
   It triggers when the door went closing→closed (or a retry just fired) and
   the total drive time (the sum over all retries of this close cycle) is
   less than `0.8 × reference`. Then:
   * count < `PREMATURE_CLOSE_MAX_RETRIES` (5): stop, set desired to
     `stopped`, and schedule a retry in 5 s.
   * count reaches 5: error state, desired `stopped`, "Door Error" push.
   * a close with enough drive time resets the counters.
9. **Retry firing**: after the 5 s cooldown it sets desired `closed`, restarts
   `startedMovingTime`, and sets `_close_retry_just_fired` so the drive block
   runs even if the endstop is still active. `last_d_door_state` is kept in
   sync so the motor budget is not reset.
10. Leaving auto/timer mode, or engaging the override, clears the retry state.
11. **Drive block**:
    * override → `check_if_switch_neutral()`, and the desired state mirrors
      the door state.
    * state ≠ desired (or a retry just fired):
      * `stopped`: when at an endstop (and no retry is pending), the desired
        state is reconciled to `open`/`closed`. Otherwise it stops.
      * `open` / `closed`: calls `door.open()` / `door.close()` and adds
        0.5 to `door_move_count`. When the count exceeds
        `reference_s + DOOR_MOVE_MAX_AFTER_ENDSTOPS` (20; 10 s is used if
        there is no reference) → `ErrorState("Endstop not reached")` and
        desired `stopped`. The count is **per iteration**, not wall-clock time.
      * any other value → error and an `AssertionError`.
    * otherwise (in the desired state) → `check_if_switch_neutral(current
      state)` and the move count is reset.
12. **Commit**: `first_iter=False`, `was_door_closing`, the position estimate
    (1.0 open, 0.0 closed, otherwise integrated from elapsed time / reference,
    -1 when unknown), then publishes `state`, `override`, `sunrise`, `sunset`,
    `error_state` and `door_position_estimate`.

---

## 8. Sensors & background tasks (`app.py`)

* **`temperature_task`**: creates DHT11 (indoor) and DHT22 or
  `LocationAPITemperatureSensor` (outdoor, when `outdoor_sensor_type: api`)
  using the pins from `gpio`. Each loop reads both sensors and resets the
  min/max values on a new day (to 500/-500 sentinels). `update_val` applies a
  **spike filter**: a jump of more than ±5° from the previous value is
  replaced with the previous value, unless it happens 3 times in a row. Then
  it updates the value, min and max. It also reads the CPU temperature.
  Exceptions are logged and the loop continues.
* **DHT wrappers**: 3 attempts; `RuntimeError`/`OverflowError` → sleep 2.2 s;
  `OSError` → exit and recreate the Adafruit device. DHT22 optionally powers
  the sensor through `dht22_power` around each read (+2.2 s warm-up). Both
  return (°F, %) or `None`.
* **`LocationAPITemperatureSensor`**: calls Open-Meteo `current=temperature_2m,
  relative_humidity_2m` in °F with a 10 s timeout. Results are cached for
  300 s, and failures also restart the cache window. On error it returns the
  last good value.
* **`data_update_task`**: `emit('data', get_all_data())` every second.
* **`get_all_data()`**: the dashboard snapshot. Temperatures are converted
  °F→°C and formatted (`"21.5°C"`, `"40.0%"`). `override` shows the state
  while the override is active, otherwise `"off"`. It computes the time until
  open/close (`HH:MM:SS`, `passed`, or `disabled` when auto is off), plus
  system metrics (psutil), uptime and timer settings. Sunrise and sunset are
  only filled in when auto mode ran.
* **`data_log_task`**: when `csvLog` is on, appends the `get_all_data()`
  values to `log/YYYY_MM_DD.csv` every 5 s. The header line starts with `# `.
* **`camera_task`**: when `enable_camera` is not `False`, emits
  base64-encoded JPEG frames as `camera`. A `RuntimeError` ends the task.
* **`wifi_watchdog_task`**: waits 15 s after boot. If AP mode is active or a
  connection exists, it does nothing. Otherwise it connects to the configured
  SSID (with the configured timeout, default 60) and falls back to
  `start_ap(ap_ssid, ap_password)`.
* **Logging**: root logger at DEBUG. Handlers: `SocketIOHandler` (keeps a
  buffer of the last 100 lines, emits `log`, and replays the buffer on
  connect), stdout, and `TimedRotatingFileHandler` for `log/app.log` (rotates
  at midnight, keeps 30 files). Format: `time - logger - LEVEL - message`.
* **Push notifications**: `send_push_notification(title, body)` loads the
  private key (cached) and `.subscriptions.json` (from the **CWD**), then
  calls `webpush` for each subscription. Subscriptions that return HTTP 410
  are removed and the file is rewritten.

---

## 9. External interfaces

### 9.1 HTTP routes

| Method & path | Purpose |
|---|---|
| `GET /` | Dashboard (`grid_dashboard.html`) with settings, location list and VAPID public key |
| `GET /debug` | Debug panel |
| `GET /mock` | Pin simulator (Windows only, 403 elsewhere) |
| `GET /favicon.ico`, `/manifest.json`, `/sw.js`, `/static/*` | PWA assets |
| `POST /subscribe` | Append a push subscription to `.subscriptions.json` (no dedupe) |
| `GET /version` | `{"version": <version.txt from CWD or "unknown">}` |
| `GET /api/logs` | App log files (`app.log`, `app.log.YYYY-MM-DD`, `app_*.log`), newest first |
| `GET /api/logs/<name>` | Parsed lines `{t, lg, lv, m}`; lines that don't match the format are returned as `lv: "RAW"`. The name is reduced to its basename and checked against the pattern. |
| `GET /api/csv` | CSV files, sorted by name in descending order |
| `GET /api/csv/<name>` | Parsed rows (time, temperature/humidity numbers, state/override/auto_mode/errorstate), downsampled to at most 600 rows |
| `GET/POST /api/gpio-config` | Read or validate and save pin config (pins 0–40, timeout 5–600 s). The invert flags and timeout apply immediately; pin changes need a restart. |
| `GET/POST /api/wifi-config` | Read or save ssid/password/ap_ssid/ap_password/timeout |
| `GET /api/wifi-status`, `GET /api/wifi-scan` | Network state / scan |
| `POST /api/wifi-ap` | Switch to hotspot now |
| `POST /api/wifi-connect` | Connect now (not saved). On failure, waits 5 s and then starts the AP. |
| `POST /api/system/time` | `sudo date -s "YYYY-MM-DD HH:MM:SS"` |
| `POST /api/restart` | Reboots the device (`sudo systemctl reboot` after 1 s) |
| `POST /update` | Spawns `update_script.py <app> <pid> [service]`, then calls `os._exit(0)` after 1 s |
| `GET /generate_204`, `/gen_204`, `/hotspot-detect.html`, `/success.html` | Captive-portal probes |

All `/api/*` responses get no-cache headers. **Captive portal:** in AP mode, a
`before_request` hook redirects every non-`/static`, non-`/api` request whose
Host is not an IP, `localhost`, the hostname, `dinky-coop`, `dinkycoop`,
`*.local` or one of `ap_allowed_hosts` to `http://<ap_ip>/`.
`WifiManager._setup_captive_portal` configures dnsmasq to resolve every name
to `ap_ip` and adds an iptables redirect from wlan0:80 to 5000.

### 9.2 Socket.IO events

| Client → server | Effect |
|---|---|
| `open` / `close` | Turn auto and timer mode off (not saved), set desired `open`/`closed` |
| `stop` | Set desired `stopped` (the modes stay on) |
| `toggle {toggle}` | Auto mode on (timer off) or off; saved |
| `toggle_timer {toggle}` | Timer mode on (auto off) or off; saved |
| `timer_times {timer_open_time, timer_close_time}` (or `open_time`/`close_time`) | Saved when both are given |
| `auto_offsets {sunrise_offset, sunset_offset}` | Converted to int and saved |
| `update_location {city, region, timezone, latitude, longitude}` | Saved and sunrise/sunset recalculated |
| `reference_endstops` / `clear_error` / `generate_error` | Set the one-shot flag for the runner |
| `get_csv_data` | Emits `csv_data` with today's CSV lines (if the file exists) |
| `get_debug_data` | Emits `debug_data`: pins, door constants, masked globals, system info, threads, logs |
| `mock_trigger_pin`, `mock_get_outputs` | Windows only |

| Server → client | Payload |
|---|---|
| `data` | `get_all_data()` every 1 s |
| `log` | `{message}` per log record, plus the buffer replayed on connect |
| `camera` | base64 JPEG |
| `csv_data`, `debug_data`, `mock_update_outputs` | Replies to the requests above |

### 9.3 Frontend (not covered by tests)

* **`grid_dashboard.html`** (served at `/`): cards for the error banner,
  webcam, door control (animated sky/door SVG using `door_position_estimate`,
  open/close/stop, auto and timer toggles, offsets, reference button), system
  metrics, temperature, data visualisation (Chart.js via `/api/csv`), location
  settings, a log viewer modal (`/api/logs`, auto-refresh every 3 s), client
  time sync (`/api/system/time`), the update button (`/update`, then polls
  `/version`), and service-worker registration with push subscription
  (`/subscribe`).
* **`debug.html`**: polls `get_debug_data` every second. Includes the GPIO
  config editor, WiFi config/scan/connect/AP, network status and reboot.
* **`sw.js`**: pre-caches the shell. `/api/*` is always fetched from the
  network. Page navigations are network-first with an offline fallback;
  other GETs are cache-first. It also shows push notifications and opens the
  app when one is clicked.

---

## 10. Testing

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                      # runs tests/ (pytest.ini)
pytest --cov=src            # coverage (≈83 % overall, app.py ≈86 %, door/runner ≈96 %)
```

The tests do not need a Raspberry Pi. On a Pi, stop the service before
running them.

| File | Covers |
|---|---|
| `conftest.py` | Puts `src/` on the path. Autouse reset of MockGPIO, `global_vars` and the `door` module globals. `app_module` imports `app.py` with `gevent.monkey.patch_all` and `subprocess.run` stubbed. `app_env` redirects `root_path`/`config_filename`/CWD to `tmp_path`, replaces `wifi_mgr` with `FakeWifiManager`, and resets location and VAPID state. Also provides `client` and `sio_client`. |
| `test_integration.py` | Scenario tests for DOOR + DoorTaskRunner: reference run, premature-close retries, error handling, basic movement, timer mode |
| `test_door_task_runner.py` | Runner branches: flags, error short-circuit, auto/timer mode selection and windows, override notifications, drive block, reconciliation, move budget, state commit, position estimate |
| `test_door.py` | DOOR: config loading, motor outputs, inverted endstops, error state, edge callbacks, polling, manual switch, reference sequence (fake clock) |
| `test_app_core.py` | Config load/save/merge, secrets, location/sun, `get_all_data`, temperature task (spike filter, min/max, API sensor), CSV/data/camera/wifi-watchdog tasks, push notifications, logging |
| `test_app_routes.py` | Every HTTP route, including captive portal, log/CSV parsing and traversal, GPIO/WiFi config validation, system time, reboot and update (all side effects faked) |
| `test_app_socketio.py` | Every Socket.IO event |
| `test_wifi_manager.py` | nmcli parsing, connect/AP/captive-portal commands, AP cache, errors |
| `test_update_script.py` | Update helper flow: systemd vs. direct relaunch, failures |
| `test_dht_sensors.py`, `test_camera.py`, `test_location_temperature_sensor.py`, `test_mock_hardware.py`, `test_protected_dict.py` | Sensor wrappers (fake `adafruit_dht`/`cv2`/`requests`), mocks, store |

The tests are **characterisation tests**: they pin down *current* behaviour,
including the quirks below (look for comments containing "Quirk"). When a
refactor intentionally changes one of these behaviours, update the matching
test in the same change. Known bugs are written as `xfail(strict=True)` tests,
so fixing one makes its test fail until the marker is removed.

Two tests in `test_integration.py` were already failing on `main` because
they were out of date, and have been fixed:
`test_move_budget_exhausted_sets_error` (still assumed
`DOOR_MOVE_MAX_AFTER_ENDSTOPS = 1`) and
`test_timer_mode_closes_door_at_set_time` (predated premature detection in
timer mode).

Not covered: the `__main__` startup block, Windows-only branches,
`generateIcons.py`, `generateVapidPair.py` (runs interactively on import),
shell scripts and the frontend JavaScript.

---

## 11. Known quirks, bugs & refactoring hotspots

**Behavioural bugs / surprises**

1. **The reference travel time is never saved.** `reference_door_endstops_ms`
   is not in `save_config`, and the `DOOR` object starts with `None`. After
   every restart the first runner step sees no reference and **switches auto
   (or timer) mode off** in memory, until someone runs the reference sequence
   again.
2. `camera.Camera.get_frame()` uses `cv2`, which is only imported inside
   `__init__`, so it raises `NameError` on real hardware. `camera_task` only
   catches `RuntimeError`, so the camera thread dies (xfail test).
3. `DOOR.ErrorState(msg)` calls `stop(str(msg))`, so `door.state` (and the UI
   "state") becomes the error text instead of `stopped`.
4. Mode decisions take effect one step (0.5 s) late (see §7 step 6). The
   override-notification flag also needs two steps to re-arm.
5. The `stop` socket handler logs "Disabling auto/timer modes" but does not
   disable them, so the next auto/timer window moves the door again.
6. `/generate_204` and `/gen_204` are registered twice. The first handler
   wins and redirects to `http://10.42.0.1` without a trailing slash.
7. `POST /api/wifi-connect` without a JSON body returns 500 (`None.get`).
8. Files resolved against the **CWD**: `.subscriptions.json`, `version.txt`
   (read by `/version`) and `.secrets.yaml` (written by
   `generateVapidPair.py`). But `__main__` writes `version.txt`, and
   `load_notification_keys` reads `.secrets.yaml`, under `root_path`. They
   only line up when the app is started from the repository root (the systemd
   unit does this; `cron_script.sh` does not).
9. `subscribe()` passes the subscription as an extra argument to
   `logger.debug` without a `%s` placeholder, so logging prints a formatting
   error. Subscriptions are never de-duplicated.
10. The move budget counts iterations (`+0.5` per step), not wall-clock time.
    A blocking step makes the budget longer in real time.
11. `camera_task` treats a missing `enable_camera` (`None`) as enabled. Only
    an explicit `False` disables it.
12. Sunrise and sunset (and therefore `tu_open`/`tu_close`) are only filled in
    while auto mode runs. `get_valid_locations()` sorts inside its loop and is
    recomputed on every `/` request.

**Security** (the device is assumed to be on a trusted LAN)

* There is no authentication on any endpoint, including reboot, self-update
  (`git reset --hard @{u}`), system time, WiFi and GPIO config.
* `SECRET_KEY` is hard-coded. The default AP password is `password`.
  `/api/wifi-config` returns passwords in plain text. The VAPID `sub` claim is
  a placeholder e-mail.

**Structure** (candidates for the refactor)

* `app.py` mixes configuration, hardware selection, background tasks,
  business logic, Flask routes, socket handlers and startup. It has heavy
  import-time side effects.
* Duplicated sources of truth for pin defaults: `GPIO_DEFAULTS` (app),
  `door.py` module globals, and the fallbacks in `get_debug_data` and
  `temperature_task`. `WIFI_DEFAULTS` is duplicated in `wifi_manager.py`.
* Mutable module globals (`door.in1`, …, `app.boulder`, `app.timezone`,
  `app.vapid_private_key`) instead of injected configuration objects.
* Booleans are stored as strings (`"True"`/`"False"`) and temperatures as °F
  internally. `desired_door_state` is written from four places (UI, mode
  blocks, retry logic, reconciliation).
* `DoorTaskRunner.step()` is a single method of about 600 lines. The auto and
  timer mode blocks duplicate their logic, including the time-window and
  override-notification code.
* Hardware / mock selection is duplicated between `app.py` and `door.py`, and
  each of them reads `config.yaml` separately at import time.
* Legacy templates `index.html` and `index_optimized_door.html` are unused.
  `DOOR.open_then_stop` / `close_then_stop`, the `camera` global in `app.py`,
  and `consoleLogToFile` are unused.
