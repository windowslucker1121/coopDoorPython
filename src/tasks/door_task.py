"""Door-management background task.

This module contains the ``door_task_main()`` function that is run in a
daemon thread.  It is responsible for:

* Driving the door motor toward the ``desired_door_state`` stored in
  ``protected_dict``.
* Implementing auto-mode (open at sunrise, close at sunset).
* **Obstruction detection during closing** — the lower endstop (BCM 24) is
  a rope-slack sensor.  If it fires more than 1 s before the calibrated
  reference time, a chicken or other obstruction is assumed; the door
  resumes closing automatically (up to ``MAX_CLOSE_RETRIES`` attempts)
  before entering error state and dispatching a push notification.
  Opening is **never** subject to this check.
* Timeouts: if the door has been running longer than
  ``reference_door_endstops_ms`` + ``DOOR_MOVE_MAX_AFTER_ENDSTOPS`` seconds
  without reaching an endstop, the door enters error state.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from protected_dict import protected_dict as global_vars
from services.door_service import MAX_CLOSE_RETRIES, AbstractDoorService
from services.astro_service import AstroService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# Extra seconds the motor is allowed to run *beyond* the reference time before
# the task gives up and raises an error.
DOOR_MOVE_MAX_AFTER_ENDSTOPS: float = 1.0


def door_task_main(
    door_service: AbstractDoorService,
    notification_service: NotificationService,
    astro_service: AstroService,
) -> None:
    """Background loop — drives door motor and validates closing strokes.

    This function never returns; it should be run in a daemon thread.

    Parameters
    ----------
    door_service:
        Service wrapping the hardware DOOR instance.
    notification_service:
        Service used to send push notifications on error / obstruction.
    astro_service:
        Service providing timezone-aware sunrise/sunset times.
    """
    # Single mutable-state dict created once and passed by reference every
    # iteration.  _single_iteration() mutates it in-place so state persists
    # across loop cycles without module-level globals.
    state: dict = {
        "door_move_count": 0.0,
        "first_iter": True,
        "sunrise": None,
        "sunset": None,
        "sent_error_notification": False,
        "thread_sleep_time": 0.5,
        "last_d_door_state": None,
        # Used to detect the "closing → closed" transition for obstruction checking
        "prev_door_state": "stopped",
        "close_attempt_count": 0,
    }

    while True:
        try:
            _single_iteration(
                door_service=door_service,
                notification_service=notification_service,
                astro_service=astro_service,
                state=state,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("door_task_main: unexpected error in iteration: %s", exc)

        time.sleep(state["thread_sleep_time"])


# ---------------------------------------------------------------------------
# Internal — single iteration (extracted for testability)
# ---------------------------------------------------------------------------


def _single_iteration(
    door_service: AbstractDoorService,
    notification_service: NotificationService,
    astro_service: AstroService,
    state: dict,
) -> None:
    """Execute one ``door_task`` iteration.

    The ``state`` dict holds all loop-level mutable variables so they can
    survive across iterations without module-level globals.
    """
    thread_sleep_time: float = state["thread_sleep_time"]

    # ── One-shot command flags ────────────────────────────────────────────────
    toggle_reference: bool
    clear_error_state: bool
    toggle_reference, clear_error_state = global_vars.instance().get_values(
        ["toggle_reference_of_endstops", "clear_error_state"]
    )
    generate_error: bool = bool(global_vars.instance().get_value("debug_error"))

    if generate_error:
        door_service.set_error("Test Error")
        global_vars.instance().set_value("debug_error", False)

    if clear_error_state:
        door_service.clear_error()
        global_vars.instance().set_value("clear_error_state", False)
        state["sent_error_notification"] = False
        state["close_attempt_count"] = 0

    # ── Reference sequence ────────────────────────────────────────────────────
    if toggle_reference:
        logger.info("door_task: starting reference sequence…")
        global_vars.instance().set_value("toggle_reference_of_endstops", False)
        state["prev_door_state"] = "stopped"
        state["close_attempt_count"] = 0

        if not door_service.reference_endstops():
            logger.critical("door_task: reference sequence failed.")
        else:
            ref_ms = door_service.get_reference_ms()
            global_vars.instance().set_value("reference_door_endstops_ms", ref_ms)
            logger.info("door_task: reference complete — %.0f ms", ref_ms)
        return  # skip normal control logic this iteration

    # ── Normal control loop ───────────────────────────────────────────────────
    door_state: str = door_service.get_state()
    door_override: bool = door_service.get_override()

    d_door_state: str
    auto_mode_str: str
    reference_door_endstops_ms: float | None
    d_door_state, auto_mode_str, reference_door_endstops_ms = (
        global_vars.instance().get_values(
            ["desired_door_state", "auto_mode", "reference_door_endstops_ms"]
        )
    )

    auto_mode: bool = auto_mode_str == "True"
    door_service.set_auto_mode(auto_mode)

    if d_door_state != state["last_d_door_state"]:
        state["door_move_count"] = 0.0
        state["last_d_door_state"] = d_door_state
        if d_door_state != "closed":
            state["close_attempt_count"] = 0

    # ── Auto mode: update desired_door_state based on sunrise/sunset ──────────
    if auto_mode and not door_override:
        if reference_door_endstops_ms is None:
            logger.warning("door_task: reference not set — disabling auto mode.")
            global_vars.instance().set_value("auto_mode", "False")
        else:
            sunrise, sunset = astro_service.get_sunrise_and_sunset()
            state["sunrise"] = sunrise
            state["sunset"] = sunset
            sunrise_offset: int
            sunset_offset: int
            sunrise_offset, sunset_offset = global_vars.instance().get_values(
                ["sunrise_offset", "sunset_offset"]
            )
            open_time = sunrise + timedelta(minutes=sunrise_offset)
            close_time = sunset + timedelta(minutes=sunset_offset)
            current_time = astro_service.get_current_time()
            time_window = timedelta(minutes=1)

            if state["first_iter"]:
                desired = "open" if open_time <= current_time < close_time else "closed"
                global_vars.instance().set_value("desired_door_state", desired)

            if open_time <= current_time <= open_time + time_window:
                global_vars.instance().set_value("desired_door_state", "open")

            if close_time <= current_time <= close_time + time_window:
                global_vars.instance().set_value("desired_door_state", "closed")

    # ── Poll endstops (reliable safety-net for missed edge callbacks) ─────────
    if door_service.check_endstops():
        door_state = door_service.get_state()

    # ── Obstruction detection — closing only ──────────────────────────────────
    if state["prev_door_state"] == "closing" and door_state == "closed":
        if reference_door_endstops_ms is not None:
            elapsed_ms = door_service.get_elapsed_closing_ms()
            if elapsed_ms is not None:
                result = door_service.validate_close_endstop(
                    elapsed_ms=elapsed_ms,
                    reference_ms=float(reference_door_endstops_ms),
                    attempt_number=state["close_attempt_count"] + 1,
                )
                if result.obstruction_detected:
                    state["close_attempt_count"] += 1
                    logger.warning(
                        "door_task: obstruction detected (attempt %d/%d) — "
                        "elapsed=%.0f ms vs reference=%.0f ms",
                        state["close_attempt_count"],
                        MAX_CLOSE_RETRIES,
                        elapsed_ms,
                        float(reference_door_endstops_ms),
                    )
                    if state["close_attempt_count"] >= MAX_CLOSE_RETRIES:
                        door_service.set_error(
                            f"Obstruction: lower endstop triggered early "
                            f"{state['close_attempt_count']} times — "
                            "possible chicken under door"
                        )
                        notification_service.send(
                            "Door Obstruction",
                            "The coop door may be blocked by a chicken. "
                            "Please check immediately.",
                        )
                    else:
                        logger.info(
                            "door_task: resuming closing after obstruction "
                            "(attempt %d/%d).",
                            state["close_attempt_count"],
                            MAX_CLOSE_RETRIES,
                        )
                        door_service.resume_after_obstruction()
                        door_state = door_service.get_state()  # → "stopped"
                        state["door_move_count"] = 0.0
                else:
                    state["close_attempt_count"] = 0

    # ── Override: physical 3-position switch ──────────────────────────────────
    if door_override:
        door_service.check_if_switch_neutral()
        global_vars.instance().set_value("desired_door_state", door_service.get_state())

    # ── Drive motor toward desired state ──────────────────────────────────────
    elif door_state != d_door_state:
        if door_service.is_error():
            door_service.stop()
            state["door_move_count"] = 0.0
            if not state["sent_error_notification"]:
                notification_service.send(
                    "Door Error",
                    "The coop door is in an error state. Please check.",
                )
                state["sent_error_notification"] = True
        else:
            endstop_timeout: float = (
                float(reference_door_endstops_ms) * 0.001
                if isinstance(reference_door_endstops_ms, (int, float))
                else 10.0
            )
            max_move = endstop_timeout + DOOR_MOVE_MAX_AFTER_ENDSTOPS

            match d_door_state:
                case "stopped":
                    if door_state in ("open", "closed"):
                        door_service.stop(door_state)
                        global_vars.instance().set_value("desired_door_state", door_state)
                    else:
                        door_service.stop()
                    state["door_move_count"] = 0.0

                case "open":
                    if state["door_move_count"] <= max_move:
                        door_service.open()
                        state["door_move_count"] += thread_sleep_time
                    else:
                        door_service.set_error("Endstop not reached (opening)")
                        global_vars.instance().set_value("desired_door_state", "stopped")
                        state["door_move_count"] = 0.0
                        logger.critical("door_task: upper endstop not reached — error state set.")

                case "closed":
                    if state["door_move_count"] <= max_move:
                        door_service.close()
                        state["door_move_count"] += thread_sleep_time
                    else:
                        door_service.set_error("Endstop not reached (closing)")
                        global_vars.instance().set_value("desired_door_state", "stopped")
                        state["door_move_count"] = 0.0
                        logger.critical("door_task: lower endstop not reached — error state set.")

                case _:
                    door_service.set_error(f"Unknown desired state: {d_door_state!r}")
                    state["door_move_count"] = 0.0

    # ── Door already at desired state ─────────────────────────────────────────
    else:
        door_service.check_if_switch_neutral(neutral_state=door_service.get_state())
        state["door_move_count"] = 0.0

    # ── Update shared state ───────────────────────────────────────────────────
    state["first_iter"] = False
    final_state = door_service.get_state()
    final_override = door_service.get_override()
    state["prev_door_state"] = final_state  # track for next iteration's obstruction check

    global_vars.instance().set_values(
        {
            "state": final_state,
            "override": final_override,
            "sunrise": state.get("sunrise"),
            "sunset": state.get("sunset"),
            "error_state": door_service.error_state() if door_service.is_error() else "",
        }
    )
