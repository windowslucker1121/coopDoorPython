"""Door-related data structures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DoorState(str, Enum):
    """Canonical door states — mirrors the string values used in protected_dict."""

    STOPPED = "stopped"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True)
class CloseAttemptResult:
    """Outcome of a single closing-stroke endstop validation.

    Produced by :meth:`services.door_service.DoorService.validate_close_endstop`.

    Attributes
    ----------
    accepted:
        ``True`` when the closing stroke is genuine (elapsed time is within
        the acceptable window of the calibrated reference time).
    elapsed_ms:
        Actual closing stroke duration in milliseconds.
    reference_ms:
        Calibrated full-stroke reference time in milliseconds
        (``reference_door_endstops_ms`` from the reference sequence).
    attempt_number:
        1-based retry counter for the current closing sequence.
    obstruction_detected:
        ``True`` when the endstop fired more than
        ``OBSTRUCTION_TOLERANCE_MS`` (1 000 ms) earlier than expected,
        indicating a chicken or other obstruction under the door.
    """

    accepted: bool
    elapsed_ms: float
    reference_ms: float
    attempt_number: int
    obstruction_detected: bool
