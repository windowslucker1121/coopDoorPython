"""E2E: navigation, door control, calibration, modes, safety banners.

The tests in this module share one server and run in file order (the door
is calibrated half-way through, like a real first-time setup).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

NAV = {"home": "Home", "climate": "Climate", "history": "History", "camera": "Camera",
       "schedule": "Schedule & location", "network": "Network", "hardware": "Door & hardware",
       "logs": "Logs", "system": "System & updates", "internals": "Live internals"}


def door_title(page):
    return page.locator("[data-door-title]")


def test_sidebar_navigation_reaches_every_page(ui):
    page = ui.goto("home")
    for route, title in NAV.items():
        page.locator(f"#sidenav [data-nav={route}]").click()
        expect(page.locator("#page-title")).to_have_text(title)
        expect(page.locator(f"#sidenav [data-nav={route}]")).to_have_attribute("aria-current", "page")
        expect(page.locator("#page")).to_have_attribute("data-page", route)
    expect(page.locator("[data-conn-text]").first).to_have_text("Connected · live")
    expect(page).to_have_title("Live internals · Dinky Coop")


@pytest.mark.parametrize("path", ["/debug", "/mock"])
def test_legacy_urls_open_the_hardware_page(ui, path):
    ui.page.goto(ui.base + path)
    ui.wait_connected()
    expect(ui.page.locator("#page-title")).to_have_text("Door & hardware")


def test_unknown_route_falls_back_to_home(ui):
    page = ui.goto("does-not-exist")
    expect(page.locator("#page-title")).to_have_text("Home")


def test_uncalibrated_door_asks_for_calibration_and_refuses_schedules(ui):
    page = ui.goto("home")
    expect(page.locator("[data-banner=calibrate]")).to_contain_text("Calibrate the door")
    expect(door_title(page)).to_have_text("Closed")
    page.locator(".seg [data-mode=auto]").click()
    ui.toast("Calibrate the door first")
    expect(page.locator(".seg [data-mode=manual]")).to_have_attribute("aria-checked", "true")


def test_manual_open_stop_close(ui):
    page = ui.goto("home")
    expect(page.locator("[data-cmd=close]")).to_be_disabled()   # already closed
    page.locator("[data-cmd=open]").click()
    ui.toast("Opening the door")
    expect(door_title(page)).to_have_text("Opening…")
    expect(page.locator("[data-door-svg]")).to_have_attribute("data-moving", "up")
    expect(door_title(page)).to_have_text("Open", timeout=10000)
    expect(page.locator("[data-cmd=open]")).to_be_disabled()
    expect(page.locator("[data-events]")).to_contain_text("Door opened manually")

    page.locator("[data-cmd=close]").click()
    expect(door_title(page)).to_have_text("Closing…")
    page.locator("[data-cmd=stop]").click()
    ui.toast("Door stopped")
    expect(door_title(page)).to_have_text("Stopped")
    expect(page.locator("[data-events]")).to_contain_text("Door stopped")
    # no position estimate before calibration: the drawing shows it half open
    assert float(ui.data("door_position_estimate")) == -1
    expect(page.locator("[data-door-svg]")).to_have_attribute("aria-label", "Door Stopped, 50 % open")

    page.locator("[data-cmd=close]").click()
    expect(door_title(page)).to_have_text("Closed", timeout=10000)
    expect(page.locator("[data-events]")).to_contain_text("Door closed manually")


def test_calibration_from_the_banner(ui, server):
    page = ui.goto("home")
    page.locator("[data-banner=calibrate] [data-action=calibrate]").click()
    expect(page.locator("dialog[open]")).to_contain_text("Calibrate the door?")
    page.locator("dialog[open] button[value=cancel]").click()           # cancel does nothing
    expect(page.locator("dialog[open]")).to_have_count(0)
    assert server.get("/api/settings")["reference_travel_ms"] is None

    page.locator("[data-banner=calibrate] [data-action=calibrate]").click()
    ui.confirm()
    ui.toast("Calibration started")
    expect(page.locator("[data-banner=calibrating]")).to_be_visible()
    expect(door_title(page)).to_have_text("Calibrating…")
    expect(page.locator("[data-cmd=open]")).to_be_disabled()
    expect(page.locator("[data-banner=calibrating]")).to_have_count(0, timeout=20000)
    expect(page.locator("[data-banner=calibrate]")).to_have_count(0)
    travel = server.get("/api/settings")["reference_travel_ms"]
    assert travel and 1000 < travel < 5000
    expect(page.locator("[data-events]")).to_contain_text("Calibrated")

    page.locator("#sidenav [data-nav=hardware]").click()
    expect(page.locator("[data-travel]")).to_have_text(re.compile(r"^\d+\.\d s$"))


def test_modes_on_home(ui, server):
    page = ui.goto("home")
    page.locator(".seg [data-mode=auto]").click()
    ui.toast("Following the sun on")
    expect(page.locator(".seg [data-mode=auto]")).to_have_attribute("aria-checked", "true")
    expect(page.locator("[data-mode-chip]")).to_have_text("Sun")
    expect(page.locator("[data-mode-summary]")).to_contain_text(re.compile(r"Sunrise \d\d:\d\d.*Sunset \d\d:\d\d"))
    expect(page.locator("[data-manual-hint]")).to_have_text("Open / Close / Stop switch to Manual")
    assert server.get("/api/settings")["mode"] == "auto"

    page.locator(".seg [data-mode=timer]").click()
    ui.toast("Timer mode on")
    expect(page.locator("[data-mode-chip]")).to_have_text("Timer")
    expect(page.locator("[data-mode-summary]")).to_contain_text("Opens")
    assert server.get("/api/settings")["mode"] == "timer"

    page.locator(".seg [data-mode=manual]").click()
    ui.toast("Manual mode on")
    expect(page.locator("[data-mode-chip]")).to_have_text("Manual")
    assert server.get("/api/settings")["mode"] == "manual"


def test_door_command_in_timer_mode_switches_to_manual(ui, server):
    page = ui.goto("home")
    page.locator(".seg [data-mode=timer]").click()
    expect(page.locator("[data-mode-chip]")).to_have_text("Timer")
    page.locator("[data-cmd=stop]").click()
    ui.toast("switched to Manual")
    expect(page.locator(".seg [data-mode=manual]")).to_have_attribute("aria-checked", "true")
    assert server.get("/api/settings")["mode"] == "manual"


def test_modes_on_schedule_page(ui, server):
    page = ui.goto("schedule")
    page.locator(".mode-card[data-mode=auto]").click()
    expect(page.locator(".mode-card[data-mode=auto]")).to_have_attribute("aria-checked", "true")
    page.locator(".mode-card[data-mode=manual]").click()
    expect(page.locator(".mode-card[data-mode=manual]")).to_have_attribute("aria-checked", "true")
    assert server.get("/api/settings")["mode"] == "manual"


def test_test_error_banner_and_clear(ui):
    page = ui.goto("hardware")
    page.locator("[data-action=test-error]").click()
    expect(page.locator("dialog[open]")).to_contain_text("Trigger a test error?")
    ui.confirm()
    ui.toast("Test error triggered")
    banner = page.locator("[data-banner=fault]")
    expect(banner).to_contain_text("The door stopped for safety")
    expect(banner).to_contain_text("A test error was triggered")
    page.locator("#sidenav [data-nav=home]").click()
    expect(door_title(page)).to_have_text("Needs attention")
    expect(page.locator("[data-cmd=open]")).to_be_disabled()
    expect(page.locator("[data-cmd=stop]")).to_be_disabled()
    banner.locator("[data-action=clear-error]").click()
    ui.toast("Error cleared")
    expect(banner).to_have_count(0)
    expect(page.locator("[data-cmd=stop]")).to_be_enabled()
    expect(page.locator("[data-events]")).to_contain_text("Error cleared")


def test_simulator_switch_override(ui):
    page = ui.goto("hardware")
    expect(page.locator("[data-sim]")).to_be_visible()
    target = "override_close" if ui.data("state") != "closed" else "override_open"
    end = "endstop_down" if target == "override_close" else "endstop_up"
    hold = page.locator(f"[data-hold={target}]")
    hold.hover()
    page.mouse.down()                       # hold the switch until the door arrives
    expect(hold).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-banner=override]")).to_contain_text("Manual switch is active")
    expect(page.locator(f"[data-pin-state={target}]")).to_have_text("HIGH")
    expect(page.locator(f"[data-pin-state={end}]")).to_have_text("HIGH", timeout=10000)
    page.mouse.up()
    expect(hold).to_have_attribute("aria-pressed", "false")
    expect(page.locator("[data-banner=override]")).to_have_count(0, timeout=10000)
    expect(page.locator(f"[data-pin-state={target}]")).to_have_text("LOW")
    page.locator("#sidenav [data-nav=home]").click()
    expect(page.locator("[data-events]")).to_contain_text("by the switch")


def test_simulator_endstop_toggle(ui):
    page = ui.goto("hardware")
    up = page.locator("[data-toggle=endstop_up]")
    expect(page.locator("[data-pin-state=endstop_up]")).to_be_visible()
    before = page.locator("[data-pin-state=endstop_up]").inner_text()
    up.click()
    after = "LOW" if before == "HIGH" else "HIGH"
    expect(page.locator("[data-pin-state=endstop_up]")).to_have_text(after)
    expect(up).to_have_attribute("aria-pressed", "true" if after == "HIGH" else "false")
    up.click()
    expect(page.locator("[data-pin-state=endstop_up]")).to_have_text(before)


def test_live_pin_table_follows_the_motor(ui):
    page = ui.goto("home")
    # get back to a clean closed door
    if ui.data("state") != "closed":
        page.locator("[data-cmd=close]").click()
        expect(door_title(page)).to_have_text("Closed", timeout=10000)
    page.locator("#sidenav [data-nav=hardware]").click()
    expect(page.locator("[data-pin-state=motor_ena]")).to_have_text("LOW")
    page.evaluate("() => window.__coop.ack('open')")
    expect(page.locator("[data-pin-state=motor_ena]")).to_have_text("HIGH")
    expect(page.locator("[data-hw-state]")).to_have_text("Open", timeout=10000)
    expect(page.locator("[data-pin-state=motor_ena]")).to_have_text("LOW")


def test_clock_mismatch_banner(browser, server):
    import datetime as dt
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    page.clock.set_system_time(dt.datetime.now() + dt.timedelta(hours=1))
    page.goto(server.base + "/#/home")
    banner = page.locator("[data-banner=clock]")
    expect(banner).to_contain_text("clock is 60 min behind")
    banner.locator("[data-dismiss]").click()
    expect(banner).to_have_count(0)
    context.close()


def test_clock_banner_fix_uses_device_time(browser, server):
    import datetime as dt
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    page.clock.set_system_time(dt.datetime.now() - dt.timedelta(minutes=10))
    page.goto(server.base + "/#/home")
    banner = page.locator("[data-banner=clock]")
    expect(banner).to_contain_text("ahead")
    banner.locator("[data-action=fix-clock]").click()
    expect(page.locator(".toast", has_text="Device time updated")).to_be_visible()
    context.close()


def test_socket_drop_shows_offline_banner_and_recovers(ui):
    page = ui.goto("home")
    page.evaluate("() => window.__coop.socket.disconnect()")
    expect(page.locator("[data-banner=offline]")).to_contain_text("Connection lost")
    expect(page.locator(".conn-card [data-conn-text]")).to_have_text("Offline - reconnecting")
    page.evaluate("() => window.__coop.socket.connect()")
    expect(page.locator("[data-banner=offline]")).to_have_count(0)
    expect(page.locator(".conn-card [data-conn-text]")).to_have_text("Connected · live")


def test_server_going_away_shows_offline_banner(ui, server):
    page = ui.goto("home")
    server.stop()
    expect(page.locator("[data-banner=offline]")).to_be_visible(timeout=15000)
    page.locator("[data-cmd=stop]").click()
    expect(page.locator(".toast", has_text="Not connected")).to_be_visible()
    # the browser logs the refused reconnect attempts - expected here
    ui.errors[:] = [e for e in ui.errors if "ERR_CONNECTION_REFUSED" not in e]
