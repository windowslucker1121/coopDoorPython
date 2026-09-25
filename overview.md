# Dinky Coop — Overview (v2)

Raspberry Pi controller for a chicken-coop door with a web dashboard. This
document describes the architecture of the backend (`src/coop/`) and the
single-page web app (`src/templates/app.html`, `src/static/app/`,
`src/sw.js`). The design brief and the list of every UI function are in
`docs/design/FEATURES.md`.

---

## 1. What it does

* Opens and closes the door with a DC motor through an H-bridge. Two endstops
  mark fully open and fully closed.
* **Modes:**
  * **auto:** sunrise / sunset at the configured location, with offsets.
  * **timer:** fixed daily times; the door may stay open overnight.
  * **manual:** UI commands and the physical 3-position override switch.
* **Supervision:**
  * **Move budget:** an endstop must be reached within the reference travel
    time + 20 s.
  * **Premature-close detection:** a chicken lifting the door while it
    closes. The door stops, retries, and after 5 consecutive false closes
    raises a fault.
  * **Faults** stop the motor and send a Web Push notification.
* Indoor and outdoor temperature/humidity (DHT11, DHT22 or Open-Meteo) plus
  CPU temperature, with a spike filter and daily min/max.
* Live dashboard over Socket.IO, CSV data logging, a log viewer, and an
  optional webcam stream.
* Wi-Fi management (NetworkManager): joins the configured network or falls
  back to a hotspot with a captive portal.
* Self-update and **release channels**: update the installed branch, or
  switch between the stable branch (`main`) and any dev branch on `origin`
  from the web UI; plus reboot and setting the system time.
* Optional HTTP Basic authentication.
* A complete **mock mode with a door simulator**, so the application runs on
  any computer.

---

## 2. Package layout

```
src/
├── app.py                    entry point: gevent monkey-patch → coop CLI "run"
├── update_script.py          detached self-update helper (kill → git update / branch switch → pip → restart)
├── generateVapidPair.py      creates VAPID keys in <root>/.secrets.yaml
├── generateIcons.py          renders the PWA icons
└── coop/
    ├── __main__.py           CLI: run | set-password | disable-auth | check-config
    ├── main.py               server start-up (monkey-patch, logging, Application)
    ├── application.py        composition root: builds & wires everything, workers
    ├── config.py             typed frozen settings, validation, ConfigStore (atomic YAML)
    ├── clock.py              Clock (monotonic + location wall time) / FakeClock
    ├── paths.py              all file locations (repo-root based, never the CWD)
    ├── workers.py            Worker: exception-safe periodic background job
    ├── logging_setup.py      console + rotating app.log + LogBuffer (→ UI)
    ├── hardware/
    │   ├── gpio.py           GpioBackend protocol, RpiGpio adapter, MockGpio
    │   ├── sensors.py        Reading (°C, %RH), DHT11/22, Open-Meteo, CPU, mocks
    │   ├── camera.py         OpenCvCamera, MockCamera
    │   ├── simulator.py      DoorSimulator (moves a virtual door on MockGpio)
    │   └── __init__.py       build_hardware(): real vs. mock selection
    ├── door/
    │   ├── model.py          DoorState, DesiredState, DoorStatus (immutable snapshot)
    │   ├── driver.py         DoorDriver: motor, endstops, override switch, reference
    │   ├── schedule.py       TimerSchedule, SunSchedule, desired_at, crossed_boundary
    │   └── controller.py     DoorController: the control loop
    ├── services/
    │   ├── environment.py    EnvironmentMonitor + MetricTracker (spike filter, min/max)
    │   ├── notifications.py  SubscriptionStore, PushNotifier (async), VAPID keys
    │   ├── datalog.py        CSV logger + CSV / app-log viewers
    │   ├── wifi.py           WifiManager (nmcli), boot watchdog
    │   ├── system.py         uptime/metrics, version, set time, reboot, update, release branches
    │   └── sun.py            SunCalculator (cached), astral location list
    └── web/
        ├── __init__.py       create_web(app) → Flask + Socket.IO
        ├── routes.py         HTTP routes
        ├── sockets.py        Socket.IO handlers
        ├── security.py       Basic auth, captive portal
        └── payloads.py       dashboard / debug payloads (frontend contract)
```

**Dependency direction:** `web → application → door / services → hardware →
config / clock`. Nothing below `web` imports Flask, and no module keeps
mutable global state. Every collaborator is passed to its constructor, which
is how the tests run the real application against mocks and a fake clock.

---

## 3. Runtime

```
src/app.py ──► gevent.monkey.patch_all() ──► coop.main.run_server()
                 configure_logging → Application(paths) → create_web → start workers → socketio.run

Workers (greenlets):
  door            DoorController.step()      every 0.1 s while moving, 0.5 s idle
  environment     EnvironmentMonitor.poll()  every 2.5 s
  broadcast       emit('data', payload)      every 1 s
  csv-log         CsvDataLogger.write_row()  every 5 s          (if csvLog)
  camera          CameraStreamer.step()      every 0.1 s        (if enable_camera)
  door-simulator  DoorSimulator.tick()       every 0.05 s       (mock hardware)
  wifi-watchdog   run_wifi_watchdog()        once, 15 s after start (real hardware)
```

* A `Worker` catches every exception, logs it, backs off 5 s and keeps
  running. If the door worker crashes, its error handler stops the motor.
  `/api/health` and the debug page show whether each worker is alive and its
  error count.
* **GPIO edge callbacks run in RPi.GPIO's native thread**, outside gevent.
  The driver's callback only cuts the motor pins and records which endstop
  was reached; it takes no locks and does no logging. The control loop
  applies the state change. The manual switch is polled by the loop, so it
  needs no callback.
* **Time:** durations (motor budget, retries, travel time) use a monotonic
  clock, so changing the system time cannot affect them. Schedules use
  wall-clock time in the *location's* timezone, independent of the Pi's own
  timezone.

---

## 4. Configuration

`config.yaml` in the repository root, managed by `ConfigStore`:

* **Typed and immutable:** `Settings` is a frozen dataclass with nested
  `GpioConfig`, `WifiConfig`, `LocationConfig` and `AuthConfig`. Any thread
  can safely use a snapshot.
* **Validated updates:** `update()` validates the new snapshot, writes it
  atomically (temporary file, fsync, `os.replace`), and only then publishes
  it and notifies subscribers. A failed validation or write changes nothing.
* **Lenient loading:** an invalid value is logged and replaced by its
  default; an empty or corrupt file is replaced by defaults; unknown keys are
  kept. A GPIO layout that fails validation is kept with a warning, so pins
  are never moved silently. Sections are only re-validated when they change,
  so a legacy value such as a short AP password never blocks unrelated
  updates.
* **Backward compatible:** reads and writes the original keys
  (`auto_mode`/`timer_mode`, `csvLog`, `reference_door_endstops_ms`, string
  booleans). Internally the two flags are one `Mode` enum
  (`manual|auto|timer`), which makes invalid combinations impossible.

```yaml
use_mock_hardware: false     # true → mock GPIO, simulated sensors
simulate_door: true          # with mock hardware: simulate the physical door
simulator_travel_s: 8        # simulated door travel time (1-120 s)
auto_mode: true              # auto_mode / timer_mode → Mode
timer_mode: false
timer_open_time: '07:00'
timer_close_time: '20:00'    # may be earlier than open (open overnight)
sunrise_offset: 0            # minutes, ±720
sunset_offset: 0
location: {city: Boulder, region: USA, timezone: America/Denver, latitude: 40.01499, longitude: -105.27055}
csvLog: true
enable_camera: false
camera_index: 0
outdoor_sensor_type: dht22   # or "api" (Open-Meteo)
gpio: {motor_in1: 17, motor_in2: 27, motor_ena: 22, endstop_up: 23, endstop_down: 24,
       override_open: 5, override_close: 6, dht11_data: 26, dht22_data: 21, dht22_power: 20,
       invert_end_up: false, invert_end_down: false, reference_timeout: 60}
wifi: {ssid: '', password: '', timeout: 60, ap_ssid: DINKY-COOP, ap_password: password,
       ap_ip: 10.42.0.1, ap_allowed_hosts: []}
auth: {username: admin, password_hash: ''}   # set with: python -m coop set-password
reference_door_endstops_ms: null             # measured by the reference run
log_level: INFO
```

Settings changes that apply at runtime: mode, times, offsets, location, invert
flags, reference timeout, Wi-Fi credentials, auth, log level. Pin numbers,
sensor type, camera and CSV logging take effect after a restart.

---

## 5. Door control

### 5.1 Driver (`door/driver.py`)

| action | in1 | in2 | ena |
|---|---|---|---|
| open | LOW | LOW | HIGH |
| close | HIGH | HIGH | HIGH |
| stop | LOW | LOW | LOW |

* **States:** `stopped`, `opening`, `closing`, `open`, `closed`. A *fault*
  stops the motor and locks it until it is cleared.
* **`sync()`**, called by every control step:
  * Applies the stop recorded by the edge callback.
  * Polls the endstops as a safety net. The upper endstop is ignored while
    closing and the lower one while opening.
  * Handles the override switch: exactly one input active means drive that
    way; neutral means stop where the door is.
* **`reference()`** (blocking) closes to the lower endstop, then opens to the
  upper one and measures the travel time. It works from any start position,
  confirms each endstop twice 0.1 s apart (contact bounce), applies
  `reference_timeout` per direction, and refuses if both endstops read
  active (wiring fault).

### 5.2 Controller (`door/controller.py`)

Other threads only call `command()`, `request_reference()`, `clear_error()`
and `inject_test_error()`, which queue commands under a lock, and read the
immutable `status`. One `step()`:

1. Take the queued commands: test error, clear error, **reference run**
   (saves the travel time, then the door stays open), manual desired state.
2. `driver.sync()`.
3. **Fault:** notify once, reset bookkeeping, publish, and stop here.
4. **Mode:** auto or timer without a reference switches to manual (saved).
   Switching a mode on requests a **re-sync**.
5. **Schedule:**
   * **Re-sync** (at boot, when a mode is switched on, after an error is
     cleared, or after a wall-clock jump of more than 6 h): the door goes to
     the position the schedule expects right now.
   * **Otherwise** only a **boundary crossing** since the last step (open or
     close time passed) changes the target. A slow step can never skip a
     transition, and manual commands between transitions are respected.
   * A close crossing is ignored while a premature-close retry is pending.
   * With the override switch active, the schedule instead sends one
     "Manual Override Active" notification.
6. **Premature-close check** and **retry** (auto/timer mode, no override):
   * **Detection:** a close (or a just-fired retry) that ends at the lower
     endstop after a total drive time, summed over all attempts, of less than
     `0.8 × reference`.
   * **Retry:** stop, wait 5 s, close again. The retry is forced even if the
     endstop is still active, and then counts as another attempt.
   * **Fault:** raised after 5 consecutive false closes.
7. **Drive** towards the desired state, with the **move budget** (monotonic
   time). A door stopped at an endstop reconciles the desired state.
8. Update the position estimate (known at the endstops, integrated from the
   travel time in between, `None` when unknown) and publish `DoorStatus`.

---

## 6. External interfaces

**HTTP:**

| Group | Routes |
|---|---|
| Pages and assets | `/` (the app), `/debug` and `/mock` (redirect to `/#/hardware`; `/mock` is 403 on real hardware), `/favicon.ico`, `/manifest.json`, `/sw.js`, `/static/*` |
| Push and version | `/version`, `POST /subscribe` |
| Log and data viewers | `/api/logs[/<f>]`, `/api/csv[/<f>]` |
| Configuration | `/api/gpio-config` (GET/POST), `/api/wifi-config` (GET/POST; passwords are masked as `********`, which is ignored on save) |
| Wi-Fi | `/api/wifi-status`, `/api/wifi-scan`, `POST /api/wifi-ap`, `POST /api/wifi-connect` |
| System | `POST /api/system/time`, `POST /api/restart`, `POST /update` (optional `{"branch": …}`) |
| Releases | `GET /api/update/info[?check=1]`, `GET /api/update/branches` (see *Updates and release channels* below) |
| Captive-portal probes | `/generate_204`, `/gen_204`, `/hotspot-detect.html`, `/success.html` |
| Status | `GET /api/status` (dashboard payload), `GET /api/health` (worker liveness, 503 if a worker died), `GET /api/settings` (mode, offsets, timer, location, flags - no secrets), `GET /api/locations` (city list for the location search) |

`/api/*` responses are never cached.

### Updates and release channels

The controller runs from a git checkout. `SystemService` (in
`services/system.py`) reads it with `git` in the code checkout (the parent
of `src/`, not `COOP_ROOT`), always with list arguments, a timeout and
`GIT_TERMINAL_PROMPT=0`:

* `GET /api/update/info` - `{branch, detached, channel ("stable" | "dev"),
  commit, commit_date, subject, upstream, behind, checked, supported,
  stable_branch, git, error}`. `?check=1` first fetches the upstream branch so
  `behind` (commits waiting) is current; the UI asks without, then with it.
* `GET /api/update/branches` - `git fetch --prune origin`, then the
  `origin/*` branches newest first: `{branches: [{name, commit, date,
  subject, current, stable}], current, stable_branch, refreshed, warning,
  error}`. A failed or timed-out fetch is not an error: the last known
  remote-tracking refs are returned with a `warning`. Without git or a
  repository the list is empty and `error` says why (HTTP 200).
* `POST /update` - no body: update the installed branch from its upstream
  (the original behaviour; refused on a detached HEAD). `{"branch": name}`:
  switch to `origin/<name>`; "Switch to stable" is `{"branch": "main"}`.
  The name must pass `is_safe_branch_name` (ASCII words separated by single
  slashes; no leading `-` or `.`, no `..`, spaces, control or git special
  characters) **and** be in the freshly fetched branch list, otherwise 400.
  Mock hardware refuses both forms with 400 (browsing works everywhere).

`update_script.py [--branch NAME] <app_entrypoint> <pid> [service]` does the
work after the app has exited: remove empty git objects, then either
`fetch --all` / `reset --hard @{u}` / `pull`, or for a branch
`fetch origin +refs/heads/NAME:refs/remotes/origin/NAME`,
`checkout -f -B NAME origin/NAME`, `branch --set-upstream-to=origin/NAME`,
`reset --hard origin/NAME`. If `requirements.txt` differs between the old
and new commit it runs `python -m pip install -r requirements.txt` with the
same interpreter. Git or pip failures are logged and the app is restarted
regardless (systemd service or direct relaunch). The helper re-checks the
branch name itself and leaves the checkout alone if it is unsafe.

On the **System & updates** page the *Updates & power* card shows the channel
(Stable / Dev), the branch (or "detached at <hash>"), commit and date, and
"Update available" when the upstream is ahead. *Switch to dev release* opens
a filterable branch picker; choosing a branch shows a confirmation (with an
instability warning for dev branches), posts `/update` and waits for the
controller to come back. On a dev branch, *Switch to stable (main)* returns.

**Socket.IO:**

| Direction | Events |
|---|---|
| Client → server: door | `open`, `close` and `stop` (each switches to manual mode, saved), `reference_endstops`, `clear_error`, `generate_error` |
| Client → server: settings (validated, saved) | `set_mode {mode}`, `toggle`, `toggle_timer`, `timer_times`, `auto_offsets`, `update_location` |
| Client → server: data requests (answered to the requesting client) | `get_csv_data`, `get_debug_data`, `mock_trigger_pin`, `mock_get_outputs` |
| Server → client | `data` (every 1 s), `log`, `camera`, `csv_data`, `debug_data`, `mock_update_outputs` |

Every command and settings event answers with an acknowledgement
`{"ok": true}` or `{"ok": false, "error": "…"}`; the app shows the error
text. Sun and Timer mode are refused until the door is calibrated.

The dashboard payload (`web/payloads.py`) keeps every original key and
format (temperatures as `"21.5°C"`, `auto_mode`/`timer_mode` as
`"True"`/`"False"`, …) and adds typed fields for the app: `mode`,
`open_time`/`close_time`, `door_desired`, `override_active`,
`retry_pending`/`retry_count`/`retry_max`, `reference_running`,
`hardware_mock` and `events` (the controller's last 12 door events, newest
first: `{ts, time, kind, text}`, e.g. "Door opened by the sun schedule").

**Security:**
* **HTTP Basic auth** is optional; enable it with
  `python -m coop set-password`. It also guards the Socket.IO connection.
  Captive-portal probes, the PWA shell and static files stay public.
* **Captive portal:** in AP mode every foreign `Host` is redirected to the
  portal. AP mode is detected from NetworkManager's wireless *mode*, never
  from the connection name.
* The Flask secret key is random per process, and passwords are never
  returned by the API.

---

## 7. Web app

A dependency-free single-page app (no build step): `templates/app.html` is
the shell, `static/app/app.css` the Slate & Amber design (light and dark
tokens, self-hosted Bricolage Grotesque + Figtree), `static/app/app.js` the
code. Chart.js and the Socket.IO client are vendored in `static/js/`.

* **Layout:** sidebar (Coop / Setup / Device) on wide screens; below 1024 px
  a bottom tab bar (Home, Climate, History, More). Hash routes
  (`#/schedule`, …) so every page can be bookmarked.
* **Pages** are objects with `render()`, `enter(root)`, `leave()` and
  `update(data, root)`. Each visit renders into a fresh container, so work
  that finishes after the user moved on only touches a detached node.
* **Live data** arrives on the `data` event every second; commands use
  Socket.IO acknowledgements, settings with REST endpoints use `fetch`.
* **Live internals** (`#/internals`) polls `get_debug_data` every second,
  twice a second while the motor runs and immediately when the pushed door
  status changes, and draws it: the state machine (current and target
  state), motor/endstop/switch "LEDs", position and motor-run budget, retry
  count, workers, sensors, system and the filterable configuration. Changed
  values flash. The `live` block of the debug payload comes from
  `DoorController.diagnostics()`.
* **Use my location** (Schedule): `navigator.geolocation` on secure
  origins, the nearest known city within 50 km names the place; otherwise
  (plain http, blocked, unavailable) the city is guessed from the browser's
  time zone. Zones are compared by their winter/summer UTC offsets because
  astral lists legacy names such as `US/Central`.
* **Banners** (fault, calibrate, calibrating, switch override, blocked
  door, clock mismatch, offline) appear on every page with a fixing action.
* **Service worker:** network-first for the page with the cached shell or
  `offline.html` as fallback, stale-while-revalidate for `/static`, never
  caches `/api` or Socket.IO; also shows push notifications.

---

## 8. Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -m "not e2e"                      # 454 unit tests, ~6 s
pytest tests/e2e                         # 57 browser tests, ~2 min (Playwright + Chromium)
pytest --cov=src                         # ≈92 % (coop package ≈95 %)
echo "use_mock_hardware: true" > config.yaml && python src/app.py   # full app with door simulator
```

| Test file | Covers |
|---|---|
| `test_controller.py` | Control loop: manual, budget, faults, reference, schedules (re-sync, crossings, overnight, clock jumps, polar night), override, all premature-close scenarios, status |
| `test_driver.py` | GPIO driver: outputs, endstops (edge + poll), invert, switch, faults, reference legs and bounce |
| `test_schedule.py` | Open periods, wrap-around, crossings, sun times and offsets, polar |
| `test_config.py` | Parsers, sections, YAML compatibility, lenient loading, atomic store, listeners |
| `test_hardware.py` | MockGpio, RpiGpio adapter, DHT (retry, re-init, power), Open-Meteo, CPU, camera, simulator, factory |
| `test_services.py` | Environment monitor, notifications, CSV and log viewers, system service, release branches (parsing, name validation, info, switching), workers, logging |
| `test_wifi.py` | nmcli parsing, AP detection, actions, watchdog |
| `test_web_http.py` / `test_web_sockets.py` / `test_payloads.py` | The complete frontend contract, auth, captive portal |
| `test_application.py` | Wiring, workers, camera, CLI, and an **end-to-end day** on the simulator with a fake clock |
| `test_door_events.py` | The controller's event feed and how events are attributed (manual, switch, sun, timer) |
| `e2e/test_e2e_door.py` | In a real browser against the real server: navigation, door commands, calibration, modes, fault / override / clock / offline banners, simulator panel |
| `e2e/test_e2e_live.py` | Live internals (updates without interaction, follows a door move, fault, switch, pause, filter, copy) and every Use-my-location path |
| `e2e/test_e2e_pages.py` | Every settings form (valid and invalid input), charts, camera, network, pins, logs filters, system actions, theme, service worker, phone layout, dark mode |
| `e2e/test_e2e_update.py` | Release card, branch picker (filter, cancel, stable / dev confirmations), refusal on mock hardware, phone layout, stubbed update-available / detached / git-missing states |

**Extending:**
* New sensor: subclass `TemperatureSensor` and select it in `build_hardware`.
* New schedule type: implement `window(day, tz)` and return it from
  `DoorController._schedule`.
* New background job: return a `Worker` from `Application.build_workers`.

---

## 9. Changes compared to v1

* **Structure:**
  * The 1,700-line `app.py`, the `protected_dict` global store and the
    mutable module globals are replaced by the layered `coop` package, with
    dependency injection and immutable settings and status snapshots.
  * The 600-line `step()` is split into focused methods.
* **Door behaviour:**
  * Monotonic-time motor budget (was iteration-counted).
  * Schedule boundary crossings instead of 1-minute windows re-applied every
    0.5 s.
  * 0.1 s control interval while the motor runs.
  * Edge callbacks are safe in native threads.
  * The door stays open after a reference run (v1 drove it back down).
  * The position estimate is `None` until known (v1 integrated from an
    assumed 0).
* **Measurements:** sensors report °C and the spike filter uses Celsius
  thresholds (3 °C temperature, 5 %RH humidity, 5 °C CPU). The CPU
  temperature is read from sysfs, so gpiozero is no longer needed.
* **Web:**
  * Log replay and debug data are sent only to the requesting client (v1
    broadcast them to every client).
  * Wi-Fi passwords are masked.
  * New: optional Basic auth, `/api/status`, `/api/health`, and the
    `python -m coop` CLI.
* **Web app:** the five v1 templates (dashboard, door page, debug and mock
  panels) are replaced by one single-page app with every function in one
  place, light and dark themes, a phone layout, human-readable error help
  and a guided first calibration. Sun and Timer mode now require a
  calibrated door.
* **Operations:**
  * Mock mode works on any OS (v1: Windows only) and includes a physical door
    simulator.
  * Startup warnings are logged.
  * Unused `eventlet`, `gpiozero` and `colorzero` were removed from
    `requirements.txt`.
* **All fixes from the v1 review** are kept: persisted reference, location
  timezone, overnight timer, CSV quoting, atomic writes, validation, and
  AP-mode detection.
