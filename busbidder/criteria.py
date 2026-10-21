"""Soft-preference criteria and how each one becomes a 0-100 score.

Two families of criteria exist, and the class hierarchy mirrors them:

* RelativeCriterion: a numeric value with no natural "good" level (price in euros,
  duration in minutes, CO2 in kg). It is scored against the other acceptable offers using
  a configurable normaliser.
* AbsoluteCriterion: a value with a meaningful fixed scale (departure-time fit, fare
  flexibility, transfers, customer rating). It is scored by a fixed rule, independent of
  which other offers happen to be in the result set.

Every concrete subclass registers itself in Criterion.registry when its class is defined
(__init_subclass__). The ranker, the weight validator and the CLI all discover criteria
from that registry, so a new criterion is added by writing one class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, ClassVar, Sequence

from busbidder.models import Offer
from busbidder.request import TravelRequest

if TYPE_CHECKING:
    from busbidder.config import Settings

Normaliser = Callable[[Sequence[float], bool], "list[float]"]
NORMALISERS: dict[str, Normaliser] = {}


def normaliser(name: str) -> Callable[[Normaliser], Normaliser]:
    """Register a normalisation method under a name usable in settings.json."""
    def register(func: Normaliser) -> Normaliser:
        NORMALISERS[name] = func
        return func
    return register


@normaliser("minmax")
def minmax_scale(values: Sequence[float], lower_is_better: bool) -> list[float]:
    """Best value = 100, worst = 0. All-equal values all get 100 (no differentiation)."""
    low, high = min(values), max(values)
    if high == low:
        return [100.0] * len(values)
    if lower_is_better:
        return [100.0 * (high - v) / (high - low) for v in values]
    return [100.0 * (v - low) / (high - low) for v in values]


@normaliser("ratio")
def ratio_scale(values: Sequence[float], lower_is_better: bool) -> list[float]:
    """Score proportional to the best value: best = 100, twice the best price = 50.

    Unlike min-max, a 1-euro difference between two similar offers stays a small score
    difference, and one very expensive outlier does not compress everyone else's scores.
    """
    best = min(values) if lower_is_better else max(values)
    if best <= 0:  # ratios are undefined; fall back rather than divide by zero
        return minmax_scale(values, lower_is_better)
    if lower_is_better:
        return [100.0 * best / v for v in values]
    return [100.0 * v / best for v in values]


def format_minutes(minutes: float) -> str:
    hours, mins = divmod(int(round(minutes)), 60)
    return f"{hours}h{mins:02d}"


def format_gap(minutes: float) -> str:
    """Like format_minutes, but short gaps read as '55 min' rather than '0h55'."""
    return f"{int(round(minutes))} min" if minutes < 60 else format_minutes(minutes)


class Criterion(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    header: ClassVar[str]  # short column header for tables
    registry: ClassVar[dict[str, type[Criterion]]] = {}

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if "key" not in cls.__dict__:  # intermediate abstract classes define no key
            return
        if cls.key in Criterion.registry:
            raise TypeError(f"Criterion '{cls.key}' is already registered")
        Criterion.registry[cls.key] = cls

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def raw_value(self, offer: Offer, request: TravelRequest) -> float | None:
        """The measured value, or None when the data is missing."""

    @abstractmethod
    def normalise(self, values: list[float], request: TravelRequest) -> list[float]:
        """Convert the present (non-missing) raw values to 0-100 scores."""

    @abstractmethod
    def display(self, offer: Offer, request: TravelRequest) -> str:
        """Short value for tables."""

    @abstractmethod
    def describe(self, offer: Offer, request: TravelRequest, candidates: Sequence[Offer]) -> str:
        """A factual phrase used in the generated explanation."""

    def score_all(self, offers: Sequence[Offer], request: TravelRequest) -> list[float | None]:
        """Score every offer; missing values stay None so the ranker can treat them explicitly."""
        raws = [self.raw_value(o, request) for o in offers]
        present = [r for r in raws if r is not None]
        scores = iter(self.normalise(present, request) if present else [])
        return [None if r is None else next(scores) for r in raws]


class RelativeCriterion(Criterion):
    """Numeric criterion scored relative to the other acceptable offers."""

    lower_is_better: ClassVar[bool] = True

    def normalise(self, values: list[float], request: TravelRequest) -> list[float]:
        return NORMALISERS[self.settings.normalisation](values, self.lower_is_better)


class AbsoluteCriterion(Criterion):
    """Criterion scored on a fixed scale, independent of the other offers."""

    def normalise(self, values: list[float], request: TravelRequest) -> list[float]:
        return [self.scale(v, request) for v in values]

    @abstractmethod
    def scale(self, value: float, request: TravelRequest) -> float:
        """Map one raw value onto 0-100."""


# ---------------------------------------------------------------- concrete criteria

class PriceCriterion(RelativeCriterion):
    key = "price"
    label = "Price"
    header = "Price"

    def raw_value(self, offer, request):
        return offer.price_eur

    def display(self, offer, request):
        return f"{offer.price_eur:.2f}"

    def describe(self, offer, request, candidates):
        cheapest = min(c.price_eur for c in candidates)
        if offer.price_eur <= cheapest + 0.005:
            return f"the cheapest acceptable fare (EUR {offer.price_eur:.2f})"
        return (f"price EUR {offer.price_eur:.2f}, EUR {offer.price_eur - cheapest:.2f} above "
                f"the cheapest acceptable offer (EUR {cheapest:.2f})")


class DurationCriterion(RelativeCriterion):
    key = "duration"
    label = "Duration"
    header = "Durat"

    def raw_value(self, offer, request):
        return float(offer.duration_min)

    def display(self, offer, request):
        return format_minutes(offer.duration_min)

    def describe(self, offer, request, candidates):
        fastest = min(c.duration_min for c in candidates)
        if offer.duration_min <= fastest:
            return f"the fastest acceptable journey ({format_minutes(offer.duration_min)})"
        return (f"{format_minutes(offer.duration_min)} journey, {format_gap(offer.duration_min - fastest)} "
                f"slower than the fastest acceptable option ({format_minutes(fastest)})")


class DepartureCriterion(AbsoluteCriterion):
    """100 inside the preferred window, falling linearly to 0 at `tolerance` minutes outside."""

    key = "departure"
    label = "Departure time"
    header = "Depart"

    def raw_value(self, offer, request):
        if request.preferred_departure is None:
            return 0.0
        return float(request.preferred_departure.distance_min(offer.departure.time()))

    def scale(self, value, request):
        tolerance = self.settings.departure_tolerance_min
        return max(0.0, 100.0 * (1 - value / tolerance))

    def display(self, offer, request):
        return f"{offer.departure:%H:%M}"

    def describe(self, offer, request, candidates):
        window = request.preferred_departure
        when = f"{offer.departure:%H:%M}"
        if window is None:
            return f"departs {when} (no departure-time preference was set)"
        gap = window.distance_min(offer.departure.time())
        if gap == 0:
            return f"departs {when}, inside your {window}"
        return f"departs {when}, {format_gap(gap)} outside your {window}"


class FlexibilityCriterion(AbsoluteCriterion):
    """Categorical: being able to change a ticket is valued above a refund, because a
    shifted plan is the more common need and a change keeps the trip."""

    key = "flexibility"
    label = "Flexibility"
    header = "Flex"
    SCORES: ClassVar[dict[tuple[bool, bool], float]] = {
        (True, True): 100.0,   # changeable and refundable
        (True, False): 60.0,   # changeable only
        (False, True): 40.0,   # refundable only
        (False, False): 0.0,
    }

    def raw_value(self, offer, request):
        return self.SCORES[(offer.fare.changeable, offer.fare.refundable)]

    def scale(self, value, request):
        return value

    def display(self, offer, request):
        return offer.fare.flags

    def describe(self, offer, request, candidates):
        return f"'{offer.fare.name}' fare is {offer.fare.summary}"


class TransfersCriterion(AbsoluteCriterion):
    key = "transfers"
    label = "Transfers"
    header = "Trnsf"
    SCORES: ClassVar[dict[int, float]] = {0: 100.0, 1: 50.0}  # two or more: 0

    def raw_value(self, offer, request):
        return float(offer.transfers)

    def scale(self, value, request):
        return self.SCORES.get(int(value), 0.0)

    def display(self, offer, request):
        return str(offer.transfers)

    def describe(self, offer, request, candidates):
        if offer.transfers == 0:
            return "direct route (no transfers)"
        return f"{offer.transfers} transfer{'s' if offer.transfers > 1 else ''} on the way"


class RatingCriterion(AbsoluteCriterion):
    """Customer rating on a 1-5 scale mapped linearly: 1 -> 0, 5 -> 100."""

    key = "rating"
    label = "Rating"
    header = "Rating"

    def raw_value(self, offer, request):
        return offer.rating

    def scale(self, value, request):
        return (value - 1.0) / 4.0 * 100.0

    def display(self, offer, request):
        return "n/a" if offer.rating is None else f"{offer.rating:.1f}"

    def describe(self, offer, request, candidates):
        if offer.rating is None:
            return "no customer rating available (a neutral score was used)"
        return f"customer rating {offer.rating:.1f}/5"


class EmissionsCriterion(RelativeCriterion):
    key = "emissions"
    label = "CO2 emissions"
    header = "CO2"

    def raw_value(self, offer, request):
        return offer.co2_kg

    def display(self, offer, request):
        return "n/a" if offer.co2_kg is None else f"{offer.co2_kg:.1f}"

    def describe(self, offer, request, candidates):
        if offer.co2_kg is None:
            return "no emissions figure reported (a neutral score was used)"
        known = [c.co2_kg for c in candidates if c.co2_kg is not None]
        lowest = min(known)
        if offer.co2_kg <= lowest:
            return f"the lowest estimated emissions ({offer.co2_kg:.1f} kg CO2 per passenger)"
        return f"{offer.co2_kg:.1f} kg CO2 per passenger, {offer.co2_kg - lowest:.1f} kg above the cleanest option"
