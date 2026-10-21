"""Weighted multi-criteria ranking.

    Score(offer) = sum over criteria c of  weight_c * score_c(offer)

Weights total 1.0 and each criterion score is on 0-100, so the total is also 0-100 and each
term (the "contribution") can be read directly as points.

The ranking is reproducible: the same offers, request and weights always produce the same
order. Ties on the rounded total are broken by lower price, then earlier departure, then
offer ID.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from busbidder.config import Settings
from busbidder.constraints import FilterResult, apply_hard_constraints
from busbidder.criteria import NORMALISERS, Criterion
from busbidder.exceptions import ConfigError, InvalidWeightsError
from busbidder.models import Offer
from busbidder.request import TravelRequest

logger = logging.getLogger(__name__)

CORE_CRITERIA = ("price", "duration", "departure", "flexibility")


class Weights(Mapping):
    """Validated criterion weights, stored as fractions that total exactly 1.0.

    Input may be fractions (total 1.0) or percentages (total 100). Anything else is
    rejected rather than silently rescaled: a user who typed 40+25+15+10 = 90 has probably
    made a mistake, and quietly inflating every weight by 11% would hide it.
    """

    def __init__(self, raw: Mapping[str, float]):
        if not raw:
            raise InvalidWeightsError("No weights were given")
        known = Criterion.registry
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise InvalidWeightsError(f"Unknown criteria: {', '.join(unknown)}. Known: {', '.join(known)}")
        missing = [key for key in CORE_CRITERIA if key not in raw]
        if missing:
            raise InvalidWeightsError(
                f"The core criteria must always be listed (0 is allowed): missing {', '.join(missing)}"
            )
        for key, value in raw.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise InvalidWeightsError(f"Weight for '{key}' must be a number (got {value!r})")
            if value < 0:
                raise InvalidWeightsError(f"Weight for '{key}' cannot be negative (got {value:g})")

        total = sum(raw.values())
        if math.isclose(total, 1.0, abs_tol=1e-6):
            scale = 1.0
        elif math.isclose(total, 100.0, abs_tol=1e-4):
            scale = 100.0
        else:
            raise InvalidWeightsError(f"Weights must total 100% (or 1.0); these total {total:g}")
        # Canonical order (registry order) keeps tables and logs stable.
        self._weights = {key: float(raw[key]) / scale for key in known if key in raw}

    def __getitem__(self, key: str) -> float:
        return self._weights[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._weights)

    def __len__(self) -> int:
        return len(self._weights)

    def __repr__(self) -> str:
        return f"Weights({self.describe()})"

    def active(self) -> list[str]:
        return [key for key, weight in self._weights.items() if weight > 0]

    def dominant(self, threshold: float) -> list[str]:
        return [key for key, weight in self._weights.items() if weight > threshold]

    def describe(self) -> str:
        return ", ".join(f"{key} {weight:.0%}" for key, weight in self._weights.items() if weight > 0)

    def shifted(self, key: str, delta: float) -> Weights:
        """Move `delta` of weight to (or from) one criterion; the others keep their proportions."""
        new = min(1.0, max(0.0, self._weights.get(key, 0.0) + delta))
        others = {k: w for k, w in self._weights.items() if k != key}
        rest = sum(others.values())
        remaining = 1.0 - new
        if rest > 0:
            rescaled = {k: w * remaining / rest for k, w in others.items()}
        else:
            rescaled = {k: remaining / len(others) for k in others}
        return Weights({**rescaled, key: new})


@dataclass(frozen=True)
class CriterionScore:
    key: str
    label: str
    display: str
    score: float
    weight: float
    missing: bool

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass(frozen=True)
class RankedOffer:
    rank: int
    offer: Offer
    total: float
    scores: dict[str, CriterionScore]
    tied: bool


@dataclass
class RankingResult:
    request: TravelRequest
    weights: Weights
    filter_result: FilterResult
    ranked: list[RankedOffer]
    normalisation: str
    warnings: list[str] = field(default_factory=list)

    @property
    def top(self) -> RankedOffer | None:
        return self.ranked[0] if self.ranked else None


class Ranker:
    def __init__(self, settings: Settings):
        if settings.normalisation not in NORMALISERS:
            raise ConfigError(
                f"Unknown normalisation '{settings.normalisation}'. Choose from: {', '.join(NORMALISERS)}"
            )
        self.settings = settings
        self.criteria: dict[str, Criterion] = {key: cls(settings) for key, cls in Criterion.registry.items()}

    def rank(self, offers: Sequence[Offer], request: TravelRequest, weights: Weights) -> RankingResult:
        filtered = apply_hard_constraints(list(offers), request)
        ranked = self.score(filtered.accepted, request, weights)
        result = RankingResult(request, weights, filtered, ranked, self.settings.normalisation)
        result.warnings = self._warnings(result)
        logger.info("Ranked %d of %d in-scope offers to %s (%s); top: %s", len(ranked), filtered.in_scope,
                    request.destination.name, weights.describe(),
                    result.top.offer.offer_id if result.top else "none")
        return result

    def score(self, candidates: Sequence[Offer], request: TravelRequest, weights: Weights) -> list[RankedOffer]:
        if not candidates:
            return []
        active = weights.active()
        per_criterion = {key: self.criteria[key].score_all(candidates, request) for key in active}

        rows = []
        for index, offer in enumerate(candidates):
            scores = {}
            for key in active:
                criterion = self.criteria[key]
                value = per_criterion[key][index]
                missing = value is None
                scores[key] = CriterionScore(
                    key=key,
                    label=criterion.label,
                    display=criterion.display(offer, request),
                    score=self.settings.missing_value_score if missing else value,
                    weight=weights[key],
                    missing=missing,
                )
            total = round(sum(s.contribution for s in scores.values()), 2)
            rows.append((offer, total, scores))

        rows.sort(key=lambda row: (-row[1], row[0].price_eur, row[0].departure, row[0].offer_id))
        totals = [row[1] for row in rows]
        return [
            RankedOffer(
                rank=position + 1,
                offer=offer,
                total=total,
                scores=scores,
                tied=(position > 0 and totals[position - 1] == total)
                or (position + 1 < len(totals) and totals[position + 1] == total),
            )
            for position, (offer, total, scores) in enumerate(rows)
        ]

    def _warnings(self, result: RankingResult) -> list[str]:
        warnings = []
        weights, request = result.weights, result.request
        for key in weights.dominant(self.settings.dominance_warning_threshold):
            warnings.append(
                f"{self.criteria[key].label} carries {weights[key]:.0%} of the weight, so it largely "
                f"decides the ranking. Fine if intended."
            )
        if weights.get("departure", 0) > 0 and request.preferred_departure is None:
            warnings.append("Departure time has a weight but no preferred window is set, so every offer "
                            "scores 100 on it and it does not affect the order.")
        if not result.ranked:
            worst = result.filter_result.most_restrictive()
            if result.filter_result.in_scope == 0:
                warnings.append("No offers exist for this route and travel window.")
            elif worst:
                constraint, count = worst
                warnings.append(f"No offer meets every hard constraint. '{constraint.label}' excluded the most "
                                f"({count} of {result.filter_result.in_scope}); consider relaxing it.")
            return warnings
        if len(result.ranked) == 1:
            warnings.append("Only one offer passed the hard constraints. Relative criteria give it full marks, "
                            "so its total says little on its own.")
        for key in weights.active():
            missing = sum(1 for r in result.ranked if r.scores[key].missing)
            if missing:
                warnings.append(f"{missing} offer(s) have no {self.criteria[key].label.lower()} data and received "
                                f"a neutral score of {self.settings.missing_value_score:g}.")
        return warnings
