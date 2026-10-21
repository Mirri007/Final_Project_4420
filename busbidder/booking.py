"""Simulated bookings, stored in a JSON file.

Seat capacity is never stored twice. The offer file holds each offer's original seat count;
remaining seats are always computed as  original - seats in CONFIRMED bookings.  Booking
reduces capacity and cancelling restores it automatically, and the two numbers can never
disagree. No payment details are collected; payment status is simulated.
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from busbidder.exceptions import (BookingError, BookingNotFoundError, DataFileError, InvalidReferenceError,
                                  SeatsUnavailableError)
from busbidder.models import Booking, BookingStatus, Offer
from busbidder.request import MAX_PASSENGERS

logger = logging.getLogger(__name__)

# BB-YYMMDD-XXXXXX. The alphabet leaves out I, O, 0 and 1, which are easy to misread aloud.
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_PATTERN = re.compile(r"^BB-\d{6}-[A-HJ-NP-Z2-9]{6}$")
# Letters (any language) in words separated by single spaces, hyphens or apostrophes.
NAME_PATTERN = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
MAX_NAME_LENGTH = 60


def normalise_reference(text: str) -> str:
    reference = text.strip().upper()
    if not REFERENCE_PATTERN.match(reference):
        raise InvalidReferenceError(f"'{text}' is not a booking reference (expected e.g. BB-260712-K7Q2XZ)")
    return reference


def validate_passenger_name(text: str) -> str:
    name = " ".join(text.split())
    if not name:
        raise BookingError("Passenger name is required")
    if len(name) > MAX_NAME_LENGTH or not NAME_PATTERN.match(name):
        raise BookingError(f"'{text}' is not a valid passenger name (letters, spaces, hyphens and apostrophes)")
    return name


class BookingManager:
    def __init__(self, path: Path, *, cancellation_fee_pct: float = 10.0,
                 rng: random.Random | None = None, clock: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.cancellation_fee_pct = cancellation_fee_pct
        self._rng = rng or random.SystemRandom()
        self._clock = clock
        self._bookings: dict[str, Booking] = self._load()

    # ------------------------------------------------------------ persistence

    def _load(self) -> dict[str, Booking]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {b["reference"]: Booking.from_dict(b) for b in data["bookings"]}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise DataFileError(f"Booking file {self.path} is corrupt: {exc}") from exc

    def _save(self, bookings: dict[str, Booking]) -> None:
        """Write to a temporary file first, then swap it in, so a crash cannot leave half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = {"bookings": [b.to_dict() for b in bookings.values()]}
        try:
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            raise BookingError(f"Could not save bookings to {self.path}: {exc}") from exc

    # ------------------------------------------------------------ capacity

    def booked_seats(self, offer_id: str) -> int:
        return sum(b.passengers for b in self._bookings.values()
                   if b.offer_id == offer_id and b.status is BookingStatus.CONFIRMED)

    def remaining_seats(self, offer: Offer) -> int:
        """`offer` must carry the original seat count from the data file."""
        return max(0, offer.seats_available - self.booked_seats(offer.offer_id))

    def apply_inventory(self, offers: Sequence[Offer]) -> list[Offer]:
        """Return copies of the offers with seats already booked subtracted."""
        booked = Counter()
        for b in self._bookings.values():
            if b.status is BookingStatus.CONFIRMED:
                booked[b.offer_id] += b.passengers
        return [o.with_seats(max(0, o.seats_available - booked[o.offer_id])) if booked[o.offer_id] else o
                for o in offers]

    # ------------------------------------------------------------ operations

    def _new_reference(self, when: datetime) -> str:
        for _ in range(100):
            code = "".join(self._rng.choice(REFERENCE_ALPHABET) for _ in range(6))
            reference = f"BB-{when:%y%m%d}-{code}"
            if reference not in self._bookings:
                return reference
        raise BookingError("Could not generate a unique booking reference")

    def book(self, offer: Offer, passenger_name: str, passengers: int = 1,
             profile: str | None = None) -> Booking:
        name = validate_passenger_name(passenger_name)
        if not 1 <= passengers <= MAX_PASSENGERS:
            raise BookingError(f"Passengers must be between 1 and {MAX_PASSENGERS}")
        remaining = self.remaining_seats(offer)
        if remaining < passengers:
            raise SeatsUnavailableError(
                f"{offer.offer_id} has {remaining} seat(s) left; {passengers} requested"
            )
        now = self._clock()
        booking = Booking(
            reference=self._new_reference(now),
            offer_id=offer.offer_id,
            company=offer.company.name,
            destination=offer.destination.name,
            departure=offer.departure,
            arrival=offer.arrival,
            passenger_name=name,
            passengers=passengers,
            unit_price_eur=offer.price_eur,
            total_price_eur=round(offer.price_eur * passengers, 2),
            fare=offer.fare,
            created_at=now.replace(microsecond=0),
            profile=profile,
        )
        updated = {**self._bookings, booking.reference: booking}
        self._save(updated)
        self._bookings = updated
        logger.info("Booked %s: %s x%d on %s, EUR %.2f, %d seat(s) left", booking.reference, name, passengers,
                    offer.offer_id, booking.total_price_eur, remaining - passengers)
        return booking

    def get(self, reference: str) -> Booking:
        reference = normalise_reference(reference)
        try:
            return self._bookings[reference]
        except KeyError:
            raise BookingNotFoundError(f"No booking with reference {reference}") from None

    def cancel(self, reference: str) -> Booking:
        """Cancel a booking. Refundable fares are refunded minus the cancellation fee."""
        booking = self.get(reference)
        if booking.status is BookingStatus.CANCELLED:
            raise BookingError(f"Booking {booking.reference} is already cancelled")
        refund = 0.0
        if booking.fare.refundable:
            refund = round(booking.total_price_eur * (1 - self.cancellation_fee_pct / 100), 2)
        snapshot = Booking.from_dict(booking.to_dict())
        snapshot.status = BookingStatus.CANCELLED
        snapshot.refund_eur = refund
        snapshot.cancelled_at = self._clock().replace(microsecond=0)
        snapshot.payment_status = "SIMULATED_REFUNDED" if refund else "SIMULATED_PAID_NO_REFUND"
        updated = {**self._bookings, booking.reference: snapshot}
        self._save(updated)
        self._bookings = updated
        logger.info("Cancelled %s (%s), refund EUR %.2f", snapshot.reference, snapshot.offer_id, refund)
        return snapshot

    def all(self) -> list[Booking]:
        return sorted(self._bookings.values(), key=lambda b: b.created_at)
