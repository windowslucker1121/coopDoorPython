"""DoorTaskRunner — extracted, testable door control loop.

The original ``door_task`` function in ``app.py`` is an infinite ``while``
loop that is hard to test in isolation.  This module pulls the per-iteration
logic into :class:`DoorTaskRunner` whose :meth:`step` method executes
exactly one iteration of that loop without any ``time.sleep`` call.

``app.py`` creates a runner instance in ``door_task()`` and calls
``runner.step()`` in a ``while True`` loop, preserving identical runtime
behaviour.  Tests create a runner directly, inject deterministic callables
for time-dependent external calls, and call ``step()`` any number of times
to drive the state machine without waiting.

No application logic has been modified; only the *structure* changed.
"""

import time
import logging
from datetime import timedelta

from protected_dict import protected_dict as global_vars

logger = logging.getLogger(__name__)


class DoorTaskRunner:
    """Encapsulates the per-iteration mutable state of ``door_task``.

    Parameters
    ----------
    door:
        A :class:`~door.DOOR` instance to control.
    get_sunrise_sunset:
        Callable ``() -> (sunrise_datetime, sunset_datetime)``.
        Injected so tests can supply deterministic values.
    get_current_time:
        Callable ``() -> datetime`` returning the current timezone-aware
        time.  Injected for the same reason.
    send_notification:
        Callable ``(title: str, body: str)`` that delivers a Web Push
        notification.  Injected so tests can capture calls without
        touching the network.
    """

    DOOR_MOVE_MAX_AFTER_ENDSTOPS = 1
    PREMATURE_CLOSE_THRESHOLD = 0.8  # require ≥80 % of reference travel time

    def __init__(self, door, get_sunrise_sunset, get_current_time, send_notification):
        self.door = door
        self._get_sunrise_sunset = get_sunrise_sunset
        self._get_current_time = get_current_time
        self._send_notification = send_notification

        # --- Per-iteration mutable state (mirrors door_task local vars) ---
        self.door_move_count = 0
        self.first_iter = True
        self.door_state = None
        self.door_override = None
        self.sunrise = None
        self.sunset = None
        self.sentErrorNotification = False
        self.thread_sleep_time = 0.5
        self.last_d_door_state = None

        # Auto-close premature lower-endstop retry state
        self.auto_close_premature_count = 0
        self.auto_close_retry_pending = False
        self.auto_close_retry_time = None
        self.was_door_closing = False  # door state at end of previous iteration
        # Set to True when a retry is fired so that premature detection and the
        # drive block can run even if the lower endstop is still physically active
        # (door_state == d_door_state == "closed" but motor never ran).
        self._close_retry_just_fired = False
        self.PREMATURE_CLOSE_MAX_RETRIES = 5

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def step(self) -> bool:
        """Execute one iteration of the door control loop.

        This is a direct extraction of the ``while True`` body from the
        original ``door_task`` function.  All logic is identical; only
        ``continue`` statements (which would jump back to the ``while``
        head) are replaced by ``return False`` / ``return True``.

        Returns
        -------
        bool
            ``False`` if the step was aborted early (e.g. a failed
            reference-endstop sequence that previously used ``continue``
            to skip the sleep and restart the loop).  ``True`` in every
            other case, including a successful reference sequence.
        """
        door = self.door

        toogle_reference_of_endstops, clearErrorState = global_vars.instance().get_values(
            ["toggle_reference_of_endstops", "clear_error_state"]
        )

        generateError = global_vars.instance().get_value("debug_error")
        if generateError:
            door.ErrorState("Test Error")
            global_vars.instance().set_value("debug_error", False)

        if clearErrorState:
            door.clear_errorState()
            global_vars.instance().set_value("clear_error_state", False)
            self.sentErrorNotification = False
            # Also reset the premature-endstop retry state so the door gets
            # a clean slate after the operator acknowledges the error.
            self.auto_close_premature_count = 0
            self.auto_close_retry_pending = False
            self.auto_close_retry_time = None
            self._close_retry_just_fired = False

        if toogle_reference_of_endstops:
            logger.info("Referencing door endstops, waiting for completion.")
            global_vars.instance().set_value("toggle_reference_of_endstops", False)

            if door.reference_endstops() == False:
                logger.critical(
                    "Referencing door endstops failed, please check the door "
                    "and try again."
                )
                # Mirrors the original ``continue`` — abort this iteration
                # (caller should NOT sleep and should loop immediately).
                return False

            global_vars.instance().set_value(
                "reference_door_endstops_ms", door.reference_door_endstops_ms
            )
            logger.info(
                "Reference Sequence Successfull with total time: %s milliseconds",
                door.reference_door_endstops_ms,
            )
            # After a reference sequence the door state is fresh; don't let
            # a stale was_door_closing flag cause a spurious premature detection.
            self.was_door_closing = False
            self._close_retry_just_fired = False

        else:
            # ------------------------------------------------------------------
            # Normal (non-reference) iteration
            # ------------------------------------------------------------------

            # Get state and desired state:
            self.door_state = door.get_state()
            self.door_override = door.get_override()
            d_door_state, auto_mode, reference_door_endstops_ms = (
                global_vars.instance().get_values(
                    ["desired_door_state", "auto_mode", "reference_door_endstops_ms"]
                )
            )

            auto_mode = auto_mode == "True"
            door.set_auto_mode(auto_mode)

            if d_door_state != self.last_d_door_state:
                self.door_move_count = 0
                self.last_d_door_state = d_door_state

            # If we are in auto mode then open or close the door based on
            # sunrise / sunset times.
            if auto_mode and not self.door_override:
                if reference_door_endstops_ms is None:
                    logger.warning(
                        "Reference door endstops not set. Please run the reference "
                        "door endstops sequence from the WebUI - disabling auto mode."
                    )
                    global_vars.instance().set_value("auto_mode", "False")
                else:
                    self.sunrise, self.sunset = self._get_sunrise_sunset()
                    sunrise_offset, sunset_offset = global_vars.instance().get_values(
                        ["sunrise_offset", "sunset_offset"]
                    )
                    open_time = self.sunrise + timedelta(minutes=sunrise_offset)
                    close_time = self.sunset + timedelta(minutes=sunset_offset)
                    current_time = self._get_current_time()
                    time_window = timedelta(minutes=1)

                    # If we just booted up, make sure the door is in the
                    # correct position.
                    if self.first_iter and not self.auto_close_retry_pending:
                        if current_time >= open_time and current_time < close_time:
                            global_vars.instance().set_value("desired_door_state", "open")
                        else:
                            global_vars.instance().set_value("desired_door_state", "closed")

                    # If we are in the 1 minute after sunrise, command open.
                    if current_time >= open_time and current_time <= open_time + time_window:
                        global_vars.instance().set_value("desired_door_state", "open")

                    # If we are in the 1 minute after sunset, command closed.
                    # Guard: don't override the premature-retry cooldown —
                    # the retry block will re-issue "closed" once the cooldown
                    # elapses.  Without this guard the sunset window rewrites
                    # "closed" every 0.5 s, bypassing the 5-second cooldown and
                    # resetting door_move_count to 0 on every iteration.
                    if (
                        current_time >= close_time
                        and current_time <= close_time + time_window
                        and not self.auto_close_retry_pending
                    ):
                        global_vars.instance().set_value("desired_door_state", "closed")

            # Poll endstops every iteration as a reliable safety-net.
            # Edge-detect callbacks can be missed (bounce, gevent blocking,
            # etc.), so this direct GPIO read guarantees the motor stops
            # within one iteration (~0.5 s) of reaching an endstop.
            if door.check_endstops():
                # An endstop was just triggered — re-read the door state
                # so the rest of this iteration uses the updated value.
                self.door_state = door.get_state()

            door_state = self.door_state  # local alias used throughout the block

            # ------------------------------------------------------------------
            # Premature lower-endstop detection during auto-close.
            #
            # Physical setup: the lower endstop sits at the motor (top of
            # door). It fires when the rope loses tension, i.e. when the
            # door rope goes slack — normally because the door has fully
            # reached the ground.  A chicken can hop through the narrowing
            # gap and push the door up momentarily, releasing rope tension
            # and triggering the endstop before the door is actually closed.
            #
            # Detection: if the door transitioned closing → closed in less
            # than PREMATURE_CLOSE_THRESHOLD * reference_time, it is
            # considered a false trigger.  We stop for 5 s then retry.
            # Three consecutive false triggers → error state.
            # ------------------------------------------------------------------
            if (
                auto_mode
                and not self.door_override
                and (self.was_door_closing or self._close_retry_just_fired)
                and door_state == "closed"
                and d_door_state == "closed"
                and door.startedMovingTime is not None
                and reference_door_endstops_ms is not None
            ):
                elapsed_close_s = time.time() - door.startedMovingTime
                ref_close_s = reference_door_endstops_ms / 1000.0
                if elapsed_close_s < ref_close_s * self.PREMATURE_CLOSE_THRESHOLD:
                    self.auto_close_premature_count += 1
                    logger.warning(
                        "Premature lower endstop during auto-close "
                        "(elapsed=%.2fs, ref=%.2fs, attempt=%d/%d)",
                        elapsed_close_s,
                        ref_close_s,
                        self.auto_close_premature_count,
                        self.PREMATURE_CLOSE_MAX_RETRIES,
                    )
                    if self.auto_close_premature_count >= self.PREMATURE_CLOSE_MAX_RETRIES:
                        error_msg = (
                            f"Auto-close failed: lower endstop triggered prematurely "
                            f"{self.auto_close_premature_count} times in a row."
                            f"(Max {self.PREMATURE_CLOSE_MAX_RETRIES} times)"
                        )
                        logger.critical(error_msg)
                        door.ErrorState(error_msg, stopDoor=True)
                        global_vars.instance().set_value("desired_door_state", "stopped")
                        self._send_notification("Door Error", error_msg)
                        self.auto_close_premature_count = 0
                        self.auto_close_retry_pending = False
                        self.auto_close_retry_time = None
                        self._close_retry_just_fired = False
                    else:
                        # Physically stop the motor and mark state as
                        # "stopped" so the desired-state mismatch logic will
                        # retrigger a close command after the cooldown.
                        door.stop(state="stopped")
                        door_state = door.get_state()
                        # Update the LOCAL d_door_state too so the drive block
                        # later in this same iteration does NOT see a mismatch
                        # and call door.close() again (which would immediately
                        # re-trigger the still-active lower endstop).
                        d_door_state = "stopped"
                        global_vars.instance().set_value("desired_door_state", "stopped")
                        self.auto_close_retry_pending = True
                        self.auto_close_retry_time = time.time() + 5.0
                        self._close_retry_just_fired = False
                        logger.info(
                            "Auto-close retry scheduled in 5 s (attempt %d/%d).",
                            self.auto_close_premature_count,
                            self.PREMATURE_CLOSE_MAX_RETRIES,
                        )
                else:
                    # Endstop triggered at the expected time — genuine close.
                    logger.debug(
                        "Auto-close timing valid (elapsed=%.2fs, ref=%.2fs). "
                        "Resetting premature counter.",
                        elapsed_close_s,
                        ref_close_s,
                    )
                    self.auto_close_premature_count = 0
                    self.auto_close_retry_pending = False
                    self.auto_close_retry_time = None
                    self._close_retry_just_fired = False

            # Fire the auto-close retry once the 5-second cooldown has elapsed.
            if (
                self.auto_close_retry_pending
                and self.auto_close_retry_time is not None
                and time.time() >= self.auto_close_retry_time
            ):
                logger.info("Auto-close retry: re-issuing close command.")
                self.auto_close_retry_pending = False
                self.auto_close_retry_time = None
                # Reset timing so premature detection measures from THIS retry attempt,
                # not from the original close that was interrupted.
                door.startedMovingTime = time.time()
                # Flag: force the drive block and premature detection to run even if
                # check_endstops() has already set door_state == d_door_state == "closed"
                # (i.e. the lower endstop is still physically active).
                self._close_retry_just_fired = True
                global_vars.instance().set_value("desired_door_state", "closed")
                # Re-read so this iteration's logic sees the updated desired state.
                d_door_state = "closed"
                # Keep last_d_door_state in sync so the next iteration's
                # "d_door_state != last_d_door_state" check does NOT fire and
                # reset door_move_count back to 0 (which would defeat the motor
                # budget timeout on every retry).
                self.last_d_door_state = "closed"

            # Reset retry state whenever we leave auto mode or enter override.
            if not auto_mode or self.door_override:
                self.auto_close_premature_count = 0
                self.auto_close_retry_pending = False
                self.auto_close_retry_time = None
                self._close_retry_just_fired = False

            # If we are in override mode, then the door is being moved by
            # the physical switch.
            if self.door_override:
                # See if switch is turned off; if so, stop the door.
                door.check_if_switch_neutral()
                # Set the desired state to the door's actual position so
                # we don't create a state mismatch when override ends.
                global_vars.instance().set_value("desired_door_state", door.get_state())

            # If the door state does not match the desired door state, move
            # the door.  Also force a close attempt when the retry just fired
            # but the lower endstop is still active (door_state == d_door_state ==
            # "closed" even though the motor never ran).
            elif door_state != d_door_state or (
                self._close_retry_just_fired and door_state == "closed"
            ):
                if door.ErrorState():
                    door.stop()
                    self.door_move_count = 0
                    if not self.sentErrorNotification:
                        self._send_notification(
                            "Door Error",
                            "The door is in an error state, please check the door.",
                        )
                        self.sentErrorNotification = True
                else:
                    endstopTimeout = door.reference_door_endstops_ms
                    if door.reference_door_endstops_ms is not None and isinstance(
                        endstopTimeout, (int, float)
                    ):
                        endstopTimeout = door.reference_door_endstops_ms * 0.001
                    if endstopTimeout is None:
                        endstopTimeout = 10
                        logger.debug(
                            f"Reference to endstops not set, the door will move "
                            f"{self.DOOR_MOVE_MAX_AFTER_ENDSTOPS + endstopTimeout} seconds."
                        )
                    match d_door_state:
                        case "stopped":
                            if (
                                door_state in ["open", "closed"]
                                and not self.auto_close_retry_pending
                            ):
                                # Normal reconcile: door reached an endstop
                                # while desired=stopped, so align desired to
                                # actual.  Skipped when a premature-endstop
                                # retry is pending to prevent the retry from
                                # being clobbered.
                                logger.debug(
                                    "stop: door already at endstop (%s), reconciling desired state",
                                    door_state,
                                )
                                door.stop(door_state)
                                global_vars.instance().set_value(
                                    "desired_door_state", door_state
                                )
                                self.door_move_count = 0
                            else:
                                logger.debug("stop because door is in moving state")
                                door.stop()
                                logger.debug(
                                    "SHOULD NOW BE STOPPED - door state: %s",
                                    door.get_state(),
                                )
                                self.door_move_count = 0
                        case "open":
                            if self.door_move_count <= (
                                endstopTimeout + self.DOOR_MOVE_MAX_AFTER_ENDSTOPS
                            ):
                                logger.debug(
                                    f"OPEN: current moveCount: {self.door_move_count} and "
                                    f"endstopTimeout {endstopTimeout} + "
                                    f"DOORMOVEMAX {self.DOOR_MOVE_MAX_AFTER_ENDSTOPS}"
                                )
                                door.open()
                                self.door_move_count += self.thread_sleep_time
                            else:
                                door.ErrorState("Endstop not reached")
                                global_vars.instance().set_value(
                                    "desired_door_state", "stopped"
                                )
                                self.door_move_count = 0
                        case "closed":
                            if self.door_move_count <= (
                                endstopTimeout + self.DOOR_MOVE_MAX_AFTER_ENDSTOPS
                            ):
                                logger.debug(
                                    f"CLOSE: current moveCount: {self.door_move_count} and "
                                    f"endstopTimeout {endstopTimeout} + "
                                    f"DOORMOVEMAX {self.DOOR_MOVE_MAX_AFTER_ENDSTOPS}"
                                )
                                door.close()
                                self.door_move_count += self.thread_sleep_time
                            else:
                                door.ErrorState("Endstop not reached")
                                global_vars.instance().set_value(
                                    "desired_door_state", "stopped"
                                )
                                self.door_move_count = 0
                        case _:
                            door.ErrorState(
                                "unknown state - i dont know how this could happen"
                            )
                            self.door_move_count = 0
                            assert False, "Unknown state: " + str(d_door_state)

            # We are not in switch override and the door is in the desired
            # state.  Make sure the motor is off.
            else:
                # Check if switch is off; if so, stop the door.
                door.check_if_switch_neutral(nuetral_state=door.get_state())
                self.door_move_count = 0

            # ------------------------------------------------------------------
            # Commit end-of-iteration state
            # ------------------------------------------------------------------
            self.first_iter = False
            self.door_state = door.get_state()
            self.door_override = door.get_override()

            # Record whether the door is currently closing so the next
            # iteration can detect a closing → closed transition.
            self.was_door_closing = self.door_state == "closing"

            global_vars.instance().set_values(
                {
                    "state": self.door_state,
                    "override": self.door_override,
                    "sunrise": self.sunrise,
                    "sunset": self.sunset,
                    "error_state": door.errorState if door.ErrorState() else "",
                }
            )

        return True
