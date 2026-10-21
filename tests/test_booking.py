"""Simulated bookings: references, capacity, persistence and cancellation."""

import random
from datetime import datetime

import pytest

from busbidder.booking import REFERENCE_PATTERN, BookingManager, normalise_reference, validate_passenger_name
from busbidder.exceptions import (BookingError, BookingNotFoundError, DataFileError, InvalidReferenceError,
                                  SeatsUnavailableError)
from busbidder.models import BookingStatus
from tests.factories import make_offer

NOW = datetime(2026, 7, 1, 9, 30)


def manager(tmp_path, seed=1, fee=10.0):
    return BookingManager(tmp_path / "bookings.json", cancellation_fee_pct=fee,
                          rng=random.Random(seed), clock=lambda: NOW)


def test_booking_creates_reference_and_reduces_capacity(tmp_path):
    offer = make_offer(seats=5)
    bookings = manager(tmp_path)
    booking = bookings.book(offer, "Ana Lopez", passengers=2)
    assert REFERENCE_PATTERN.match(booking.reference)
    assert booking.reference.startswith("BB-260701-")
    assert booking.total_price_eur == pytest.approx(80.0)
    assert bookings.remaining_seats(offer) == 3
    assert bookings.apply_inventory([offer])[0].seats_available == 3


def test_booking_the_last_seats_is_allowed_but_not_more(tmp_path):
    offer = make_offer(seats=2)
    bookings = manager(tmp_path)
    bookings.book(offer, "Ana Lopez", passengers=2)
    with pytest.raises(SeatsUnavailableError):
        bookings.book(offer, "Ben Ruiz", passengers=1)


def test_sold_out_offer_cannot_be_booked(tmp_path):
    with pytest.raises(SeatsUnavailableError):
        manager(tmp_path).book(make_offer(seats=0), "Ana Lopez")


def test_bookings_persist_with_fare_snapshot(tmp_path):
    offer = make_offer(changeable=True, refundable=True)
    reference = manager(tmp_path).book(offer, "Ana Lopez", profile="example").reference
    reloaded = manager(tmp_path, seed=2).get(reference)
    assert reloaded.fare == offer.fare
    assert reloaded.profile == "example"
    assert reloaded.payment_status == "SIMULATED_PAID"
    assert manager(tmp_path).remaining_seats(offer) == offer.seats_available - 1


def test_references_are_unique_even_if_the_generator_repeats(tmp_path):
    offer = make_offer(seats=10)
    first = manager(tmp_path, seed=7).book(offer, "Ana Lopez").reference
    # A fresh manager with the same seed and clock would generate the same code first.
    second = manager(tmp_path, seed=7).book(offer, "Ben Ruiz").reference
    assert first != second


def test_cancel_refundable_fare_refunds_minus_fee_and_restores_seats(tmp_path):
    offer = make_offer(seats=4, changeable=True, refundable=True, price=50.0)
    bookings = manager(tmp_path, fee=10)
    booking = bookings.book(offer, "Ana Lopez", passengers=2)
    cancelled = bookings.cancel(booking.reference)
    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.refund_eur == pytest.approx(90.0)
    assert bookings.remaining_seats(offer) == 4
    with pytest.raises(BookingError, match="already cancelled"):
        bookings.cancel(booking.reference)


def test_cancel_non_refundable_fare_gives_no_refund(tmp_path):
    bookings = manager(tmp_path)
    booking = bookings.book(make_offer(refundable=False), "Ana Lopez")
    assert bookings.cancel(booking.reference).refund_eur == 0.0


@pytest.mark.parametrize("text", ["BB-1234-ABCDEF", "XX-260701-ABCDEF", "BB-260701-ABCDE0", "BB-260701-ABCDEFG", ""])
def test_malformed_references_are_rejected(text):
    with pytest.raises(InvalidReferenceError):
        normalise_reference(text)


def test_reference_lookup_is_case_insensitive(tmp_path):
    bookings = manager(tmp_path)
    booking = bookings.book(make_offer(), "Ana Lopez")
    assert bookings.get(f"  {booking.reference.lower()} ").reference == booking.reference


def test_unknown_reference(tmp_path):
    with pytest.raises(BookingNotFoundError):
        manager(tmp_path).get("BB-260701-ABCDEF")


@pytest.mark.parametrize("name", ["Ana-María O'Neill", "Ola Nordmann", "Øystein  Ås"])
def test_valid_passenger_names(name):
    assert validate_passenger_name(name)


@pytest.mark.parametrize("name", ["", "   ", "R2D2", "Bob; DROP TABLE", "x" * 61, "-Ana"])
def test_invalid_passenger_names(name):
    with pytest.raises(BookingError):
        validate_passenger_name(name)


@pytest.mark.parametrize("passengers", [0, 10])
def test_passenger_count_limits(tmp_path, passengers):
    with pytest.raises(BookingError):
        manager(tmp_path).book(make_offer(seats=20), "Ana Lopez", passengers=passengers)


def test_corrupt_booking_file_is_reported(tmp_path):
    (tmp_path / "bookings.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DataFileError, match="corrupt"):
        manager(tmp_path)
