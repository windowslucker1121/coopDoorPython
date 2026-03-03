"""Configuration data structures.

These dataclasses mirror the schema of ``config.yaml`` and are used by
:class:`services.config_service.ConfigService` for validation and defaults.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class LocationConfig:
    """Geographic location used for sunrise/sunset calculations."""

    city: str = "Boulder"
    region: str = "USA"
    timezone: str = "America/Denver"
    latitude: float = 40.01499
    longitude: float = -105.27055

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class AppConfig:
    """Full application configuration.

    Note
    ----
    ``auto_mode`` is intentionally stored as the strings ``"True"`` /
    ``"False"`` for backwards compatibility with ``config.yaml``. Conversion
    to ``bool`` should happen at task / handler boundaries only.
    """

    auto_mode: str = "True"
    sunrise_offset: int = 0
    sunset_offset: int = 0
    location: LocationConfig = field(default_factory=LocationConfig)
    consoleLogToFile: bool = False
    csvLog: bool = True
    enable_camera: bool = False
    camera_index: int = 0
    reference_door_endstops_ms: float | None = None

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        return d
