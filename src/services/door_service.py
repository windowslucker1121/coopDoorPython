"""Door service — orchestration wrapper over the hardware DOOR class.

Responsibilities
----------------
* Provide a clean interface to the hardware ``DOOR`` instance.
* Implement the obstruction-detection policy for closing strokes:
  if the lower rope-slack endstop fires more than
  ``OBSTRUCTION_TOLERANCE_MS`` (1 s) earlier than the calibrated reference
  time, the closing stroke is considered blocked by a foreign object
  (e.g. a chicken) and ``CloseAttemptResult.obstruction_detected`` is set.
* Expose ``resume_after_obstruction()`` so ``door_task`` can reset state
  and allow the closing sequence to retry.

The opening direction is *never* validated here — the feature only applies
to closing.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from door import DOOR
from models.door_model import CloseAttemptResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: Milliseconds by which ``reference_ms`` must exceed ``elapsed_ms`` before
#: the closing stroke is considered obstructed.  1 000 ms = 1 second.
OBSTRUCTION_TOLERANCE_MS: float = 1_000.0

#: Maximum number of consecutive obstruction detections before the door
#: enters error state and a push notification is dispatched.
MAX_CLOSE_RETRIES: int = 3


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class AbstractDoorService(ABC):
    """Abstract base class defining the full door-service contract."""

    @abstractmethod
    def get_state(self) -> str: ...

    @abstractmethod
    def get_override(self) -> bool: ...

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def stop(self, state: str = "stopped") -> None: ...

    @abstractmethod
    def check_endstops(self) -> bool: ...

    @abstractmethod
    def check_if_switch_neutral(self, neutral_state: str = "stopped") -> None: ...

    @abstractmethod
    def reference_endstops(self) -> bool: ...

    @abstractmethod
    def set_auto_mode(self, auto_mode: bool) -> None: ...

    @abstractmethod
    def set_error(self, message: str) -> None: ...

    @abstractmethod
    def clear_error(self) -> None: ...

    @abstractmethod
    def is_error(self) -> bool: ...

    @abstractmethod
    def error_state(self) -> str | None: ...

    @abstractmethod
    def get_reference_ms(self) -> float | None: ...

    @abstractmethod
    def get_elapsed_closing_ms(self) -> float | None: ...

    @abstractmethod
    def validate_close_endstop(
        self,
        elapsed_ms: float,
        reference_ms: float,
        attempt_number: int,
    ) -> CloseAttemptResult: ...

    @abstractmethod
    def resume_after_obstruction(self) -> None: ...


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------


class DoorService(AbstractDoorService):
    """Concrete door service wrapping the hardware :class:`door.DOOR` class.

    Parameters
    ----------
    door:
        A fully-initialised :class:`door.DOOR` hardware instance.
    """

    def __init__(self, door: DOOR) -> None:
        self._door = door

    # ------------------------------------------------------------------
    # Basic state access
    # ------------------------------------------------------------------

    def get_state(self) -> str:
        return self._door.get_state()

    def get_override(self) -> bool:
        return self._door.get_override()

    # ------------------------------------------------------------------
    # Motor control (direct delegation)
    # ------------------------------------------------------------------

    def open(self) -> None:
        self._door.open()

    def close(self) -> None:
        self._door.close()

    def stop(self, state: str = "stopped") -> None:
        self._door.stop(state=state)

    # ------------------------------------------------------------------
    # Endstop / switch helpers
    # ------------------------------------------------------------------

    def check_endstops(self) -> bool:
        return self._door.check_endstops()

    def check_if_switch_neutral(self, neutral_state: str = "stopped") -> None:
        # DOOR's parameter spelling is deliberately preserved
        self._door.check_if_switch_neutral(nuetral_state=neutral_state)

    # ------------------------------------------------------------------
    # Reference sequence
    # ------------------------------------------------------------------

    def reference_endstops(self) -> bool:
        return self._door.reference_endstops()

    # ------------------------------------------------------------------
    # Auto-mode flag
    # ------------------------------------------------------------------

    def set_auto_mode(self, auto_mode: bool) -> None:
        self._door.set_auto_mode(auto_mode)

    # ------------------------------------------------------------------
    # Error state management
    # ------------------------------------------------------------------

    def set_error(self, message: str) -> None:
        self._door.ErrorState(state=message, stopDoor=True)

    def clear_error(self) -> None:
        self._door.clear_errorState()

    def is_error(self) -> bool:
        return bool(self._door.ErrorState())

    def error_state(self) -> str | None:
        return self._door.errorState

    # ------------------------------------------------------------------
    # Reference time helpers
    # ------------------------------------------------------------------

    def get_reference_ms(self) -> float | None:
        return self._door.reference_door_endstops_ms

    # ------------------------------------------------------------------
    # Obstruction-detection helpers
    # ------------------------------------------------------------------

    def get_elapsed_closing_ms(self) -> float | None:
        """Return milliseconds elapsed since the closing stroke began.

        Returns ``None`` if ``startedMovingTime`` has not been recorded yet
        (e.g. door had not moved in the current session).
        """
        start = self._door.get_started_moving_time()
        if start is None:
            return None
        return (time.time() - start) * 1_000.0

    def validate_close_endstop(
        self,
        elapsed_ms: float,
        reference_ms: float,
        attempt_number: int,
    ) -> CloseAttemptResult:
        """Validate whether a closing-stroke endstop trigger is genuine.

        The lower endstop (BCM 24) is a rope-slack sensor mounted at the top
        of the door frame.  When a chicken is under the door the rope goes
        slack prematurely and the sensor fires before the door is fully
        closed.  By comparing the actual closing-stroke duration
        (``elapsed_ms``) against the calibrated reference
        (``reference_ms``), we can distinguish a genuine close from an
        obstruction.

        Obstruction criterion
        ~~~~~~~~~~~~~~~~~~~~~
        ``reference_ms - elapsed_ms > OBSTRUCTION_TOLERANCE_MS`` (1 s)

        This deliberately uses the reference travel time in *both* directions
        even though the reference sequence only measures the opening stroke —
        the strokes are assumed to be approximately symmetric in duration.

        This method is **only called for closing strokes**; it is never
        invoked during opening.

        Parameters
        ----------
        elapsed_ms:
            Milliseconds the motor has been running in the closing direction
            at the moment the lower endstop fired.
        reference_ms:
            Calibrated full-stroke duration from ``reference_door_endstops_ms``.
        attempt_number:
            1-based counter of how many closing attempts have been made in
            the current closing sequence (thread-local state owned by
            ``door_task``).

        Returns
        -------
        CloseAttemptResult
        """
        obstruction = (reference_ms - elapsed_ms) > OBSTRUCTION_TOLERANCE_MS
        if obstruction:
            logger.warning(
                "validate_close_endstop: obstruction flag raised "
                "(elapsed=%.0f ms, reference=%.0f ms, tolerance=%.0f ms, attempt=%d)",
                elapsed_ms,
                reference_ms,
                OBSTRUCTION_TOLERANCE_MS,
                attempt_number,
            )
        return CloseAttemptResult(
            accepted=not obstruction,
            elapsed_ms=elapsed_ms,
            reference_ms=reference_ms,
            attempt_number=attempt_number,
            obstruction_detected=obstruction,
        )

    def resume_after_obstruction(self) -> None:
        """Stop the motor and reset internal door state to ``'stopped'``.

        Called by ``door_task`` when an obstruction is detected but the retry
        limit has not yet been reached.  Stopping the motor with state
        ``'stopped'`` means the next ``door_task`` iteration will see a
        mismatch between ``door_state`` (``'stopped'``) and
        ``desired_door_state`` (``'closed'``), causing it to re-initiate the
        closing sequence automatically.
        """
        logger.info("resume_after_obstruction: stopping motor, resetting state to 'stopped'")
        self._door.stop(state="stopped")
