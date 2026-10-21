"""Hard constraints: each one at and either side of its boundary."""

from datetime import date, datetime

import pytest

from busbidder.constraints import HARD_CONSTRAINTS, apply_hard_constraints, hard_constraint
from busbidder.exceptions import InvalidRequestError
from tests.factories import VALENCIA, make_offer, make_request


def accepted(offer, **request_fields):
    return apply_hard_constraints([offer], make_request(**request_fields)).accepted == [offer]


def test_registry_contains_the_expected_constraints():
    assert {"destination", "travel_window", "seats", "budget", "transfers", "duration", "luggage",
            "changeable", "refundable"} <= set(HARD_CONSTRAINTS)


def test_registering_a_duplicate_key_fails():
    with pytest.raises(ValueError):
        hard_constraint("budget", "Budget again", describe=lambda r: "")(lambda o, r: None)


@pytest.mark.parametrize("price, ok", [(49.99, True), (50.00, True), (50.01, False)])
def test_budget_boundary(price, ok):
    assert accepted(make_offer(price=price), max_price_eur=50) is ok


def test_budget_inactive_when_not_set():
    assert accepted(make_offer(price=400))


@pytest.mark.parametrize("transfers, ok", [(0, True), (1, True), (2, False)])
def test_max_transfers_boundary(transfers, ok):
    assert accepted(make_offer(transfers=transfers), max_transfers=1) is ok


def test_zero_transfers_allowed_is_a_real_limit():
    assert not accepted(make_offer(transfers=1), max_transfers=0)


@pytest.mark.parametrize("day, ok", [(11, False), (12, True), (14, True), (15, False)])
def test_travel_window_is_inclusive(day, ok):
    assert accepted(make_offer(departure=datetime(2026, 7, day, 8, 0))) is ok


def test_other_destinations_are_out_of_scope_not_violations():
    result = apply_hard_constraints([make_offer(destination=VALENCIA)], make_request())
    assert result.in_scope == 0 and result.excluded == []


@pytest.mark.parametrize("seats, passengers, ok", [(3, 3, True), (2, 3, False), (0, 1, False)])
def test_seat_availability(seats, passengers, ok):
    assert accepted(make_offer(seats=seats), passengers=passengers) is ok


def test_sold_out_reason_is_explicit():
    result = apply_hard_constraints([make_offer(seats=0)], make_request())
    assert result.excluded[0][1] == ["sold out"]


@pytest.mark.parametrize("duration, ok", [(479, True), (480, True), (481, False)])
def test_max_duration_boundary(duration, ok):
    assert accepted(make_offer(duration=duration), max_duration_min=480) is ok


@pytest.mark.parametrize("luggage, ok", [(19, False), (20, True)])
def test_min_luggage_boundary(luggage, ok):
    assert accepted(make_offer(luggage=luggage), min_luggage_kg=20) is ok


def test_changeable_and_refundable_requirements():
    flexible = make_offer(changeable=True, refundable=True)
    basic = make_offer(changeable=False, refundable=False)
    assert accepted(flexible, require_changeable=True, require_refundable=True)
    assert not accepted(basic, require_changeable=True)
    assert not accepted(basic, require_refundable=True)
    assert accepted(basic)  # not required -> not filtered


def test_every_violation_is_recorded_and_counted():
    offer = make_offer(price=90, transfers=2, changeable=False)
    result = apply_hard_constraints([offer], make_request(max_price_eur=50, max_transfers=1, require_changeable=True))
    assert len(result.excluded[0][1]) == 3
    assert result.violation_counts == {"budget": 1, "transfers": 1, "changeable": 1}


def test_only_requested_constraints_are_active():
    keys = {c.key for c, _ in apply_hard_constraints([], make_request(max_price_eur=40)).active}
    assert keys == {"destination", "travel_window", "seats", "budget"}


@pytest.mark.parametrize("fields", [
    {"date_from": date(2026, 7, 14), "date_to": date(2026, 7, 12)},
    {"passengers": 0},
    {"passengers": 10},
    {"max_price_eur": -1},
])
def test_impossible_requests_are_refused(fields):
    with pytest.raises(InvalidRequestError):
        make_request(**fields)
