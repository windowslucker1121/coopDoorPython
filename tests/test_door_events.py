"""The controller's human-readable event feed (Home "Today" list, History)."""

from __future__ import annotations

from conftest import at
from coop.config import Mode
from coop.door.model import DesiredState, DoorState
from coop.web.payloads import dashboard_payload


def texts(rig):
    return [e["text"] for e in rig.controller.recent_events(20)]


def test_manual_open_and_close(rig):
    rig.controller.command(DesiredState.OPEN)
    rig.step(2)
    rig.upper()
    rig.step(2)
    assert rig.state is DoorState.OPEN
    rig.upper(False)
    rig.controller.command(DesiredState.CLOSED)
    rig.step(2)
    rig.lower()
    rig.step(2)
    assert rig.state is DoorState.CLOSED
    assert texts(rig)[:2] == ["Door closed manually", "Door opened manually"]


def test_newest_first_with_time_and_kind(rig):
    rig.controller.command(DesiredState.OPEN)
    rig.step(2)
    rig.upper()
    rig.step(2)
    e = rig.controller.recent_events(1)[0]
    assert (e["kind"], e["time"]) == ("open", "12:00") and e["ts"].startswith("2025-06-01T12:00")


def test_switch_override_is_attributed(rig):
    rig.step()
    rig.switch("open")
    rig.step(2)
    rig.upper()
    rig.step(2)
    assert "Door opened by the switch" in texts(rig)


def test_schedule_is_attributed(rig_factory):
    rig = rig_factory(mode=Mode.AUTO, now=at(12))
    rig.step(2)
    rig.upper()
    rig.step(2)
    assert "Door opened by the sun schedule" in texts(rig)


def test_stop_while_moving(rig):
    rig.controller.command(DesiredState.OPEN)
    rig.step(2)
    rig.controller.command(DesiredState.STOPPED)
    rig.step(2)
    assert texts(rig)[0] == "Door stopped"


def test_calibration_and_error_events(rig_factory):
    rig = rig_factory(reference_ms=None)
    rig.controller.request_reference()
    rig.step()
    assert "Calibration started" in texts(rig)
    rig.controller.clear_error()
    rig.step()
    rig.controller.inject_test_error()
    rig.step()
    assert any(t.startswith("Fault:") for t in texts(rig))
    rig.controller.clear_error()
    rig.step()
    assert texts(rig)[0] == "Error cleared"


def test_feed_is_bounded(rig):
    for i in range(150):
        rig.controller._event("info", str(i))
    assert len(rig.controller.events) == 100
    assert texts(rig)[0] == "149"
    assert len(rig.controller.recent_events(12)) == 12


def test_payload_carries_events(make_app):
    app = make_app({"auto_mode": False, "reference_door_endstops_ms": 8000})
    app.controller._event("info", "hello")
    app.controller.step()
    d = dashboard_payload(app)
    assert d["events"][0]["text"] == "hello" and len(d["events"]) <= 12
    assert d["mode"] == "manual" and d["hardware_mock"] is True and d["retry_max"] >= 1
