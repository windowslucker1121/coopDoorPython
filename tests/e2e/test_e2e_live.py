"""E2E: the Live internals page and "Use my location"."""

from __future__ import annotations

import re

from playwright.sync_api import expect


def node(page, name):
    return page.locator(f"[data-node={name}]")


CUR = re.compile(r"\bcur\b")


# ── live internals ───────────────────────────────────────────────────────────

def test_internals_updates_without_interaction(ui):
    page = ui.goto("internals")
    expect(page.locator("[data-live-state]")).to_have_text("Live")
    stamp = page.locator("[data-live-stamp]")
    expect(stamp).to_have_text(re.compile(r"^updated \d\d:\d\d:\d\d\.\d{3}"))
    first = stamp.inner_text()
    expect(stamp).not_to_have_text(first, timeout=3000)          # refreshes on its own
    expect(node(page, "closed")).to_have_class(CUR)
    expect(page.locator("[data-led=lower]")).to_have_attribute("data-on", "true")
    expect(page.locator("[data-led=upper]")).to_have_attribute("data-on", "false")
    expect(page.locator("[data-motor]")).to_have_attribute("data-dir", "off")
    expect(page.locator("[data-v=travel]")).to_contain_text("not calibrated")


def test_internals_follow_the_door(ui):
    page = ui.goto("internals")
    page.evaluate("() => window.__coop.ack('open')")
    # the page shows the move while it happens ...
    expect(node(page, "opening")).to_have_class(CUR, timeout=3000)
    expect(node(page, "open")).to_have_class(re.compile(r"\btarget\b"))
    expect(page.locator("[data-motor]")).to_have_attribute("data-dir", "up")
    expect(page.locator("[data-led=motor_ena]")).to_have_attribute("data-on", "true")
    expect(page.locator("[data-edge=opening-open]")).to_have_class(re.compile(r"\bhot\b"))
    expect(page.locator("[data-v=run]")).to_have_text(re.compile(r"^\d+\.\d s of \d+(\.\d)? s$"))
    expect(page.locator("[data-v=desired]")).to_have_text("Open")
    # ... and the result
    expect(node(page, "open")).to_have_class(CUR, timeout=10000)
    expect(page.locator("[data-led=upper]")).to_have_attribute("data-on", "true")
    expect(page.locator("[data-motor]")).to_have_attribute("data-dir", "off")
    expect(page.locator("[data-v=run]")).to_have_text("idle")


def test_internals_show_fault_and_switch(ui):
    page = ui.goto("internals")
    page.evaluate("() => window.__coop.ack('generate_error')")
    expect(page.locator("[data-int-fault]")).to_be_visible(timeout=3000)
    expect(page.locator("[data-v=fault]")).to_have_text("Test Error")
    page.evaluate("() => window.__coop.ack('clear_error')")
    expect(page.locator("[data-int-fault]")).to_be_hidden(timeout=3000)

    pin = page.evaluate("() => fetch('/api/gpio-config').then(r => r.json()).then(c => c.override_close)")
    page.evaluate("(p) => window.__coop.socket.emit('mock_trigger_pin', {pin: p, state: 'HIGH'})", pin)
    expect(page.locator("[data-sw=closed]")).to_have_class(re.compile(r"\bon\b"), timeout=3000)
    page.evaluate("(p) => window.__coop.socket.emit('mock_trigger_pin', {pin: p, state: 'LOW'})", pin)
    expect(page.locator("[data-sw=off]")).to_have_class(re.compile(r"\bon\b"), timeout=3000)


def test_internals_pause_and_resume(ui):
    page = ui.goto("internals")
    stamp = page.locator("[data-live-stamp]")
    expect(stamp).to_contain_text("updated")
    page.locator("[data-pause]").click()
    expect(page.locator("[data-live-state]")).to_have_text("Paused")
    expect(page.locator("[data-live-dot]")).to_have_attribute("data-state", "paused")
    frozen = stamp.inner_text()
    page.wait_for_timeout(1800)
    assert stamp.inner_text() == frozen
    page.locator("[data-pause]").click()
    expect(page.locator("[data-live-state]")).to_have_text("Live")
    expect(stamp).not_to_have_text(frozen, timeout=3000)


def test_internals_workers_sensors_system(ui):
    page = ui.goto("internals")
    for w in ("door", "environment", "broadcast", "camera"):
        row = page.locator(f"[data-wrow={w}]")
        expect(row.locator(".led")).to_have_attribute("data-on", "true")
        expect(row.locator("[data-werr]")).to_have_text("ok")
    expect(page.locator("[data-sensor=temp_in] [data-sv]")).to_have_text(re.compile(r"^-?\d+\.\d°$"))
    expect(page.locator("[data-sensor=hum_in] [data-sv]")).to_have_text(re.compile(r"^\d+\.\d %$"))
    expect(page.locator("[data-sys=python]")).to_have_text(re.compile(r"^3\.\d+"))
    expect(page.locator("[data-consts] dt").first).to_be_visible()


def test_internals_config_filter_and_live_values(ui):
    page = ui.goto("internals")
    count = page.locator("[data-cfg-count]")
    expect(count).to_have_text(re.compile(r"^(\d+) of \1 values$"))
    page.locator("[data-cfg-filter]").fill("sunrise_offset")
    expect(count).to_have_text(re.compile(r"^1 of \d+ values$"))
    value = page.locator("[data-cfg-k='config.sunrise_offset']")
    expect(value).to_have_text("0")
    page.evaluate("() => window.__coop.ack('auto_offsets', {sunrise_offset: 25, sunset_offset: 0})")
    expect(value).to_have_text("25", timeout=3000)                  # changes appear by themselves
    page.locator("[data-cfg-filter]").fill("no-such-key-xyz")
    expect(page.locator("[data-cfg]")).to_contain_text("No matching keys.")


def test_internals_raw_json_and_copy(browser, server):
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=server.base)
    page = context.new_page()
    page.goto(server.base + "/#/internals")
    expect(page.locator("[data-live-state]")).to_have_text("Live")
    page.locator("[data-raw] summary").click()
    expect(page.locator("[data-raw-body]")).to_contain_text('"live"')
    page.locator("[data-copy]").click()
    expect(page.locator(".toast", has_text="Copied")).to_be_visible()
    assert '"door_constants"' in page.evaluate("() => navigator.clipboard.readText()")
    context.close()


def test_system_page_links_to_internals(ui):
    page = ui.goto("system")
    page.locator("[data-open-internals]").click()
    expect(page.locator("#page-title")).to_have_text("Live internals")
    expect(page.locator("#sidenav [data-nav=internals]")).to_have_attribute("aria-current", "page")


def test_internals_on_phone(phone):
    page = phone.goto("more")
    page.locator("[data-more=internals]").tap()
    expect(page.locator("#page-title")).to_have_text("Live internals")
    expect(page.locator("[data-live-state]")).to_have_text("Live")
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 390


# ── use my location ──────────────────────────────────────────────────────────

def _schedule(browser, server, **ctx):
    context = browser.new_context(viewport={"width": 1366, "height": 900}, **ctx)
    page = context.new_page()
    page.goto(server.base + "/#/schedule")
    page.wait_for_function("() => window.__coop && window.__coop.S.connected && window.__coop.S.settings")
    # the page re-renders once the settings arrived; wait for the filled form
    page.wait_for_function("() => { const c = document.querySelector('[data-location] [name=city]'); return c && c.value; }")
    return context, page


def _form(page):
    return {n: page.locator(f"[data-location] [name={n}]").input_value()
            for n in ("city", "region", "latitude", "longitude", "timezone")}


def test_use_my_location_fills_the_form(browser, server):
    context, page = _schedule(browser, server, geolocation={"latitude": 51.5072, "longitude": -0.1276, "accuracy": 30},
                              permissions=["geolocation"], timezone_id="Europe/London")
    page.locator("[data-locate]").click()
    expect(page.locator("[data-locate-msg]")).to_contain_text("nearest city London")
    expect(page.locator("[data-locate-msg]")).to_contain_text("±30 m")
    assert _form(page) == {"city": "London", "region": "England", "latitude": "51.5072",
                           "longitude": "-0.1276", "timezone": "Europe/London"}
    expect(page.locator("[data-location] button[type=submit]")).to_be_focused()
    assert server.get("/api/settings")["location"]["city"] != "London"   # filled, not saved yet
    page.locator("[data-location] button[type=submit]").click()
    expect(page.locator("[data-location] [data-msg]")).to_have_text("Location saved")
    assert server.get("/api/settings")["location"]["city"] == "London"
    context.close()


def test_use_my_location_far_from_any_city(browser, server):
    context, page = _schedule(browser, server, geolocation={"latitude": -48.87, "longitude": -123.39},
                              permissions=["geolocation"], timezone_id="Pacific/Pitcairn")
    page.locator("[data-locate]").click()
    expect(page.locator("[data-locate-msg]")).to_contain_text("Filled in from your position")
    assert _form(page) == {"city": "My coop", "region": "", "latitude": "-48.87",
                           "longitude": "-123.39", "timezone": "Pacific/Pitcairn"}
    context.close()


def test_use_my_location_when_access_is_blocked(browser, server):
    context, page = _schedule(browser, server, timezone_id="Europe/Paris")
    page.evaluate("() => { navigator.geolocation.getCurrentPosition = (ok, fail) => fail({code: 1, message: 'denied'}); }")
    page.locator("[data-locate]").click()
    msg = page.locator("[data-locate-msg]")
    expect(msg).to_contain_text("Location access was blocked.")
    expect(msg).to_contain_text("Filled in Paris from this device's time zone (Europe/Paris)")
    assert (_form(page)["city"], _form(page)["timezone"]) == ("Paris", "Europe/Paris")
    context.close()


def test_use_my_location_on_plain_http(browser, server):
    context, page = _schedule(browser, server, timezone_id="America/Chicago")
    page.evaluate("() => Object.defineProperty(window, 'isSecureContext', {value: false})")
    page.locator("[data-locate]").click()
    msg = page.locator("[data-locate-msg]")
    expect(msg).to_contain_text("https://")
    expect(msg).to_contain_text("Chicago")
    assert _form(page)["timezone"] == "America/Chicago"
    context.close()


def test_use_my_location_unknown_time_zone(browser, server):
    context, page = _schedule(browser, server, timezone_id="Antarctica/Troll")
    page.evaluate("() => Object.defineProperty(window, 'isSecureContext', {value: false})")
    before = _form(page)
    page.locator("[data-locate]").click()
    expect(page.locator("[data-locate-msg].error")).to_contain_text("matches no known city")
    assert _form(page) == before
    context.close()


def test_use_my_location_prefers_the_canonical_time_zone(browser, server):
    # the city list says "US/Mountain" for Denver; the browser's "America/Denver" is the same zone
    context, page = _schedule(browser, server, geolocation={"latitude": 39.74, "longitude": -104.99},
                              permissions=["geolocation"], timezone_id="America/Denver")
    page.locator("[data-locate]").click()
    expect(page.locator("[data-locate-msg]")).to_contain_text("nearest city Denver")
    f = _form(page)
    assert (f["city"], f["timezone"]) == ("Denver", "America/Denver")
    page.locator("[data-location] button[type=submit]").click()
    expect(page.locator("[data-location] [data-msg]")).to_have_text("Location saved")
    assert server.get("/api/settings")["location"]["timezone"] == "America/Denver"
    context.close()


def test_use_my_location_uses_the_places_zone_when_the_phone_differs(browser, server):
    # a phone still on home time while standing in Berlin
    context, page = _schedule(browser, server, geolocation={"latitude": 52.52, "longitude": 13.40},
                              permissions=["geolocation"], timezone_id="America/New_York")
    page.locator("[data-locate]").click()
    expect(page.locator("[data-locate-msg]")).to_contain_text("nearest city Berlin")
    assert _form(page)["timezone"] == "Europe/Berlin"
    context.close()
