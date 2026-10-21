"""Generate a plain-language explanation for the top-ranked offer.

The explanation is built only from the scores and data, never from fixed text about a
company, so it stays truthful when the data, profile or weights change. It lists the
offer's main strengths, its remaining disadvantages, what separates it from the runner-up,
and a reminder that "best" is relative to the chosen constraints and weights.
"""

from __future__ import annotations

from typing import Mapping

from busbidder.criteria import Criterion
from busbidder.scoring import RankingResult

STRONG_SCORE = 80.0  # a criterion at or above this is presented as a strength
WEAK_SCORE = 70.0    # below this it is presented as a disadvantage
CLOSE_MARGIN = 1.0   # points; smaller leads are reported as practically a tie
MAX_STRENGTHS = 3
MAX_WEAKNESSES = 2


def explain_recommendation(result: RankingResult, criteria: Mapping[str, Criterion],
                           profile_name: str | None = None) -> list[str]:
    top = result.top
    if top is None:
        return []
    request = result.request
    candidates = [r.offer for r in result.ranked]
    scores = list(top.scores.values())

    def phrase(key: str) -> str:
        return criteria[key].describe(top.offer, request, candidates)

    def point(sign: str, key: str) -> str:
        s = top.scores[key]
        return f"  {sign} {s.label} {s.score:.0f}/100: {phrase(key)}"

    who = f" for the {profile_name}" if profile_name else ""
    lines = [f"Why {top.offer.offer_id} ({top.offer.company.name}) ranks first{who}:"]

    strengths = sorted((s for s in scores if s.score >= STRONG_SCORE),
                       key=lambda s: s.contribution, reverse=True)[:MAX_STRENGTHS]
    for s in strengths:
        lines.append(point("+", s.key))

    # Rank disadvantages by the points they cost: weight x shortfall from 100.
    weaknesses = sorted((s for s in scores if s.score < WEAK_SCORE),
                        key=lambda s: s.weight * (100 - s.score), reverse=True)[:MAX_WEAKNESSES]
    for s in weaknesses:
        lines.append(point("-", s.key))
    if not weaknesses:
        weakest = min(scores, key=lambda s: s.score)
        lines.append(point("-", weakest.key) + " (its weakest criterion; no score is below "
                     f"{WEAK_SCORE:g})")

    if len(result.ranked) > 1:
        runner_up = result.ranked[1]
        margin = top.total - runner_up.total
        diffs = {key: top.scores[key].contribution - runner_up.scores[key].contribution for key in top.scores}
        gains = sorted(((k, d) for k, d in diffs.items() if d > 0.05), key=lambda kd: -kd[1])[:2]
        losses = sorted(((k, d) for k, d in diffs.items() if d < -0.05), key=lambda kd: kd[1])[:2]
        sentence = (f"  Lead over #2 ({runner_up.offer.offer_id}, {runner_up.offer.company.name}): "
                    f"{margin:.2f} points.")
        if gains:
            sentence += " Gains most on " + ", ".join(f"{criteria[k].label.lower()} (+{d:.1f})" for k, d in gains)
            sentence += ";" if losses else "."
        if losses:
            sentence += " #2 is stronger on " + ", ".join(f"{criteria[k].label.lower()} ({d:.1f})" for k, d in losses)
            sentence += "."
        lines.append(sentence)
        if margin < CLOSE_MARGIN:
            lines.append(f"  The lead is under {CLOSE_MARGIN:g} point, so #1 and #2 are practically equivalent "
                         "under this model; choose on what the scores do not capture.")
    else:
        lines.append("  It is the only offer that meets all hard constraints.")

    lines.append(f"  This is the best match under your hard constraints and weights ({result.weights.describe()}), "
                 "not an objectively best ticket.")
    return lines
