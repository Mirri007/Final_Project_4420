"""The traveller's request: hard constraints plus a preferred departure window."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time

from busbidder.exceptions import InvalidRequestError
from busbidder.models import Destination

MINUTES_PER_DAY = 24 * 60
MAX_PASSENGERS = 9

# "07:00-10:30" style custom windows typed by the user or written in profiles.json
_CUSTOM_WINDOW = re.compile(
    r"^(?P<start>(?:[01]\d|2[0-3]):[0-5]\d)\s*-\s*(?P<end>(?:[01]\d|2[0-3]):[0-5]\d)$"
)


def parse_clock(text: str) -> time:
    try:
        return datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        raise InvalidRequestError(f"'{text}' is not a valid HH:MM time") from None


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _circular_gap(a: int, b: int) -> int:
    """Distance between two clock times in minutes, going the short way round midnight."""
    gap = abs(a - b) % MINUTES_PER_DAY
    return min(gap, MINUTES_PER_DAY - gap)


@dataclass(frozen=True)
class DepartureWindow:
    """A preferred departure period; it may wrap past midnight (e.g. 23:00-06:00)."""

    label: str
    start: time
    end: time

    def contains(self, t: time) -> bool:
        m, s, e = _minutes(t), _minutes(self.start), _minutes(self.end)
        if s <= e:
            return s <= m <= e
        return m >= s or m <= e

    def distance_min(self, t: time) -> int:
        """Minutes between t and the nearest edge of the window (0 when inside)."""
        if self.contains(t):
            return 0
        m = _minutes(t)
        return min(_circular_gap(m, _minutes(self.start)), _circular_gap(m, _minutes(self.end)))

    def __str__(self) -> str:
        return f"{self.label} window ({self.start:%H:%M}-{self.end:%H:%M})"


def parse_departure_preference(
    text: str | None, named_windows: dict[str, DepartureWindow]
) -> DepartureWindow | None:
    """Turn 'morning', '07:00-10:00' or 'any' into a DepartureWindow (None = no preference)."""
    if text is None:
        return None
    key = text.strip().lower()
    if key in ("", "any", "none"):
        return None
    if key in named_windows:
        return named_windows[key]
    match = _CUSTOM_WINDOW.match(key)
    if match:
        return DepartureWindow("preferred", parse_clock(match["start"]), parse_clock(match["end"]))
    names = ", ".join(named_windows)
    raise InvalidRequestError(
        f"Unknown departure preference '{text}'. Use {names}, 'any' or a range such as 07:00-10:00"
    )


@dataclass(frozen=True)
class TravelRequest:
    """Everything a search needs. Fields set to None/False impose no hard constraint.

    max_price_eur is the budget per ticket. preferred_departure is a soft preference: it is
    scored, never used to exclude offers.
    """

    destination: Destination
    date_from: date
    date_to: date
    passengers: int = 1
    max_price_eur: float | None = None
    max_transfers: int | None = None
    max_duration_min: int | None = None
    min_luggage_kg: int | None = None
    require_changeable: bool = False
    require_refundable: bool = False
    preferred_departure: DepartureWindow | None = None

    def __post_init__(self) -> None:
        if self.date_from > self.date_to:
            raise InvalidRequestError(
                f"The travel window ends ({self.date_to}) before it starts ({self.date_from})"
            )
        if not 1 <= self.passengers <= MAX_PASSENGERS:
            raise InvalidRequestError(f"Passengers must be between 1 and {MAX_PASSENGERS}")
        for name in ("max_price_eur", "max_transfers", "max_duration_min", "min_luggage_kg"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise InvalidRequestError(f"{name} cannot be negative (got {value})")
