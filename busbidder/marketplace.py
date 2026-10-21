"""The Marketplace facade: one object the user interface talks to.

It composes the catalogue (validated data), the profiles, the ranker and the booking
manager. Keeping these as separate collaborators, rather than one large class or a class
hierarchy, means each can be tested on its own, and the CLI never needs to know how the
parts fit together.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from busbidder.booking import BookingManager
from busbidder.config import Settings
from busbidder.exceptions import BookingError, InvalidRequestError, ProfileError
from busbidder.loader import load_catalogue
from busbidder.models import Booking, Destination, Offer
from busbidder.profiles import TravellerProfile, load_profiles
from busbidder.request import TravelRequest
from busbidder.scoring import Ranker, RankingResult, Weights
from busbidder.sensitivity import Scenario, weight_sensitivity


class Marketplace:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalogue = load_catalogue(settings.data_dir, settings.quarantine_file)
        self.profiles = load_profiles(settings.profiles_file, settings.departure_windows)
        self.bookings = BookingManager(settings.bookings_file, cancellation_fee_pct=settings.cancellation_fee_pct)
        self.ranker = Ranker(settings)

    def current_offers(self) -> list[Offer]:
        """All valid offers with seats already booked taken off."""
        return self.bookings.apply_inventory(self.catalogue.offers)

    def destination(self, text: str) -> Destination:
        found = self.catalogue.find_destination(text)
        if found is None:
            names = ", ".join(d.name for d in self.catalogue.destinations.values())
            raise InvalidRequestError(f"Unknown destination '{text}'. Choose from: {names}")
        return found

    def profile(self, key: str) -> TravellerProfile:
        try:
            return self.profiles[key]
        except KeyError:
            raise ProfileError(f"Unknown profile '{key}'. Choose from: {', '.join(self.profiles)}") from None

    def build_request(self, profile: TravellerProfile, destination: str, date_from: date,
                      date_to: date, passengers: int = 1) -> TravelRequest:
        return profile.build_request(self.destination(destination), date_from, date_to, passengers)

    def rank(self, request: TravelRequest, weights: Weights) -> RankingResult:
        return self.ranker.rank(self.current_offers(), request, weights)

    def sensitivity(self, request: TravelRequest, weights: Weights,
                    step: float = 0.10) -> tuple[RankingResult, list[Scenario]]:
        return weight_sensitivity(self.ranker, self.current_offers(), request, weights, step)

    def book(self, offer_id: str, passenger_name: str, passengers: int = 1,
             profile: str | None = None) -> Booking:
        # Always book against the original offer from the data file, so seats already
        # booked are subtracted exactly once (inside BookingManager).
        offer = self.catalogue.offer(offer_id)
        if offer is None:
            raise BookingError(f"Unknown offer '{offer_id}'")
        return self.bookings.book(offer, passenger_name, passengers, profile)

    def remaining_seats(self, offer_id: str) -> int:
        offer = self.catalogue.offer(offer_id)
        return 0 if offer is None else self.bookings.remaining_seats(offer)

    def all_bookings(self) -> Sequence[Booking]:
        return self.bookings.all()
