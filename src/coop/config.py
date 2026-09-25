"""Typed, validated application settings persisted in ``config.yaml``.

* All settings are **frozen dataclasses**: a :class:`Settings` object is an
  immutable snapshot that can be handed to any thread safely.
* :class:`ConfigStore` owns the current snapshot, applies validated updates,
  persists them **atomically** and notifies subscribers.
* Loading is lenient: an invalid value in the file is logged and replaced by
  its default instead of preventing the application from starting.  Unknown
  keys are preserved when the file is rewritten.
* The YAML layout is backward compatible with the original application
  (``auto_mode``/``timer_mode`` flags, ``csvLog``, string booleans, ...).
"""

from __future__ import annotations

import copy
import enum
import logging
import os
import threading
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from typing import Any, Callable, Iterable

import pytz
import ruamel.yaml as YAML

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when a setting fails validation.  The message is user facing."""


# ─────────────────────────────── value helpers ──────────────────────────────

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off", ""}


def parse_bool(value: Any) -> bool:
    """Parse JSON/YAML booleans and their common string/number spellings.

    (``bool("false")`` is ``True``, so a plain cast is wrong for form data.)
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
    raise ConfigError(f"{value!r} is not a boolean")


def parse_int(value: Any, name: str, lo: int | None = None, hi: int | None = None) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"'{name}' must be an integer")
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        result = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{name}' must be an integer") from None
    if (lo is not None and result < lo) or (hi is not None and result > hi):
        raise ConfigError(f"'{name}' must be {lo}–{hi}")
    return result


def parse_float(value: Any, name: str, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"'{name}' must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{name}' must be a number") from None
    if result != result:  # NaN
        raise ConfigError(f"'{name}' must be a number")
    if (lo is not None and result < lo) or (hi is not None and result > hi):
        raise ConfigError(f"'{name}' must be between {lo} and {hi}")
    return result


def parse_hhmm(value: Any, name: str) -> str:
    """Normalise a time of day to ``HH:MM`` (``H:MM`` and ``HH:MM:SS`` accepted)."""
    if isinstance(value, str):
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), fmt).strftime("%H:%M")
            except ValueError:
                continue
    raise ConfigError(f"'{name}' must be a time of day (HH:MM)")


# ─────────────────────────────── settings model ─────────────────────────────

class Mode(str, enum.Enum):
    """Who decides where the door should be."""

    MANUAL = "manual"   # only explicit commands (UI / physical switch)
    AUTO = "auto"       # sunrise / sunset (+ offsets) at the configured location
    TIMER = "timer"     # fixed daily open / close times


class OutdoorSensorType(str, enum.Enum):
    DHT22 = "dht22"
    API = "api"


GPIO_PIN_FIELDS = (
    "motor_in1", "motor_in2", "motor_ena",
    "endstop_up", "endstop_down",
    "override_open", "override_close",
    "dht11_data", "dht22_data", "dht22_power",
)
GPIO_BOOL_FIELDS = ("invert_end_up", "invert_end_down")
MAX_SUN_OFFSET_MIN = 720


@dataclass(frozen=True)
class GpioConfig:
    """BCM pin assignment.  ``dht22_power`` may be ``None`` (no power cycling)."""

    motor_in1: int = 17
    motor_in2: int = 27
    motor_ena: int = 22
    endstop_up: int = 23
    endstop_down: int = 24
    override_open: int = 5
    override_close: int = 6
    dht11_data: int = 26
    dht22_data: int = 21
    dht22_power: int | None = 20
    invert_end_up: bool = False
    invert_end_down: bool = False
    reference_timeout: int = 60  # seconds per reference leg

    def validate(self) -> "GpioConfig":
        errors = []
        seen: dict[int, str] = {}
        for name in GPIO_PIN_FIELDS:
            pin = getattr(self, name)
            if pin is None and name == "dht22_power":
                continue
            if not isinstance(pin, int) or isinstance(pin, bool) or not 0 <= pin <= 40:
                errors.append(f"'{name}' must be 0–40")
                continue
            if pin in seen:
                errors.append(f"'{name}' and '{seen[pin]}' both use GPIO {pin}")
            else:
                seen[pin] = name
        if not 5 <= self.reference_timeout <= 600:
            errors.append("'reference_timeout' must be 5–600 seconds")
        if errors:
            raise ConfigError("; ".join(errors))
        return self

    def merged(self, data: dict) -> "GpioConfig":
        """Return a copy with values from *data* (API / YAML input) applied."""
        changes: dict[str, Any] = {}
        errors = []
        for name in GPIO_PIN_FIELDS:
            if name not in data:
                continue
            if name == "dht22_power" and data[name] in (None, ""):
                changes[name] = None
                continue
            try:
                changes[name] = parse_int(data[name], name)
            except ConfigError as e:
                errors.append(str(e))
        for name in GPIO_BOOL_FIELDS:
            if name in data:
                try:
                    changes[name] = parse_bool(data[name])
                except ConfigError:
                    errors.append(f"'{name}' must be a boolean")
        if "reference_timeout" in data:
            try:
                changes["reference_timeout"] = parse_int(data["reference_timeout"], "reference_timeout")
            except ConfigError as e:
                errors.append(str(e))
        candidate = replace(self, **changes)
        try:
            candidate.validate()
        except ConfigError as e:
            errors.append(str(e))
        if errors:
            raise ConfigError("; ".join(errors))
        return candidate

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class WifiConfig:
    ssid: str = ""
    password: str = ""
    timeout: int = 60
    ap_ssid: str = "DINKY-COOP"
    ap_password: str = "password"
    ap_ip: str = "10.42.0.1"
    ap_allowed_hosts: tuple[str, ...] = ()

    def validate(self) -> "WifiConfig":
        if not self.ap_ssid.strip():
            raise ConfigError("AP SSID must not be empty")
        # WPA2 needs 8–63 characters; nmcli refuses to start the hotspot
        # otherwise, which would leave the device unreachable.
        if not 8 <= len(self.ap_password) <= 63:
            raise ConfigError("AP password must be 8-63 characters")
        if self.timeout <= 0:
            raise ConfigError("Timeout must be positive")
        return self

    def merged(self, data: dict) -> "WifiConfig":
        changes: dict[str, Any] = {}
        for name in ("ssid", "password", "ap_ssid", "ap_password", "ap_ip"):
            if name in data and data[name] is not None:
                changes[name] = str(data[name])
        if "timeout" in data:
            try:
                changes["timeout"] = parse_int(data["timeout"], "timeout")
            except ConfigError:
                raise ConfigError("Timeout must be an integer") from None
        if "ap_allowed_hosts" in data:
            hosts = data["ap_allowed_hosts"] or []
            if not isinstance(hosts, (list, tuple)):
                raise ConfigError("'ap_allowed_hosts' must be a list")
            changes["ap_allowed_hosts"] = tuple(str(h).lower() for h in hosts)
        return replace(self, **changes).validate()

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["ap_allowed_hosts"] = list(self.ap_allowed_hosts)
        return d


@dataclass(frozen=True)
class LocationConfig:
    city: str = "Boulder"
    region: str = "USA"
    timezone: str = "America/Denver"
    latitude: float = 40.01499
    longitude: float = -105.27055

    def validate(self) -> "LocationConfig":
        try:
            pytz.timezone(self.timezone)
        except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
            raise ConfigError(f"Unknown timezone {self.timezone!r}") from None
        parse_float(self.latitude, "latitude", -90.0, 90.0)
        parse_float(self.longitude, "longitude", -180.0, 180.0)
        return self

    @classmethod
    def from_dict(cls, data: dict) -> "LocationConfig":
        if not isinstance(data, dict):
            raise ConfigError("Location must be an object")
        return cls(
            city=str(data.get("city") or ""),
            region=str(data.get("region") or ""),
            timezone=data.get("timezone") if isinstance(data.get("timezone"), str) else "",
            latitude=parse_float(data.get("latitude"), "latitude", -90.0, 90.0),
            longitude=parse_float(data.get("longitude"), "longitude", -180.0, 180.0),
        ).validate()

    @property
    def tz(self):
        return pytz.timezone(self.timezone)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class AuthConfig:
    """Optional HTTP Basic authentication for the web interface.

    Disabled while ``password_hash`` is empty.  Set a password with
    ``python -m coop set-password``.
    """

    username: str = "admin"
    password_hash: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.password_hash)

    def to_dict(self) -> dict:
        return {"username": self.username, "password_hash": self.password_hash}


@dataclass(frozen=True)
class Settings:
    use_mock_hardware: bool = False
    simulate_door: bool = True  # only used with mock hardware
    simulator_travel_s: float = 8.0  # simulated door travel time
    mode: Mode = Mode.AUTO
    timer_open_time: str = "07:00"
    timer_close_time: str = "20:00"
    sunrise_offset: int = 0
    sunset_offset: int = 0
    location: LocationConfig = field(default_factory=LocationConfig)
    csv_log: bool = True
    enable_camera: bool = False
    camera_index: int = 0
    outdoor_sensor_type: OutdoorSensorType = OutdoorSensorType.DHT22
    gpio: GpioConfig = field(default_factory=GpioConfig)
    wifi: WifiConfig = field(default_factory=WifiConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    reference_travel_ms: float | None = None
    log_level: str = "INFO"
    extra: dict = field(default_factory=dict, compare=False)  # unknown YAML keys

    # ------------------------------------------------------------------
    @property
    def auto_mode(self) -> bool:
        return self.mode is Mode.AUTO

    @property
    def timer_mode(self) -> bool:
        return self.mode is Mode.TIMER

    def validate(self, previous: "Settings | None" = None) -> "Settings":
        """Validate this snapshot.

        With *previous*, the nested sections (location / gpio / wifi) are
        only validated when they changed, so a legacy value that predates
        a validation rule (e.g. a short AP password) never blocks unrelated
        updates such as switching the mode.
        """
        parse_hhmm(self.timer_open_time, "timer_open_time")
        parse_hhmm(self.timer_close_time, "timer_close_time")
        parse_int(self.sunrise_offset, "sunrise_offset", -MAX_SUN_OFFSET_MIN, MAX_SUN_OFFSET_MIN)
        parse_int(self.sunset_offset, "sunset_offset", -MAX_SUN_OFFSET_MIN, MAX_SUN_OFFSET_MIN)
        parse_int(self.camera_index, "camera_index", 0, 64)
        if self.reference_travel_ms is not None and not self.reference_travel_ms > 0:
            raise ConfigError("'reference_travel_ms' must be positive")
        parse_float(self.simulator_travel_s, "simulator_travel_s", 1, 120)
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ConfigError("'log_level' must be DEBUG, INFO, WARNING or ERROR")
        for section in ("location", "gpio", "wifi"):
            value = getattr(self, section)
            if previous is None or value != getattr(previous, section):
                value.validate()
        return self

    # ── YAML (de)serialisation ─────────────────────────────────────────
    def to_yaml_dict(self) -> dict:
        data = dict(self.extra)
        data.update({
            "use_mock_hardware": self.use_mock_hardware,
            "simulate_door": self.simulate_door,
            "simulator_travel_s": self.simulator_travel_s,
            "auto_mode": self.mode is Mode.AUTO,
            "timer_mode": self.mode is Mode.TIMER,
            "timer_open_time": self.timer_open_time,
            "timer_close_time": self.timer_close_time,
            "sunrise_offset": self.sunrise_offset,
            "sunset_offset": self.sunset_offset,
            "location": self.location.to_dict(),
            "csvLog": self.csv_log,
            "enable_camera": self.enable_camera,
            "camera_index": self.camera_index,
            "outdoor_sensor_type": self.outdoor_sensor_type.value,
            "gpio": self.gpio.to_dict(),
            "wifi": self.wifi.to_dict(),
            "auth": self.auth.to_dict(),
            "reference_door_endstops_ms": self.reference_travel_ms,
            "log_level": self.log_level,
        })
        return data

    @classmethod
    def from_yaml_dict(cls, raw: dict) -> "Settings":
        """Build settings from a YAML mapping, falling back to defaults for
        every invalid value (logged) so a bad file never prevents booting."""
        d = cls()
        raw = dict(raw or {})
        known = {
            "use_mock_hardware", "simulate_door", "simulator_travel_s", "auto_mode", "timer_mode", "mode",
            "timer_open_time", "timer_close_time", "sunrise_offset", "sunset_offset",
            "location", "csvLog", "csv_log", "enable_camera", "camera_index",
            "outdoor_sensor_type", "gpio", "wifi", "auth", "reference_door_endstops_ms",
            "reference_travel_ms", "log_level",
            "consoleLogToFile",  # obsolete, dropped
        }
        values: dict[str, Any] = {}

        def take(key, parser, target=None):
            if key not in raw or raw[key] is None and parser is not _nullable_float:
                return
            try:
                values[target or key] = parser(raw[key])
            except (ConfigError, ValueError, TypeError) as e:
                logger.warning("config.yaml: ignoring invalid %s=%r (%s)", key, raw[key], e)

        take("use_mock_hardware", parse_bool)
        take("simulate_door", parse_bool)
        take("simulator_travel_s", lambda v: parse_float(v, "simulator_travel_s", 1, 120))
        take("timer_open_time", lambda v: parse_hhmm(v, "timer_open_time"))
        take("timer_close_time", lambda v: parse_hhmm(v, "timer_close_time"))
        take("sunrise_offset", lambda v: parse_int(v, "sunrise_offset", -MAX_SUN_OFFSET_MIN, MAX_SUN_OFFSET_MIN))
        take("sunset_offset", lambda v: parse_int(v, "sunset_offset", -MAX_SUN_OFFSET_MIN, MAX_SUN_OFFSET_MIN))
        take("csvLog", parse_bool, "csv_log")
        take("csv_log", parse_bool)
        take("enable_camera", parse_bool)
        take("camera_index", lambda v: parse_int(v, "camera_index", 0, 64))
        take("outdoor_sensor_type", lambda v: OutdoorSensorType(str(v).lower()))
        take("reference_door_endstops_ms", _nullable_float, "reference_travel_ms")
        take("reference_travel_ms", _nullable_float)
        take("log_level", _log_level)
        take("location", LocationConfig.from_dict)
        take("gpio", _lenient_gpio)
        take("wifi", lambda v: _lenient_wifi(v))
        take("auth", lambda v: AuthConfig(str(v.get("username") or "admin"), str(v.get("password_hash") or "")))

        # Mode: explicit "mode" wins, otherwise the legacy flag pair.
        mode = d.mode
        if "mode" in raw:
            try:
                mode = Mode(str(raw["mode"]).lower())
            except ValueError:
                logger.warning("config.yaml: ignoring invalid mode=%r", raw["mode"])
        elif "auto_mode" in raw or "timer_mode" in raw:
            try:
                if parse_bool(raw.get("auto_mode", False)):
                    mode = Mode.AUTO
                elif parse_bool(raw.get("timer_mode", False)):
                    mode = Mode.TIMER
                else:
                    mode = Mode.MANUAL
            except ConfigError:
                logger.warning("config.yaml: ignoring invalid auto_mode/timer_mode")
        values["mode"] = mode

        extra = {k: copy.deepcopy(v) for k, v in raw.items() if k not in known}
        return replace(d, extra=extra, **values)


def _nullable_float(v):
    if v is None:
        return None
    result = parse_float(v, "reference_door_endstops_ms")
    if result <= 0:
        raise ConfigError("must be positive")
    return result


def _log_level(v):
    level = str(v).upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError("unknown log level")
    return level


def _lenient_gpio(v) -> GpioConfig:
    """GPIO settings from YAML: coerce types per field, keep the user's
    layout even if it would fail validation (never silently move pins)."""
    if not isinstance(v, dict):
        raise ConfigError("gpio must be a mapping")
    base = GpioConfig()
    changes: dict[str, Any] = {}
    for name in GPIO_PIN_FIELDS + ("reference_timeout",):
        if name not in v:
            continue
        if name == "dht22_power" and v[name] in (None, ""):
            changes[name] = None
            continue
        try:
            changes[name] = parse_int(v[name], name)
        except ConfigError:
            logger.warning("config.yaml: ignoring invalid gpio.%s=%r", name, v[name])
    for name in GPIO_BOOL_FIELDS:
        if name in v:
            try:
                changes[name] = parse_bool(v[name])
            except ConfigError:
                logger.warning("config.yaml: ignoring invalid gpio.%s=%r", name, v[name])
    result = replace(base, **changes)
    try:
        result.validate()
    except ConfigError as e:
        logger.warning("config.yaml: GPIO configuration looks wrong: %s", e)
    return result


def _lenient_wifi(v) -> WifiConfig:
    """WiFi settings from YAML.  Unlike the API, an existing (e.g. short)
    AP password is kept so upgrading never silently changes credentials;
    only type errors fall back to defaults."""
    if not isinstance(v, dict):
        raise ConfigError("wifi must be a mapping")
    base = WifiConfig()
    hosts = v.get("ap_allowed_hosts") or []
    return replace(
        base,
        ssid=str(v.get("ssid", base.ssid) or ""),
        password=str(v.get("password", base.password) or ""),
        timeout=parse_int(v.get("timeout", base.timeout), "timeout", 1),
        ap_ssid=str(v.get("ap_ssid", base.ap_ssid) or base.ap_ssid),
        ap_password=str(v.get("ap_password", base.ap_password) or base.ap_password),
        ap_ip=str(v.get("ap_ip", base.ap_ip) or base.ap_ip),
        ap_allowed_hosts=tuple(str(h).lower() for h in hosts) if isinstance(hosts, (list, tuple)) else (),
    )


# ─────────────────────────────── the store ──────────────────────────────────

Listener = Callable[[Settings, Settings], None]


class ConfigStore:
    """Thread-safe owner of the current :class:`Settings`.

    ``update()`` validates the new snapshot, writes it atomically and only
    then publishes it — a failed validation or write leaves everything as it
    was.
    """

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.RLock()
        self._settings = Settings()
        self._listeners: list[Listener] = []

    @property
    def path(self) -> str:
        return self._path

    @property
    def settings(self) -> Settings:
        return self._settings

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    # ------------------------------------------------------------------
    def load(self) -> Settings:
        """Load the file (creating it with defaults if missing/invalid)."""
        with self._lock:
            rewrite = False
            raw: Any = None
            if os.path.exists(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        raw = YAML.YAML(typ="safe").load(f)
                except Exception as e:  # corrupt YAML
                    logger.error("config.yaml could not be parsed (%s) - using defaults.", e)
                    raw = None
                if not isinstance(raw, dict):
                    logger.error("config.yaml is empty or invalid - using defaults.")
                    raw = {}
                    rewrite = True
            else:
                logger.info("No configuration file found, creating a new one.")
                raw = {}
                rewrite = True
            self._settings = Settings.from_yaml_dict(raw)
            if rewrite:
                self._write(self._settings)
            return self._settings

    def update(self, _fn: Callable[[Settings], Settings] | None = None, **changes) -> Settings:
        """Apply *changes* (or ``_fn(settings)``), validate, persist, publish.

        Raises :class:`ConfigError` if the result is invalid.
        """
        with self._lock:
            old = self._settings
            new = _fn(old) if _fn is not None else replace(old, **changes)
            new.validate(previous=old)
            if new == old and new.extra == old.extra:
                return old
            self._write(new)
            self._settings = new
        for listener in list(self._listeners):
            try:
                listener(old, new)
            except Exception:
                logger.exception("Config listener failed")
        return new

    def _write(self, settings: Settings) -> None:
        """Write via a temporary file + ``os.replace`` so a power cut can
        never leave a truncated config behind."""
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            YAML.YAML().dump(settings.to_yaml_dict(), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)


def iter_settings_changes(old: Settings, new: Settings) -> Iterable[str]:
    """Names of top-level settings that differ between two snapshots."""
    for f in fields(Settings):
        if getattr(old, f.name) != getattr(new, f.name):
            yield f.name
