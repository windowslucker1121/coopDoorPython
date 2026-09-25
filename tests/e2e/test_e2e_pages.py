"""E2E: every settings / information page and the phone layout."""

from __future__ import annotations

import re

from playwright.sync_api import expect

MASK = "********"


# ── climate / history / camera ──────────────────────────────────────────────

def test_home_tiles_and_camera_thumbnail(ui):
    page = ui.goto("home")
    expect(page.locator("[data-bind=temp_in]").first).to_have_text(re.compile(r"^-?\d+\.\d°$"))
    expect(page.locator("[data-bind=hum_out]").first).to_have_text(re.compile(r"^\d+\.\d %$"))
    thumb = page.locator("[data-cam-thumb]")
    expect(thumb).to_be_visible()
    expect(thumb.locator("[data-cam-img]")).to_have_attribute("src", re.compile(r"^data:image/jpeg;base64,"))
    thumb.click()
    expect(page.locator("#page-title")).to_have_text("Camera")


def test_climate_page(ui):
    page = ui.goto("climate")
    for key in ("temp_in", "temp_out", "cpu_temp", "temp_in_min", "temp_in_max"):
        expect(page.locator(f"[data-bind={key}]")).to_have_text(re.compile(r"^-?\d+\.\d°$"))
    expect(page.locator("[data-chart-info]")).to_have_text(re.compile(r"^\d+ readings$"), timeout=10000)
    n = page.evaluate("() => Chart.getChart(document.querySelector('[data-chart]')).data.datasets.length")
    assert n == 2


def test_history_page(ui):
    page = ui.goto("history")
    expect(page.locator("[data-info]")).to_have_text(re.compile(r"^\d+ data points$"), timeout=10000)
    expect(page.locator("[data-day] option").first).to_have_text(re.compile(r"^\d{4}-\d\d-\d\d$"))
    datasets = "() => Chart.getChart(document.querySelector('[data-chart]')).data.datasets.map(d => d.label)"
    assert page.evaluate(datasets) == ["Coop °C", "Outside °C", "Coop humidity %", "Outside humidity %"]
    page.locator("[data-series] [data-key=cpu_temp]").click()
    expect(page.locator("[data-series] [data-key=cpu_temp]")).to_have_attribute("aria-pressed", "true")
    assert "CPU °C" in page.evaluate(datasets)
    page.locator("[data-series] [data-key=temp_in]").click()
    assert "Coop °C" not in page.evaluate(datasets)
    page.locator("[data-refresh]").click()
    expect(page.locator("[data-info]")).to_have_text(re.compile(r"^\d+ data points$"))
    # door events recorded since the start
    ui.page.evaluate("() => window.__coop.ack('open')")
    expect(page.locator("[data-events]")).to_contain_text("Door opened manually", timeout=10000)


def test_camera_page(ui):
    page = ui.goto("camera")
    expect(page.locator("[data-cam-img]")).to_be_visible()
    expect(page.locator("[data-cam-img]")).to_have_attribute("src", re.compile(r"^data:image/jpeg;base64,/9j/"))
    expect(page.locator("[data-cam-status]")).to_have_text(re.compile(r"^Live · \d+ fps$"))
    expect(page.locator("[data-cam-off]")).to_be_hidden()
    page.locator("[data-fullscreen]").click()
    page.wait_for_function("() => document.fullscreenElement !== null")
    page.evaluate("() => document.exitFullscreen()")


# ── schedule & location ──────────────────────────────────────────────────────

def test_sun_offsets(ui, server):
    page = ui.goto("schedule")
    form = page.locator("[data-offsets]")
    form.locator("[name=sunrise_offset]").fill("15")
    form.locator("[name=sunset_offset]").fill("-10")
    expect(page.locator("[data-sun-preview]")).to_contain_text(re.compile(r"sunrise \d\d:\d\d → opens 15 min later"))
    expect(page.locator("[data-sun-preview]")).to_contain_text("closes 10 min earlier")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_have_text("Saved")
    s = server.get("/api/settings")
    assert (s["sunrise_offset"], s["sunset_offset"]) == (15, -10)

    form.locator("[name=sunrise_offset]").fill("9999")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_contain_text("between -720 and 720")
    expect(form.locator("[name=sunrise_offset]")).to_have_attribute("aria-invalid", "true")
    assert server.get("/api/settings")["sunrise_offset"] == 15


def test_timer_times(ui, server):
    page = ui.goto("schedule")
    form = page.locator("[data-timer]")
    form.locator("[name=open_time]").fill("06:15")
    form.locator("[name=close_time]").fill("21:45")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_have_text("Saved")
    s = server.get("/api/settings")
    assert (s["timer_open_time"], s["timer_close_time"]) == ("06:15", "21:45")
    form.locator("[name=close_time]").fill("")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_have_text("Please enter both times.")


def test_location_search_and_save(ui, server):
    page = ui.goto("schedule")
    page.locator("[data-loc-search]").fill("berl")
    results = page.locator("[data-loc-results]")
    expect(results).to_be_visible()
    results.locator("button", has_text="Berlin").first.click()
    form = page.locator("[data-location]")
    expect(form.locator("[name=city]")).to_have_value("Berlin")
    expect(form.locator("[name=timezone]")).to_have_value("Europe/Berlin")
    expect(form.locator("[data-msg]")).to_contain_text("Berlin selected")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_have_text("Location saved")
    expect(page.locator("[data-loc-current]")).to_have_text(re.compile(r"^Berlin"))
    assert server.get("/api/settings")["location"]["timezone"] == "Europe/Berlin"

    page.locator("[data-loc-search]").fill("zzqq")
    expect(results).to_contain_text("No match")


def test_location_manual_entry_is_validated(ui, server):
    page = ui.goto("schedule")
    form = page.locator("[data-location]")
    form.locator("[name=latitude]").fill("123")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_have_text("Please check the highlighted fields.")
    expect(form.locator("[name=latitude]")).to_have_attribute("aria-invalid", "true")
    form.locator("[name=latitude]").fill("48.1")
    form.locator("[name=longitude]").fill("11.6")
    form.locator("[name=city]").fill("Munich")
    form.locator("[name=timezone]").fill("Mars/Olympus")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_contain_text("imezone")
    assert server.get("/api/settings")["location"]["city"] != "Munich"
    form.locator("[name=timezone]").fill("Europe/Berlin")
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_have_text("Location saved")
    assert server.get("/api/settings")["location"]["city"] == "Munich"


# ── network ──────────────────────────────────────────────────────────────────

def test_network_status_and_scan(ui):
    page = ui.goto("network")
    expect(page.locator("[data-status]")).to_contain_text("Mock-WiFi")
    expect(page.locator("[data-status]")).to_contain_text("Hotspot")
    page.locator("[data-scan]").click()
    rows = page.locator("[data-networks] .list-row")
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text("My-Home-WiFi")
    rows.first.locator("[data-use]").click()
    wifi = page.locator("[data-wifi]")
    expect(wifi.locator("[name=ssid]")).to_have_value("My-Home-WiFi")
    expect(wifi.locator("[name=password]")).to_be_focused()
    page.locator("[data-status-refresh]").click()
    expect(page.locator("[data-status]")).to_contain_text("Mock-WiFi")


def test_wifi_save_keeps_the_saved_password(ui, server):
    page = ui.goto("network")
    wifi = page.locator("[data-wifi]")
    wifi.locator("[name=ssid]").fill("Barn")
    wifi.locator("[name=password]").fill("supersecret")
    wifi.locator("[name=timeout]").fill("45")
    wifi.locator("button[type=submit]").click()
    expect(wifi.locator("[data-msg]")).to_have_text("Saved")
    cfg = server.get("/api/wifi-config")
    assert (cfg["ssid"], cfg["password"], cfg["timeout"]) == ("Barn", MASK, 45)
    expect(wifi.locator("[name=password]")).to_have_attribute("placeholder", "Saved - leave empty to keep")
    # save again without typing the password → still saved
    wifi.locator("[name=timeout]").fill("50")
    wifi.locator("button[type=submit]").click()
    expect(wifi.locator("[data-msg]")).to_have_text("Saved")
    cfg = server.get("/api/wifi-config")
    assert (cfg["password"], cfg["timeout"]) == (MASK, 50)
    wifi.locator("[name=timeout]").fill("0")
    wifi.locator("button[type=submit]").click()
    expect(wifi.locator("[data-msg]")).to_contain_text("imeout")


def test_wifi_connect_now(ui):
    page = ui.goto("network")
    wifi = page.locator("[data-wifi]")
    expect(wifi.locator("[name=ssid]")).not_to_have_value("")
    wifi.locator("[name=ssid]").fill("")
    page.locator("[data-connect]").click()
    expect(wifi.locator("[data-msg]")).to_have_text("Enter a network name first.")
    wifi.locator("[name=ssid]").fill("Barn")
    page.locator("[data-connect]").click()
    expect(page.locator("dialog[open]")).to_contain_text("Connect to “Barn” now?")
    ui.confirm()
    ui.toast("Connected to Barn")


def test_hotspot_settings_and_start(ui, server):
    page = ui.goto("network")
    ap = page.locator("[data-ap]")
    expect(ap.locator("[name=ap_ssid]")).to_have_value("DINKY-COOP")
    ap.locator("[name=ap_password]").fill("short")
    ap.locator("button[type=submit]").click()
    expect(ap.locator("[data-msg]")).to_contain_text("8-63")
    ap.locator("[name=ap_ssid]").fill("COOP-AP")
    ap.locator("[name=ap_password]").fill("longenough1")
    ap.locator("button[type=submit]").click()
    expect(ap.locator("[data-msg]")).to_have_text("Saved")
    assert server.get("/api/wifi-config")["ap_ssid"] == "COOP-AP"
    page.locator("[data-start-ap]").click()
    ui.confirm()
    ui.toast("Hotspot started")


# ── door & hardware: pin configuration ──────────────────────────────────────

def test_pin_configuration(ui, server):
    page = ui.goto("hardware")
    form = page.locator("[data-gpio]")
    expect(form.locator("[name=motor_in1]")).to_have_value("17")
    expect(page.locator("[data-pins] tr")).to_have_count(10)
    form.locator("[name=reference_timeout]").fill("90")
    form.locator("[name=invert_end_up]").check()
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_contain_text("GPIO config saved")
    cfg = server.get("/api/gpio-config")
    assert (cfg["reference_timeout"], cfg["invert_end_up"]) == (90, True)
    form.locator("[name=invert_end_up]").uncheck()
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg]")).to_contain_text("GPIO config saved")

    form.locator("[name=motor_in2]").fill("17")   # duplicate pin
    form.locator("button[type=submit]").click()
    expect(form.locator("[data-msg].error")).to_be_visible()
    assert server.get("/api/gpio-config")["motor_in2"] == 27
    form.locator("[data-reload]").click()
    expect(form.locator("[name=motor_in2]")).to_have_value("27")
    expect(form.locator("[data-msg]")).to_have_text("")


# ── logs ─────────────────────────────────────────────────────────────────────

def test_logs_page(ui):
    page = ui.goto("logs")
    lines = page.locator("[data-log-line]")
    expect(lines.first).to_be_visible()
    total = lines.count()
    assert total >= 3
    expect(page.locator("[data-count]")).to_contain_text(f"Showing {total} of {total}")

    page.locator("[data-search]").fill("MOCK hardware")
    expect(lines).not_to_have_count(total)
    for text in lines.all_inner_texts():
        assert "mock hardware" in text.lower()
    page.locator("[data-search]").fill("")

    page.locator("[data-level=ERROR]").click()
    expect(page.locator("[data-level=ERROR]")).to_have_attribute("aria-pressed", "true")
    for lv in page.locator("[data-log-line] .lv").all_inner_texts():
        assert lv in ("ERROR", "CRITICAL")
    page.locator("[data-level=DEBUG]").click()

    page.locator("[data-logger]").select_option("coop.application")
    for lg in page.locator("[data-log-line] .lg").all_inner_texts():
        assert lg == "coop.application"
    page.locator("[data-logger]").select_option("")

    first = lines.first.inner_text()
    page.locator("[data-sort]").click()
    expect(page.locator("[data-sort]")).to_have_text("Oldest first")
    assert lines.first.inner_text() != first

    page.locator("[data-auto]").click()
    expect(page.locator("[data-auto]")).to_have_text("Auto-refresh on")
    before = page.locator("[data-count]").inner_text()
    ui.page.evaluate("() => window.__coop.ack('stop')")      # produces a new log line
    expect(page.locator("[data-count]")).not_to_have_text(before, timeout=8000)
    page.locator("[data-auto]").click()
    expect(page.locator("[data-auto]")).to_have_text("Auto-refresh off")


# ── system ───────────────────────────────────────────────────────────────────

def test_system_information(ui, server):
    page = ui.goto("system")
    expect(page.locator("[data-version]")).to_have_text(server.get("/version")["version"])
    expect(page.locator("[data-bind=python_version]")).to_have_text(re.compile(r"^3\.\d+"))
    expect(page.locator("[data-cpu]")).to_have_text(re.compile(r"%$"))
    expect(page.locator("[data-health] [data-worker=door]")).to_have_text("Running")
    expect(page.locator("[data-health] [data-worker=camera]")).to_have_text("Running")
    page.locator("[data-health-refresh]").click()
    expect(page.locator("[data-health] [data-worker=broadcast]")).to_have_text("Running")
    expect(page.locator("[data-skew]")).to_have_text("In sync")
    expect(page.locator("[data-open-internals]")).to_have_attribute("href", "#/internals")


def test_system_time_controls(ui):
    page = ui.goto("system")
    page.locator("[data-sync-time]").click()
    ui.toast("Device time updated")
    page.locator("[data-set-time] button[type=submit]").click()
    ui.toast("Pick a date and time first")
    expect(page.locator("#sys-time")).to_have_attribute("aria-invalid", "true")
    page.locator("#sys-time").fill("2026-01-02T03:04:05")
    page.locator("[data-set-time] button[type=submit]").click()
    ui.toast("Device time updated")


def test_update_and_restart_are_refused_on_mock_hardware(ui):
    page = ui.goto("system")
    page.locator("[data-update]").click()
    expect(page.locator("dialog[open]")).to_contain_text("Update and restart?")
    page.locator("dialog[open] button[value=cancel]").click()
    page.locator("[data-update]").click()
    ui.confirm()
    ui.toast("not supported")
    page.locator("[data-reboot]").click()
    expect(page.locator("dialog[open]")).to_contain_text("Restart the device?")
    ui.confirm()
    expect(page.locator(".toast[data-kind=error]").last).to_be_visible()
    expect(page.locator(".overlay")).to_have_count(0)


def test_push_notifications_explain_missing_keys(ui):
    page = ui.goto("system")
    expect(page.locator("[data-push-status]")).to_contain_text("VAPID keys missing")
    expect(page.locator("[data-push]")).to_be_disabled()


def test_theme_switching_persists(ui):
    page = ui.goto("system")
    html = page.locator("html")
    page.locator("[data-theme-pref=dark]").click()
    expect(html).to_have_attribute("data-theme", "dark")
    bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(15, 20, 26)"
    page.reload()
    ui.wait_connected()
    expect(html).to_have_attribute("data-theme", "dark")
    expect(page.locator("[data-theme-pref=dark]")).to_have_attribute("aria-checked", "true")
    page.locator("#theme-toggle").click()
    expect(html).to_have_attribute("data-theme", "light")
    page.locator("[data-theme-pref=system]").click()
    assert page.evaluate("() => document.documentElement.dataset.theme") is None
    assert page.evaluate("() => localStorage.getItem('coop-theme')") is None


def test_service_worker_and_manifest(ui, server):
    page = ui.goto("home")
    page.wait_for_function("() => navigator.serviceWorker.getRegistration().then(r => !!(r && r.active))")
    manifest = server.get("/manifest.json")
    assert manifest["theme_color"] == "#1F3A5F" and manifest["start_url"] == "/"


# ── phone layout ─────────────────────────────────────────────────────────────

def test_phone_tab_bar_and_more_menu(phone):
    page = phone.goto("home")
    expect(page.locator("#tabbar")).to_be_visible()
    expect(page.locator(".sidebar")).to_be_hidden()
    for route, title in [("climate", "Climate"), ("history", "History"), ("more", "More")]:
        page.locator(f"#tabbar [data-nav={route}]").tap()
        expect(page.locator("#page-title")).to_have_text(title)
    expect(page.locator("[data-more=schedule] .val")).to_have_text(re.compile(r"Manual|Sun|Timer"))
    expect(page.locator("[data-more=hardware] .val")).to_have_text(re.compile(r"Not calibrated|\d+\.\d s"))
    page.locator("[data-more=network]").tap()
    expect(page.locator("#page-title")).to_have_text("Network")
    expect(page.locator("#tabbar [data-nav=more]")).to_have_attribute("aria-current", "page")
    page.go_back()
    expect(page.locator("#page-title")).to_have_text("More")


def test_phone_home_controls_fit_the_screen(phone):
    page = phone.goto("home")
    width = page.evaluate("() => document.documentElement.scrollWidth")
    assert width <= 390
    for cmd in ("open", "stop", "close"):
        box = page.locator(f"[data-cmd={cmd}]").bounding_box()
        assert box["height"] >= 44 and box["x"] >= 0 and box["x"] + box["width"] <= 390
    page.locator("[data-cmd=stop]").tap()
    phone.toast("Door stopped")


def test_every_page_renders_without_errors_in_dark_mode(browser, server):
    context = browser.new_context(viewport={"width": 1366, "height": 900}, color_scheme="dark")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for route in ("home", "climate", "history", "camera", "schedule", "network", "hardware", "logs", "system", "internals", "more"):
        page.goto(f"{server.base}/#/{route}")
        page.wait_for_function("() => window.__coop && window.__coop.S.data")
        page.wait_for_timeout(300)
        assert page.evaluate("() => getComputedStyle(document.body).backgroundColor") == "rgb(15, 20, 26)"
        overflow = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
        assert not overflow, f"horizontal overflow on {route}"
    context.close()
    assert errors == []
