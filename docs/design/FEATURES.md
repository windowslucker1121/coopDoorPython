# Dinky Coop — Feature Inventory (design memory file)

Source of truth for the UI redesign. It lists **every function** of the web
app and what it needs: data, states, actions, constraints. It deliberately
says nothing about how things look today, so a new design can start fresh.
Screenshots of the current UI are in `current-ui/` for reference only.
Section 9 lists the problems found in the current UI.

> **Status:** implemented as layout F with the Slate & Amber palette
> (`src/templates/app.html`, `src/static/app/`). Every function listed here
> is covered by the browser tests in `tests/e2e/`. The old templates were
> removed; `current-ui/` keeps the screenshots of the v1 UI for comparison.

Captured 2026-09-25 from the running app (mock hardware + door simulator),
v2 backend. Screens covered: desktop 1440×900, mobile 390×844, light and
dark mode.

---

## 0. Product context

| | |
|---|---|
| **What** | Controller for a chicken-coop door on a Raspberry Pi: motorised door, 2 endstops, a physical 3-position override switch, indoor/outdoor climate sensors, an optional webcam. |
| **Who** | Hobby poultry keepers (1–3 people per household), mostly non-technical. A technical owner does setup and troubleshooting. |
| **Where / when** | Phone first: a quick check in the morning and evening ("is the door closed? are the chickens safe?"), often outdoors, in sunlight, one-handed, sometimes at night in the dark. Desktop and tablet for setup, data browsing and diagnostics. Installable as a PWA (home-screen app with push notifications). |
| **Network** | Home Wi-Fi, or the device's own hotspot (captive portal) during setup. Connections can be flaky and must reconnect gracefully. |
| **Core question the UI must answer in < 1 second** | Is the door open or closed, is everything OK, and what happens next (when does it open or close)? |
| **Safety** | Wrong door state means predators get in, or chickens are locked out. Errors must be impossible to miss. Destructive or system actions need confirmation. |

## 1. Live status (pushed by the server every 1 s over Socket.IO `data`)

| Field | Meaning | Values / format |
|---|---|---|
| `state` | Door state | `open`, `closed`, `opening`, `closing`, `stopped` |
| `door_position_estimate` | Door position | `0.0` (closed) … `1.0` (open); `-1` when unknown |
| `override` | Physical switch active | `off`, or the state it is driving |
| `errorstate` | Current fault text | `""` when OK, e.g. `"Endstop not reached"`, `"Auto-close failed: lower endstop triggered prematurely 5 times in a row."` |
| `auto_mode`, `timer_mode` | Active mode | `"True"`/`"False"`; at most one is true; both false = **manual** |
| `sunrise`, `sunset` | Today's sun times at the location | `"6:51:56 AM"` |
| `tu_open`, `tu_close` | Countdown to the next scheduled open / close | `"05:09:21"`, `"passed"`, `"disabled"` (manual mode) |
| `timer_open_time`, `timer_close_time` | Timer schedule | `"07:00"`, `"20:00"` (close may be earlier than open, meaning open overnight) |
| `reference_door_endstops_ms` | Measured door travel time | `"8158.59"` or `"Not set"` |
| `temp_in`, `hum_in`, `temp_out`, `hum_out`, `cpu_temp`, each with `_min` / `_max` | Climate + CPU; min/max reset daily | `"21.5°C"`, `"45.2%"`, `""` if missing |
| `uptime`, `cpu_percent`, `ram_used_mb`, `ram_total_mb`, `ram_percent`, `disk_used_gb`, `disk_total_gb`, `disk_percent`, `python_version` | System health | strings |
| `camera_enabled` | Webcam configured | `"True"`/`"False"` |
| `os_timestamp`, `os_time_local_str`, `time` | Device clock | Used to detect a device/phone time mismatch |

Other live streams: `camera` (base64 JPEG frames, about 10 fps) and `log`
(each new log line; the last 100 lines are replayed on connect).

## 2. Door control (the primary job)

### 2.1 Door status
* Shows state, position (animate the travel while moving), and **who is in
  control**: manual, auto (sun), timer, or physical switch (override).
* The five states need distinct, colour-independent cues (icon or shape plus
  label). `opening` and `closing` need a direction cue.
* Door state and next action must be visible without scrolling on a phone.

### 2.2 Manual commands
| Action | Socket event | Behaviour |
|---|---|---|
| Open | `open` | Switches the mode to **manual** (persisted) and drives the door up. |
| Close | `close` | Switches to manual and drives the door down. |
| Stop | `stop` | Switches to manual and stops immediately. **Must always be reachable while the door moves.** |

* Commands are ignored while the physical switch is active (override); show
  why.
* The motor is locked while a fault is active; the Open/Close buttons should
  reflect that.
* A manual command switches off auto/timer mode. The user must understand
  this (e.g. "Switch to manual?" hint or an inline notice).

### 2.3 Modes (exclusive: Manual / Auto / Timer)
| Mode | Settings | Events |
|---|---|---|
| **Auto (sun)** | sunrise offset, sunset offset in minutes (−720…720) | `toggle {toggle:bool}`, `auto_offsets {sunrise_offset, sunset_offset}` |
| **Timer** | open time, close time (HH:MM) | `toggle_timer {toggle:bool}`, `timer_times {open_time, close_time}` |
| **Manual** | none | switching auto or timer off, or any Open/Close/Stop command |

* Auto shows today's sunrise and sunset, and the resulting open/close times
  including offsets.
* Both schedule modes show **next event + countdown** ("Opens in 05:09" /
  "Closes at 20:00").
* Enabling a mode immediately moves the door to where the schedule wants it.
  Worth telling the user: "Door will close now — it's after sunset".
* Auto/timer need a completed reference run. Without one, the mode switches
  itself back to manual; the UI should block enabling it and point to
  calibration.
* Invalid input is rejected by the server. Validate in the UI too and show
  inline errors.

### 2.4 Calibration ("reference run")
* Event `reference_endstops`. The door fully closes, then fully opens, and
  the travel time is measured and stored. This takes up to 2 × 60 s.
* Required once after installation and after mechanical changes. While it is
  missing the app shows a **"Calibration needed"** state with a call to
  action.
* While it runs: show progress (closing, opening, done) and discourage other
  actions. Result: travel time (e.g. 8.2 s), or a fault on timeout.
* Setup-style flow: "Door will move fully down and up. Make sure nothing is
  in the doorway."

### 2.5 Faults and safety
* Fault causes: endstop not reached in time, reference timeout, 5×
  premature close (a chicken lifting the door), test error.
* Faults lock the motor. The UI needs a **prominent, persistent alert** with
  a plain-language explanation plus the technical text, and a **Clear
  error** action (`clear_error`), ideally with guidance ("Check the door is
  not blocked, then clear").
* Push notifications are sent for faults and for "Manual Override Active".
* Premature-close retries happen automatically (5 s cool-down). They could be
  surfaced as "Retrying close (2/5) — something is blocking the door".
  (Available via the debug data; not in the `data` payload yet.)

## 3. Climate

* Indoor (coop) and outdoor: temperature and humidity, current value plus
  today's min/max.
* Outdoor source is a DHT22 sensor or the Open-Meteo weather API (config).
* Values can be missing (sensor offline); show "—" and a sensor-offline
  hint, never fake numbers.
* Useful emphasis: frost warning (coop < 0 °C), heat (> 30 °C), high
  humidity (> 80 %). These are **new ideas**, not implemented yet.

## 4. History (data visualisation)

* Daily CSV files: `GET /api/csv` returns the list of days (newest first);
  `GET /api/csv/<file>` returns up to 600 downsampled rows `{time, temp_in,
  temp_out, hum_in, hum_out, cpu_temp, state, override, auto_mode,
  errorstate}`.
* Chart: temperature (°C) and humidity (%) on two axes, series toggles, and
  the door state as a background band (open/opening/closed/closing/stopped)
  over time.
* Pick a day, refresh, and see the number of data points.
* Opportunities: day/week range, "door opened 06:52, closed 19:14" event
  list.

## 5. Webcam

* Optional live MJPEG-like stream (Socket.IO `camera` frames). Only exists
  when `camera_enabled`. Otherwise hide it entirely; no grey placeholder.
* Useful next to the door status ("are all chickens inside?").
* Needs a fullscreen view, a loading state and an offline state.

## 6. Settings

### 6.1 Location (for sunrise/sunset)
* Search-select from about 385 predefined cities
  (`name "europe - berlin"`, region, timezone, lat, lon) **or** enter city,
  region, latitude, longitude and timezone manually. Event
  `update_location`.
* The server validates the timezone (IANA) and coordinate ranges.
* Show the resulting sunrise/sunset preview.

### 6.2 Device time
* A banner appears when the phone and device clocks differ: "Time mismatch
  detected, Fix".
* Set device time: a datetime input plus "use my phone's time", sent with
  `POST /api/system/time {time: "YYYY-MM-DD HH:MM:SS"}`.

### 6.3 Wi-Fi (currently on the debug page; belongs in Settings)
* Status: `GET /api/wifi-status` returns Ethernet connected, AP mode active,
  current SSID.
* Scan networks: `GET /api/wifi-scan` returns `[{ssid, signal 0–100,
  security}]`.
* Saved config: SSID, password (masked `********`), connection timeout; the
  fallback hotspot's SSID and password (8–63 chars). `GET/POST
  /api/wifi-config`.
* Actions: **Connect now** (`POST /api/wifi-connect`; on failure the device
  falls back to its hotspot after 5 s, so warn the user) and **Switch to
  hotspot now** (`POST /api/wifi-ap`). Both can disconnect the user; they
  need confirmation and an explanation of how to reconnect.

### 6.4 Hardware / GPIO (expert)
* Pin numbers (BCM 0–40, no duplicates): motor in1/in2/enable, endstop
  up/down, override open/close, DHT11 data, DHT22 data, DHT22 power
  (optional).
* Invert upper/lower endstop (applied immediately), reference timeout 5–600
  s (applied immediately). Pin changes need a restart.
* `GET/POST /api/gpio-config`; the server returns combined validation
  errors.

### 6.5 Security
* Optional web password (HTTP Basic auth, set via CLI today). A settings UI
  for it would be new.

### 6.6 Appearance and app
* Light / dark / system theme.
* Install as app (PWA install prompt).
* Enable push notifications (browser permission + `POST /subscribe`).
  Currently this happens silently on page load; it should be an explicit
  opt-in with status.

## 7. System and maintenance

| Function | API | Notes |
|---|---|---|
| System info | from `data` | uptime, CPU load, CPU temp (+min/max), RAM, disk, Python version, git version (`GET /version`) |
| Update software | `POST /update` | Pulls the latest code and restarts (about 15 s offline). Needs confirmation and a "reconnecting…" state; afterwards show the new version. |
| Reboot device | `POST /api/restart` | Needs confirmation and a reconnecting state. |
| Log viewer | `GET /api/logs`, `GET /api/logs/<file>` | File picker (today + 30 days), text search, logger filter, level chips (ALL/DEBUG/INFO/WARN/ERROR/CRIT), sort newest/oldest, auto-refresh (3 s). Rows: time, level, logger, message. |
| Health | `GET /api/health` | Worker liveness (door, environment, broadcast, csv-log, camera, simulator); 503 if a worker died. New, not shown yet. |
| Diagnostics (expert) | Socket `get_debug_data` → `debug_data` (poll 1 s) | Live GPIO pin table (pin, name, purpose, direction, HIGH/LOW); door constants; all state variables; system info; threads/workers; log buffer. |
| Simulator (dev only) | `/mock`; `mock_trigger_pin`, `mock_get_outputs` | Only with mock hardware: hold open/close switch, toggle endstops, see motor outputs. |
| Test error | `generate_error` | Developer tool; belongs in diagnostics, not the main UI. |

## 8. Cross-cutting requirements

* **Connection state:** live / reconnecting / offline (the service worker
  serves an offline page). Data older than a few seconds must look stale.
* **Feedback:** every command needs immediate acknowledgement (pressed,
  sending, confirmed by a state change) and an error toast.
* **Accessibility:**
  * WCAG AA contrast, including in bright sunlight.
  * Touch targets ≥ 44 px.
  * Colour is never the only signal.
  * Respects `prefers-reduced-motion`.
* **Performance:** runs on a Raspberry Pi and is viewed on older phones. No
  heavy frameworks; Chart.js and Socket.IO are already bundled.
* **Localisation-ready** (English now). Times follow the location's
  timezone.
* **Roles** (not implemented): everyday "keeper" functions vs. "admin"
  functions (Wi-Fi, GPIO, update, diagnostics). The IA should separate them
  even without real roles.

## 9. Problems in the current UI (from the screenshots)

1. **No hierarchy:** the webcam (often empty, a grey box) is the first and
   largest card, and on mobile it fills the whole first screen. The door
   status sits below the fold.
2. **Door state label is almost invisible:** dark text on the dark "sky"
   panel (e.g. "OPEN" is barely readable).
3. **Top bar collides with the page title on mobile.** Admin tools (Debug,
   Mock, Update) sit next to everyday ones (Logs, Dark Mode). "Mock" shows
   even when disabled.
4. **Mode UI:** two independent toggles for mutually exclusive modes. Their
   settings appear only when switched on; there is no "manual" state
   indicator and no explanation that Open/Close switches the mode off.
5. **Buttons:** Open (green), Stop (red), Close (grey) are equal in weight,
   with ambiguous icons (Open and Close use the same icon). No disabled or
   feedback states.
6. **Chart:**
   * The legend says "°F" but the data is °C.
   * The door-state bands form a legend-like row detached from the chart.
   * Dense gridlines; dark mode has harsh black gridlines.
7. **Error card** is just another collapsible card among the others; the
   error text is technical, with no guidance.
8. **Calibration** appears as a warning banner inside the door card with no
   explanation of what will happen.
9. **Settings are scattered:** location at the bottom of the dashboard,
   system time hidden in the System card, Wi-Fi and GPIO on the debug page.
10. **Debug page** is a long unstyled stack (GPIO table, config forms,
    global variables dump, threads, log buffer). Wi-Fi setup is buried
    there.
11. **Log viewer** is a modal crammed into the dashboard; level chips have
    low contrast.
12. **Emoji icons** everywhere (inconsistent across platforms), and
    collapsible "▼" on every card, including the ones you always need.
13. **No connection-state indicator,** no toasts or feedback, and the push
    permission is requested silently.
14. **Time-mismatch banner** is a loud orange block that pushes everything
    down; its fix is hidden inside the System card.
15. **Mock panel** is an unstyled developer page.

## 10. Screen inventory (for the new IA)

| Current screen | Content |
|---|---|
| `/` Dashboard | top bar, banners (install, time mismatch, error), webcam, system, door control (reference banner, door visual, Open/Stop/Close, auto/timer toggles + settings, sun times, countdowns), temperature, data visualisation, location settings, log viewer modal |
| `/debug` | GPIO pin states, door configuration constants, GPIO config form, Wi-Fi config + scan + connect + AP, global variables, system info, threads, log buffer |
| `/mock` | switch buttons, endstop toggles, motor outputs |

Screenshots in `current-ui/`: `01`–`02` initial and cards, `03`–`10` door
states and modes, `11` error, `12` system time, `13` location, `14` chart,
`15`–`17` log viewer, `18`–`19` full page light/dark, `20`–`21` mobile,
`22` time mismatch, `23`–`24` debug, `25` mock.
