"""The door control loop.

:class:`DoorController` decides where the door should be (manual commands,
schedule, manual override switch), drives the :class:`~coop.door.driver.DoorDriver`
towards it, and supervises the movement:

* **Move budget** — if an endstop is not reached within
  ``reference travel time + MOVE_MARGIN_S`` (measured with the monotonic
  clock) the door is put into a fault state.
* **Premature close detection** (auto / timer mode) — the lower endstop sits
  at the motor and fires when the rope goes slack.  A chicken pushing the
  door up while it closes also slackens the rope.  A close that reaches the
  lower endstop after less than ``PREMATURE_CLOSE_THRESHOLD`` of the
  reference travel time (accumulated over retries) is treated as false:
  stop, wait ``RETRY_DELAY_S`` and close again; after
  ``PREMATURE_CLOSE_MAX_RETRIES`` consecutive false closes → fault.
* **Schedule synchronisation** — on start-up, when auto/timer mode is
  switched on and after a fault is cleared, the door is moved to the
  position the schedule expects now; afterwards only open/close boundary
  crossings change the target, so manual commands in between are respected.

Other threads interact only through the thread-safe command methods
(:meth:`command`, :meth:`request_reference`, :meth:`clear_error`,
:meth:`inject_test_error`) and the immutable :attr:`status` snapshot.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from ..clock import Clock
from ..config import ConfigStore, Mode, Settings
from ..services.sun import SunCalculator
from .driver import DoorDriver
from .model import DesiredState, DoorState, DoorStatus
from .schedule import Schedule, SunSchedule, TimerSchedule, crossed_boundary, desired_at

logger = logging.getLogger(__name__)

Notify = Callable[[str, str], None]


@dataclass
class _Commands:
    desired: DesiredState | None = None
    reference: bool = False
    clear_error: bool = False
    test_error: bool = False


class DoorController:
    MOVE_MARGIN_S = 20.0            # extra time allowed beyond the reference travel
    DEFAULT_TRAVEL_S = 10.0         # assumed travel time without a reference
    PREMATURE_CLOSE_THRESHOLD = 0.8
    PREMATURE_CLOSE_MAX_RETRIES = 5
    RETRY_DELAY_S = 5.0
    IDLE_INTERVAL_S = 0.5
    MOVING_INTERVAL_S = 0.1
    RESYNC_GAP = timedelta(hours=6)  # wall-clock jump that forces a re-sync

    def __init__(self, driver: DoorDriver, config: ConfigStore, clock: Clock,
                 sun: Callable[[], SunCalculator], notify: Notify):
        self.driver = driver
        self._config = config
        self._clock = clock
        self._sun = sun
        self._notify_cb = notify

        self._lock = threading.Lock()
        self._commands = _Commands()

        self.desired = DesiredState.STOPPED
        self._resync = True
        self._prev_mode: Mode | None = None
        self._last_eval: datetime | None = None
        self._was_override = False
        self._budget_desired: DesiredState | None = None
        self._move_since: float | None = None

        # premature-close bookkeeping
        self.premature_count = 0
        self.retry_at: float | None = None
        self._retry_just_fired = False
        self.cumulative_close_s = 0.0
        self._was_closing = False

        self._error_notified = False
        self._override_notified = False

        self.position: float | None = None
        self._last_step: float | None = None
        self._status = DoorStatus()

    # ── thread-safe API ──────────────────────────────────────────────
    @property
    def status(self) -> DoorStatus:
        return self._status

    def command(self, desired: DesiredState) -> None:
        with self._lock:
            self._commands.desired = DesiredState(desired)

    def request_reference(self) -> None:
        with self._lock:
            self._commands.reference = True

    def clear_error(self) -> None:
        with self._lock:
            self._commands.clear_error = True

    def inject_test_error(self) -> None:
        with self._lock:
            self._commands.test_error = True

    @property
    def retry_pending(self) -> bool:
        return self.retry_at is not None

    # ── the loop body ────────────────────────────────────────────────
    def step(self) -> float:
        """Run one control iteration; returns the delay until the next one."""
        with self._lock:
            cmds, self._commands = self._commands, _Commands()
        mono = self._clock.monotonic()
        now = self._clock.now()
        settings = self._config.settings
        dt = 0.0 if self._last_step is None else max(0.0, mono - self._last_step)
        self._last_step = mono

        if cmds.test_error:
            self.driver.set_fault("Test Error")
        if cmds.clear_error:
            self._on_clear_error()
        if cmds.reference:
            self._run_reference()
            self._publish(self._config.settings, now)
            return self.IDLE_INTERVAL_S
        if cmds.desired is not None:
            logger.info("[Manual] desired door state: %s", cmds.desired.value)
            self.desired = cmds.desired
            self._reset_premature()

        self.driver.sync()

        if self.driver.fault:
            self._handle_fault()
            self._publish(settings, now)
            return self.IDLE_INTERVAL_S

        mode = self._effective_mode(settings)
        schedule = self._schedule(settings, mode)
        self._apply_schedule(mode, schedule, now)
        self._last_eval = now

        if mode is Mode.MANUAL or self.driver.override:
            self._reset_premature()
        else:
            self._check_premature_close(settings, mono)
            self._fire_retry(mono)

        self._track_desired_changes()
        if self.driver.override:
            self._move_since = None
        else:
            self._drive(settings, mono)

        self._was_closing = self.driver.state is DoorState.CLOSING
        self._update_position(settings, dt)
        self._publish(settings, now)
        moving = self.driver.state.moving
        return self.MOVING_INTERVAL_S if moving else self.IDLE_INTERVAL_S

    # ── helpers: commands / faults ───────────────────────────────────
    def _notify(self, title: str, body: str) -> None:
        try:
            self._notify_cb(title, body)
        except Exception:
            logger.exception("Sending notification failed")

    def _on_clear_error(self) -> None:
        self.driver.clear_fault()
        self._error_notified = False
        self._override_notified = False
        self._reset_premature()
        self._resync = True

    def _handle_fault(self) -> None:
        if not self._error_notified:
            self._notify("Door Error", "The door is in an error state, please check the door.")
            self._error_notified = True
        self._move_since = None
        self._reset_premature()
        self._was_closing = False

    def _run_reference(self) -> None:
        travel_ms = self.driver.reference()
        if travel_ms is None:
            logger.critical("Referencing door endstops failed, please check the door and try again.")
            return
        try:
            self._config.update(reference_travel_ms=travel_ms)
        except Exception as e:
            logger.error("Could not save the reference travel time: %s", e)
        self.position = 1.0
        # The sequence ends at the top: stay there (auto/timer re-sync below
        # moves the door if the schedule wants it elsewhere).
        self.desired = DesiredState.OPEN
        self._was_closing = False
        self._reset_premature()
        self._move_since = None
        self._resync = True

    # ── helpers: schedule ────────────────────────────────────────────
    def _effective_mode(self, settings: Settings) -> Mode:
        mode = settings.mode
        if mode is not Mode.MANUAL and settings.reference_travel_ms is None:
            logger.warning("Reference door endstops not set. Please run the reference sequence "
                           "from the WebUI - switching to manual mode.")
            try:
                self._config.update(mode=Mode.MANUAL)
            except Exception as e:
                logger.error("Could not switch to manual mode: %s", e)
            mode = Mode.MANUAL
        if mode is not self._prev_mode:
            if mode is not Mode.MANUAL:
                self._resync = True
            self._prev_mode = mode
        return mode

    def _schedule(self, settings: Settings, mode: Mode) -> Schedule | None:
        if mode is Mode.AUTO:
            return SunSchedule(self._sun(), settings.sunrise_offset, settings.sunset_offset)
        if mode is Mode.TIMER:
            return TimerSchedule(settings.timer_open_time, settings.timer_close_time)
        return None

    def _apply_schedule(self, mode: Mode, schedule: Schedule | None, now: datetime) -> None:
        override = self.driver.override
        if override:
            self._was_override = True
        elif self._was_override:
            # switch released: keep the door where the operator left it
            self._was_override = False
            self.desired = DesiredState.at_rest(self.driver.state)
        if not override:
            self._override_notified = False

        if schedule is None:
            self._resync = False
            return

        last = self._last_eval
        if last is not None and (now < last or now - last > self.RESYNC_GAP):
            self._resync = True  # wall clock jumped
        resync = self._resync
        target = desired_at(schedule, now) if resync else (
            crossed_boundary(schedule, last, now) if last is not None else None)

        if override:
            if (resync or target is not None) and not self._override_notified:
                who = "Auto-mode" if mode is Mode.AUTO else "Timer-mode"
                self._notify("Manual Override Active",
                             f"{who} wants to move the door, but the manual override switch is active.")
                logger.warning("%s door command blocked by the manual override switch.", who)
                self._override_notified = True
            self._resync = False
            return

        if target is None:
            if resync:
                self._resync = False  # no sun times today (polar) - nothing to sync
            return
        if self.retry_pending and target is DesiredState.CLOSED:
            return  # the retry will close the door after its cool-down
        if resync:
            self._resync = False
        if target is not self.desired:
            label = "[Auto Mode]" if mode is Mode.AUTO else "[Timer Mode]"
            logger.info("%s desired door state %s -> %s", label, self.desired.value, target.value)
        self.desired = target

    # ── helpers: premature close ─────────────────────────────────────
    def _reset_premature(self) -> None:
        self.premature_count = 0
        self.retry_at = None
        self._retry_just_fired = False
        self.cumulative_close_s = 0.0

    def _check_premature_close(self, settings: Settings, mono: float) -> None:
        ref_ms = settings.reference_travel_ms
        if not ((self._was_closing or self._retry_just_fired)
                and self.driver.state is DoorState.CLOSED
                and self.desired is DesiredState.CLOSED
                and self.driver.move_started is not None
                and ref_ms):
            return
        elapsed = mono - self.driver.move_started
        ref_s = ref_ms / 1000.0
        total = self.cumulative_close_s + elapsed
        if total >= ref_s * self.PREMATURE_CLOSE_THRESHOLD:
            logger.debug("Close accepted (elapsed %.2fs, total %.2fs, ref %.2fs)", elapsed, total, ref_s)
            self._reset_premature()
            return

        self.cumulative_close_s = total
        self.premature_count += 1
        logger.warning("Premature lower endstop during close (elapsed=%.2fs, cumulative=%.2fs, "
                       "ref=%.2fs, attempt=%d/%d)", elapsed, total, ref_s,
                       self.premature_count, self.PREMATURE_CLOSE_MAX_RETRIES)
        if self.premature_count >= self.PREMATURE_CLOSE_MAX_RETRIES:
            message = (f"Auto-close failed: lower endstop triggered prematurely "
                       f"{self.premature_count} times in a row.")
            self.driver.set_fault(message)
            self.desired = DesiredState.STOPPED
            self._notify("Door Error", message)
            self._error_notified = True
            self._reset_premature()
            return
        self.driver.stop()
        self.desired = DesiredState.STOPPED
        self.retry_at = mono + self.RETRY_DELAY_S
        self._retry_just_fired = False
        logger.info("Close retry scheduled in %.0f s (attempt %d/%d).", self.RETRY_DELAY_S,
                    self.premature_count, self.PREMATURE_CLOSE_MAX_RETRIES)

    def _fire_retry(self, mono: float) -> None:
        if self.retry_at is None or mono < self.retry_at:
            return
        logger.info("Close retry: closing again.")
        self.retry_at = None
        self.desired = DesiredState.CLOSED
        # Measure the next attempt from now, and force the drive block to
        # run even if the lower endstop is still active.
        self.driver.move_started = mono
        self._retry_just_fired = True

    # ── helpers: driving ─────────────────────────────────────────────
    def _track_desired_changes(self) -> None:
        if self.desired is not self._budget_desired:
            self._budget_desired = self.desired
            self._move_since = None

    def _drive(self, settings: Settings, mono: float) -> None:
        state = self.driver.state
        desired = self.desired

        if desired is DesiredState.STOPPED:
            self._move_since = None
            if state.moving:
                self.driver.stop()
            elif state in (DoorState.OPEN, DoorState.CLOSED) and not self.retry_pending:
                self.desired = DesiredState.at_rest(state)  # reached an endstop: reconcile
            return

        target_state = DoorState.OPEN if desired is DesiredState.OPEN else DoorState.CLOSED
        force = self._retry_just_fired and desired is DesiredState.CLOSED and state is DoorState.CLOSED
        if state is target_state and not force:
            self._move_since = None
            return

        travel_s = (settings.reference_travel_ms / 1000.0) if settings.reference_travel_ms else self.DEFAULT_TRAVEL_S
        if self._move_since is None:
            self._move_since = mono
        elif mono - self._move_since > travel_s + self.MOVE_MARGIN_S:
            self.driver.set_fault("Endstop not reached")
            self.desired = DesiredState.STOPPED
            self._move_since = None
            return
        if desired is DesiredState.OPEN:
            self.driver.open()
        else:
            self.driver.close()

    # ── helpers: status ──────────────────────────────────────────────
    def _update_position(self, settings: Settings, dt: float) -> None:
        state = self.driver.state
        if state is DoorState.OPEN:
            self.position = 1.0
        elif state is DoorState.CLOSED:
            self.position = 0.0
        elif settings.reference_travel_ms and state.moving and self.position is not None:
            delta = dt / (settings.reference_travel_ms / 1000.0)
            self.position += delta if state is DoorState.OPENING else -delta
            self.position = max(0.0, min(1.0, self.position))

    def _publish(self, settings: Settings, now: datetime) -> None:
        sunrise = sunset = open_time = close_time = None
        try:
            times = self._sun().sun_times(now.date())
            if times:
                sunrise, sunset = times
            schedule = self._schedule(settings, settings.mode)
            window = schedule.window(now.date(), now.tzinfo) if schedule else None
            if window:
                open_time, close_time = window.open_time, window.close_time
        except Exception as e:  # never let a status detail break the loop
            logger.debug("status: schedule unavailable: %s", e)
        self._status = DoorStatus(
            state=self.driver.state,
            desired=self.desired,
            override=self.driver.override,
            error=self.driver.fault,
            # Known at the endstops; between them only with a reference
            # travel time to integrate against (None → UI falls back to CSS).
            position=self.position if (settings.reference_travel_ms or self.driver.state in (
                DoorState.OPEN, DoorState.CLOSED)) else None,
            reference_ms=settings.reference_travel_ms,
            mode=settings.mode,
            sunrise=sunrise,
            sunset=sunset,
            open_time=open_time,
            close_time=close_time,
            reference_running=self.driver.reference_active,
            premature_close_count=self.premature_count,
            retry_pending=self.retry_pending,
        )
