"""Door domain types."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from ..config import Mode


class DoorState(str, enum.Enum):
    STOPPED = "stopped"
    OPENING = "opening"
    CLOSING = "closing"
    OPEN = "open"
    CLOSED = "closed"

    @property
    def moving(self) -> bool:
        return self in (DoorState.OPENING, DoorState.CLOSING)


class DesiredState(str, enum.Enum):
    STOPPED = "stopped"
    OPEN = "open"
    CLOSED = "closed"

    @classmethod
    def at_rest(cls, state: DoorState) -> "DesiredState":
        """Desired state that matches a door at rest in *state*."""
        if state is DoorState.OPEN:
            return cls.OPEN
        if state is DoorState.CLOSED:
            return cls.CLOSED
        return cls.STOPPED


@dataclass(frozen=True)
class DoorStatus:
    """Immutable snapshot published by the controller after every step."""

    state: DoorState = DoorState.STOPPED
    desired: DesiredState = DesiredState.STOPPED
    override: bool = False
    error: str | None = None
    position: float | None = None       # 0.0 closed … 1.0 open; None = unknown
    reference_ms: float | None = None
    mode: Mode = Mode.MANUAL
    sunrise: datetime | None = None
    sunset: datetime | None = None
    open_time: datetime | None = None   # today's schedule (auto / timer)
    close_time: datetime | None = None
    reference_running: bool = False
    premature_close_count: int = 0
    retry_pending: bool = False
