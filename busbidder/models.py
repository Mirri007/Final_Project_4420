"""Domain objects for the marketplace.

Offers and their parts are immutable (frozen dataclasses): once loaded and validated they
cannot drift into an invalid state. Seat availability changes through bookings, so it is
modelled by creating an updated copy (Offer.with_seats) instead of mutating shared data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from enum import Enum


@dataclass(frozen=True)
class Company:
    code: str
    name: str
    profile: str


@dataclass(frozen=True)
class Destination:
    code: str
    name: str
    region: str
    coastal: bool
    distance_km: int


@dataclass(frozen=True)
class FareConditions:
    """Ticket conditions of an offer; copied into a booking when the offer is booked."""

    name: str
    changeable: bool
    refundable: bool
    change_fee_eur: float
    luggage_kg: int

    @property
    def flags(self) -> str:
        """Compact label used in tables: C = changeable, R = refundable."""
        if self.changeable and self.refundable:
            return "C+R"
        if self.changeable:
            return "C"
        if self.refundable:
            return "R"
        return "--"

    @property
    def summary(self) -> str:
        if self.changeable and self.refundable:
            text = "changeable and refundable"
        elif self.changeable:
            text = "changeable, non-refundable"
        elif self.refundable:
            text = "refundable, not changeable"
        else:
            text = "non-changeable, non-refundable"
        if self.changeable and self.change_fee_eur > 0:
            text += f" (EUR {self.change_fee_eur:.2f} change fee)"
        return text


@dataclass(frozen=True)
class Offer:
    """One bookable fare on one departure. Fare conditions are composed, not inherited."""

    offer_id: str
    company: Company
    destination: Destination
    departure: datetime
    arrival: datetime
    price_eur: float
    transfers: int
    seats_available: int
    fare: FareConditions
    rating: float | None = None
    co2_kg: float | None = None

    @property
    def duration_min(self) -> int:
        return int((self.arrival - self.departure).total_seconds() // 60)

    @property
    def travel_date(self) -> date:
        return self.departure.date()

    def with_seats(self, seats: int) -> Offer:
        return replace(self, seats_available=seats)


class BookingStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


@dataclass
class Booking:
    """A simulated booking.

    It keeps a snapshot of the journey and fare conditions, so the booking still says what
    the traveller bought even if the offer data is later regenerated or changed.
    """

    reference: str
    offer_id: str
    company: str
    destination: str
    departure: datetime
    arrival: datetime
    passenger_name: str
    passengers: int
    unit_price_eur: float
    total_price_eur: float
    fare: FareConditions
    created_at: datetime
    profile: str | None = None
    status: BookingStatus = BookingStatus.CONFIRMED
    payment_status: str = "SIMULATED_PAID"
    refund_eur: float = 0.0
    cancelled_at: datetime | None = None

    _DATETIME_FIELDS = ("departure", "arrival", "created_at", "cancelled_at")

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in self._DATETIME_FIELDS:
            if data[key] is not None:
                data[key] = data[key].isoformat(timespec="seconds")
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Booking:
        data = dict(data)
        data["fare"] = FareConditions(**data["fare"])
        for key in cls._DATETIME_FIELDS:
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        data["status"] = BookingStatus(data["status"])
        return cls(**data)
