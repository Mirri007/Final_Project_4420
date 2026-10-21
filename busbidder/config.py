"""Application settings, read from config/settings.json.

Settings that change scoring behaviour live in a file rather than in code, so an experiment
such as switching the normalisation method is reproducible and documented alongside the
results it produced.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from busbidder.exceptions import ConfigError, InvalidRequestError
from busbidder.request import DepartureWindow, parse_clock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"

logger = logging.getLogger(__name__)

_DEFAULTS: dict = {
    "data_dir": "data",
    "output_dir": "output",
    "log_file": "logs/busbidder.log",
    "profiles_file": "config/profiles.json",
    "normalisation": "ratio",
    "departure_tolerance_min": 240,
    "missing_value_score": 50,
    "dominance_warning_threshold": 0.5,
    "cancellation_fee_pct": 10,
    "departure_windows": {
        "morning": ["06:00", "12:00"],
        "afternoon": ["12:00", "18:00"],
        "evening": ["18:00", "23:00"],
        "night": ["23:00", "06:00"],
    },
}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    output_dir: Path
    log_file: Path
    profiles_file: Path
    normalisation: str = "ratio"
    departure_tolerance_min: int = 240
    missing_value_score: float = 50.0
    dominance_warning_threshold: float = 0.5
    cancellation_fee_pct: float = 10.0
    departure_windows: dict[str, DepartureWindow] = field(default_factory=dict)

    @property
    def bookings_file(self) -> Path:
        return self.output_dir / "bookings.json"

    @property
    def quarantine_file(self) -> Path:
        return self.output_dir / "rejected_offers.csv"


def _number(raw: dict, key: str, low: float, high: float) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ConfigError(f"Setting '{key}' must be a number between {low:g} and {high:g} (got {value!r})")
    return value


def _windows(raw: object) -> dict[str, DepartureWindow]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("Setting 'departure_windows' must map names to [start, end] pairs")
    windows = {}
    for name, bounds in raw.items():
        if not (isinstance(bounds, list) and len(bounds) == 2 and all(isinstance(b, str) for b in bounds)):
            raise ConfigError(f"Departure window '{name}' must be a [\"HH:MM\", \"HH:MM\"] pair")
        try:
            windows[name.lower()] = DepartureWindow(name.lower(), parse_clock(bounds[0]), parse_clock(bounds[1]))
        except InvalidRequestError as exc:
            raise ConfigError(f"Departure window '{name}': {exc}") from exc
    return windows


def load_settings(path: Path | str | None = None, root: Path = PROJECT_ROOT) -> Settings:
    """Read settings, falling back to built-in defaults when the default file is absent.

    An explicitly requested file that does not exist is an error, because silently ignoring
    it would make results irreproducible.
    """
    explicit = path is not None
    path = Path(path) if explicit else DEFAULT_CONFIG_PATH
    raw: dict = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a JSON object")
    elif explicit:
        raise ConfigError(f"Config file not found: {path}")
    else:
        logger.warning("No config file at %s; using built-in defaults", path)

    unknown = sorted(set(raw) - set(_DEFAULTS))
    if unknown:
        raise ConfigError(f"Unknown setting(s) in {path}: {', '.join(unknown)}")
    merged = {**_DEFAULTS, **raw}

    def resolve(key: str) -> Path:
        value = merged[key]
        if not isinstance(value, str) or not value:
            raise ConfigError(f"Setting '{key}' must be a non-empty path string")
        p = Path(value)
        return p if p.is_absolute() else root / p

    if not isinstance(merged["normalisation"], str):
        raise ConfigError("Setting 'normalisation' must be a string")

    return Settings(
        data_dir=resolve("data_dir"),
        output_dir=resolve("output_dir"),
        log_file=resolve("log_file"),
        profiles_file=resolve("profiles_file"),
        normalisation=merged["normalisation"],
        departure_tolerance_min=int(_number(merged, "departure_tolerance_min", 1, 720)),
        missing_value_score=float(_number(merged, "missing_value_score", 0, 100)),
        dominance_warning_threshold=float(_number(merged, "dominance_warning_threshold", 0, 1)),
        cancellation_fee_pct=float(_number(merged, "cancellation_fee_pct", 0, 100)),
        departure_windows=_windows(merged["departure_windows"]),
    )
