"""Weight-sensitivity analysis: does the recommendation survive small changes in weights?

For every criterion, its weight is moved up and down by `step` (10 percentage points by
default) while the other weights keep their relative proportions. The same filtered offers
are re-scored and the new winner is compared with the baseline. A recommendation that
changes under small shifts should be presented with more caution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from busbidder.models import Offer
from busbidder.request import TravelRequest
from busbidder.scoring import RankedOffer, Ranker, RankingResult, Weights


@dataclass(frozen=True)
class Scenario:
    criterion: str
    delta: float
    weights: Weights
    top: RankedOffer | None
    changed: bool


def weight_sensitivity(ranker: Ranker, offers: Sequence[Offer], request: TravelRequest,
                       weights: Weights, step: float = 0.10) -> tuple[RankingResult, list[Scenario]]:
    baseline = ranker.rank(offers, request, weights)
    baseline_id = baseline.top.offer.offer_id if baseline.top else None
    candidates = baseline.filter_result.accepted
    scenarios = []
    for key in weights:
        for delta in (-step, step):
            current = weights[key]
            if (delta < 0 and current <= 0) or (delta > 0 and current >= 1):
                continue
            shifted = weights.shifted(key, delta)
            ranked = ranker.score(candidates, request, shifted)
            top = ranked[0] if ranked else None
            top_id = top.offer.offer_id if top else None
            scenarios.append(Scenario(key, delta, shifted, top, top_id != baseline_id))
    return baseline, scenarios
