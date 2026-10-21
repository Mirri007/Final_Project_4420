"""Hard constraints: conditions an offer must meet before it is ranked.

Each constraint is a plain function registered with the @hard_constraint decorator. The
filter discovers constraints through the registry, so adding a new one means writing one
decorated function, with no changes to the filtering loop or the user interface.

A check returns None when the offer passes, or a short human-readable reason when it
fails. `active` decides whether the traveller's request switches the constraint on.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from busbidder.models import Offer
from busbidder.request import TravelRequest

Check = Callable[[Offer, TravelRequest], "str | None"]


@dataclass(frozen=True)
class HardConstraint:
    key: str
    label: str
    check: Check
    is_active: Callable[[TravelRequest], bool]
    describe: Callable[[TravelRequest], str]
    scope: bool  # scope constraints define the search itself (route, dates)


HARD_CONSTRAINTS: dict[str, HardConstraint] = {}


def hard_constraint(key: str, label: str, *, describe: Callable[[TravelRequest], str],
                    active: Callable[[TravelRequest], bool] = lambda request: True,
                    scope: bool = False) -> Callable[[Check], Check]:
    def register(check: Check) -> Check:
        if key in HARD_CONSTRAINTS:
            raise ValueError(f"Hard constraint '{key}' is already registered")
        HARD_CONSTRAINTS[key] = HardConstraint(key, label, check, active, describe, scope)
        return check
    return register


# ---------------------------------------------------------------- the constraints

@hard_constraint("destination", "Destination", scope=True,
                 describe=lambda r: f"route Madrid -> {r.destination.name}")
def _destination(offer: Offer, request: TravelRequest) -> str | None:
    if offer.destination.code != request.destination.code:
        return f"goes to {offer.destination.name}"
    return None


@hard_constraint("travel_window", "Travel window", scope=True,
                 describe=lambda r: f"departs between {r.date_from:%d %b} and {r.date_to:%d %b %Y}")
def _travel_window(offer: Offer, request: TravelRequest) -> str | None:
    if not request.date_from <= offer.travel_date <= request.date_to:
        return f"departs on {offer.travel_date:%d %b}, outside the travel window"
    return None


@hard_constraint("seats", "Seat availability",
                 describe=lambda r: f"at least {r.passengers} free seat(s)")
def _seats(offer: Offer, request: TravelRequest) -> str | None:
    if offer.seats_available == 0:
        return "sold out"
    if offer.seats_available < request.passengers:
        return f"only {offer.seats_available} seat(s) left for {request.passengers} passengers"
    return None


@hard_constraint("budget", "Budget", active=lambda r: r.max_price_eur is not None,
                 describe=lambda r: f"price <= EUR {r.max_price_eur:.2f} per ticket")
def _budget(offer: Offer, request: TravelRequest) -> str | None:
    if offer.price_eur > request.max_price_eur:
        return f"price EUR {offer.price_eur:.2f} exceeds the EUR {request.max_price_eur:.2f} budget"
    return None


@hard_constraint("transfers", "Maximum transfers", active=lambda r: r.max_transfers is not None,
                 describe=lambda r: f"at most {r.max_transfers} transfer(s)")
def _transfers(offer: Offer, request: TravelRequest) -> str | None:
    if offer.transfers > request.max_transfers:
        return f"{offer.transfers} transfers (max {request.max_transfers})"
    return None


@hard_constraint("duration", "Maximum duration", active=lambda r: r.max_duration_min is not None,
                 describe=lambda r: f"journey <= {r.max_duration_min // 60}h{r.max_duration_min % 60:02d}")
def _duration(offer: Offer, request: TravelRequest) -> str | None:
    if offer.duration_min > request.max_duration_min:
        return f"journey takes {offer.duration_min} min (max {request.max_duration_min})"
    return None


@hard_constraint("luggage", "Luggage allowance", active=lambda r: r.min_luggage_kg is not None,
                 describe=lambda r: f"luggage allowance >= {r.min_luggage_kg} kg")
def _luggage(offer: Offer, request: TravelRequest) -> str | None:
    if offer.fare.luggage_kg < request.min_luggage_kg:
        return f"luggage allowance {offer.fare.luggage_kg} kg (need {request.min_luggage_kg})"
    return None


@hard_constraint("changeable", "Changeable ticket", active=lambda r: r.require_changeable,
                 describe=lambda r: "ticket must be changeable")
def _changeable(offer: Offer, request: TravelRequest) -> str | None:
    return None if offer.fare.changeable else f"'{offer.fare.name}' fare is not changeable"


@hard_constraint("refundable", "Refundable ticket", active=lambda r: r.require_refundable,
                 describe=lambda r: "ticket must be refundable")
def _refundable(offer: Offer, request: TravelRequest) -> str | None:
    return None if offer.fare.refundable else f"'{offer.fare.name}' fare is not refundable"


# ---------------------------------------------------------------- filtering

@dataclass
class FilterResult:
    total: int
    in_scope: int
    accepted: list[Offer]
    excluded: list[tuple[Offer, list[str]]] = field(default_factory=list)
    violation_counts: Counter = field(default_factory=Counter)
    active: list[tuple[HardConstraint, str]] = field(default_factory=list)

    def most_restrictive(self) -> tuple[HardConstraint, int] | None:
        if not self.violation_counts:
            return None
        key, count = self.violation_counts.most_common(1)[0]
        return HARD_CONSTRAINTS[key], count


def active_constraints(request: TravelRequest) -> list[tuple[HardConstraint, str]]:
    return [(c, c.describe(request)) for c in HARD_CONSTRAINTS.values() if c.is_active(request)]


def apply_hard_constraints(offers: list[Offer], request: TravelRequest) -> FilterResult:
    """Keep offers that satisfy every active constraint.

    Scope constraints (route and dates) run first. For the in-scope offers, every remaining
    constraint is checked, so the result records *all* reasons an offer was excluded and how
    often each constraint bit. That makes over-strict requests easy to diagnose.
    """
    active = active_constraints(request)
    scope = [c for c, _ in active if c.scope]
    others = [c for c, _ in active if not c.scope]

    in_scope = [o for o in offers if all(c.check(o, request) is None for c in scope)]
    result = FilterResult(total=len(offers), in_scope=len(in_scope), accepted=[], active=active)
    for offer in in_scope:
        reasons = []
        for constraint in others:
            reason = constraint.check(offer, request)
            if reason:
                reasons.append(reason)
                result.violation_counts[constraint.key] += 1
        if reasons:
            result.excluded.append((offer, reasons))
        else:
            result.accepted.append(offer)
    return result
