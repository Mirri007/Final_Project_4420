"""Traveller profiles: named bundles of hard constraints, a departure preference and weights.

Profiles live in config/profiles.json so new travellers can be added without code changes.
A profile does not fix the trip itself (destination, dates, passengers); that comes from the
search, so every profile can be compared on exactly the same offers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from busbidder.exceptions import DataFileError, InvalidRequestError, InvalidWeightsError, ProfileError
from busbidder.models import Destination
from busbidder.request import DepartureWindow, TravelRequest, parse_departure_preference
from busbidder.scoring import Weights

# Constraint name -> accepted JSON type(s). bool is checked separately because it is an int subtype.
ALLOWED_CONSTRAINTS: dict[str, type | tuple[type, ...]] = {
    "max_price_eur": (int, float),
    "max_transfers": int,
    "max_duration_min": int,
    "min_luggage_kg": int,
    "require_changeable": bool,
    "require_refundable": bool,
}
PROFILE_FIELDS = {"name", "description", "preferred_departure", "constraints", "weights"}


@dataclass(frozen=True)
class TravellerProfile:
    key: str
    name: str
    description: str
    weights: Weights
    constraints: Mapping[str, object]
    preferred_departure: DepartureWindow | None

    def build_request(self, destination: Destination, date_from: date, date_to: date,
                      passengers: int = 1) -> TravelRequest:
        return TravelRequest(
            destination=destination,
            date_from=date_from,
            date_to=date_to,
            passengers=passengers,
            preferred_departure=self.preferred_departure,
            **self.constraints,
        )


def _check_constraints(key: str, raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ProfileError(f"Profile '{key}': 'constraints' must be an object")
    for name, value in raw.items():
        if name not in ALLOWED_CONSTRAINTS:
            raise ProfileError(f"Profile '{key}': unknown constraint '{name}'. "
                               f"Allowed: {', '.join(ALLOWED_CONSTRAINTS)}")
        expected = ALLOWED_CONSTRAINTS[name]
        wants_bool = expected is bool
        if value is not None and (isinstance(value, bool) != wants_bool or not isinstance(value, expected)):
            raise ProfileError(f"Profile '{key}': constraint '{name}' has the wrong type ({value!r})")
    return dict(raw)


def parse_profile(key: str, entry: object, windows: dict[str, DepartureWindow]) -> TravellerProfile:
    if not isinstance(entry, dict):
        raise ProfileError(f"Profile '{key}' must be an object")
    unknown = sorted(set(entry) - PROFILE_FIELDS)
    if unknown:
        raise ProfileError(f"Profile '{key}': unknown field(s) {', '.join(unknown)}")
    if not entry.get("name"):
        raise ProfileError(f"Profile '{key}' has no name")
    try:
        weights = Weights(entry.get("weights") or {})
    except InvalidWeightsError as exc:
        raise ProfileError(f"Profile '{key}': {exc}") from exc
    try:
        departure = parse_departure_preference(entry.get("preferred_departure"), windows)
    except InvalidRequestError as exc:
        raise ProfileError(f"Profile '{key}': {exc}") from exc
    return TravellerProfile(
        key=key,
        name=entry["name"],
        description=entry.get("description", ""),
        weights=weights,
        constraints=_check_constraints(key, entry.get("constraints", {})),
        preferred_departure=departure,
    )


def load_profiles(path: Path, windows: dict[str, DepartureWindow]) -> dict[str, TravellerProfile]:
    if not path.exists():
        raise DataFileError(f"Profiles file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise ProfileError(f"{path.name} must contain at least one profile")
    return {key: parse_profile(key, entry, windows) for key, entry in raw.items()}
