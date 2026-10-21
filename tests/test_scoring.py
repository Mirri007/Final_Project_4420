"""Normalisation, weights, the weighted total, ties, missing and extreme values."""

import random
from dataclasses import replace
from datetime import datetime

import pytest

from busbidder.criteria import Criterion, minmax_scale, ratio_scale
from busbidder.exceptions import ConfigError, InvalidWeightsError
from busbidder.request import DepartureWindow, parse_clock
from busbidder.scoring import Ranker, Weights
from tests.factories import MORNING, make_offer, make_request, test_settings

CORE = {"price": 40, "duration": 25, "departure": 15, "flexibility": 20}


@pytest.fixture
def ranker(tmp_path):
    return Ranker(test_settings(tmp_path))


# ---------------------------------------------------------------- weights

def test_percentages_and_fractions_are_equivalent():
    as_percent = Weights(CORE)
    as_fraction = Weights({k: v / 100 for k, v in CORE.items()})
    assert dict(as_percent) == pytest.approx(dict(as_fraction))
    assert sum(as_percent.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("raw, message", [
    ({**CORE, "price": 30}, "must total 100"),
    ({**CORE, "price": -10, "duration": 75}, "cannot be negative"),
    ({**CORE, "comfort": 0}, "Unknown criteria"),
    ({"price": 50, "duration": 50}, "core criteria"),
    ({**CORE, "price": True}, "must be a number"),
    ({}, "No weights"),
])
def test_invalid_weights_are_rejected(raw, message):
    with pytest.raises(InvalidWeightsError, match=message):
        Weights(raw)


def test_zero_weight_is_allowed_and_inactive():
    weights = Weights({"price": 60, "duration": 40, "departure": 0, "flexibility": 0})
    assert weights.active() == ["price", "duration"]


def test_shifted_weights_still_total_one_and_keep_proportions():
    shifted = Weights(CORE).shifted("price", 0.10)
    assert shifted["price"] == pytest.approx(0.50)
    assert sum(shifted.values()) == pytest.approx(1.0)
    assert shifted["duration"] / shifted["flexibility"] == pytest.approx(25 / 20)


# ---------------------------------------------------------------- normalisers

def test_ratio_scale_lower_is_better():
    assert ratio_scale([20, 40, 80], lower_is_better=True) == pytest.approx([100, 50, 25])


def test_minmax_scale_lower_is_better():
    assert minmax_scale([20, 40, 80], lower_is_better=True) == pytest.approx([100, 66.667, 0], abs=1e-3)


@pytest.mark.parametrize("scale", [ratio_scale, minmax_scale])
def test_equal_values_all_score_100(scale):
    assert scale([35.0, 35.0, 35.0], True) == [100.0, 100.0, 100.0]
    assert scale([35.0], True) == [100.0]


def test_ratio_keeps_small_differences_small_unlike_minmax():
    # A 1-euro difference: min-max turns it into the full 0-100 range, ratio does not.
    assert minmax_scale([30, 31], True)[1] == 0.0
    assert ratio_scale([30, 31], True)[1] == pytest.approx(96.77, abs=0.01)


def test_ratio_is_robust_to_an_extreme_outlier():
    without = ratio_scale([30, 35], True)
    with_outlier = ratio_scale([30, 35, 400], True)
    assert with_outlier[:2] == pytest.approx(without)  # others' scores unchanged
    assert minmax_scale([30, 35, 400], True)[1] > 95    # min-max compresses them towards 100


def test_ratio_falls_back_when_best_value_is_zero():
    assert ratio_scale([0, 10], True) == [100.0, 0.0]


# ---------------------------------------------------------------- criteria

def test_criteria_register_themselves():
    assert list(Criterion.registry)[:4] == ["price", "duration", "departure", "flexibility"]
    assert {"transfers", "rating", "emissions"} <= set(Criterion.registry)


@pytest.mark.parametrize("hour, minute, expected", [(8, 0, 100.0), (12, 0, 100.0), (14, 0, 50.0), (16, 0, 0.0),
                                                    (20, 0, 0.0), (4, 0, 50.0)])
def test_departure_score_decays_outside_window(ranker, hour, minute, expected):
    offer = make_offer(departure=datetime(2026, 7, 12, hour, minute))
    score = ranker.criteria["departure"].score_all([offer], make_request(preferred_departure=MORNING))
    assert score == [pytest.approx(expected)]


def test_window_wrapping_midnight():
    night = DepartureWindow("night", parse_clock("23:00"), parse_clock("06:00"))
    assert night.contains(parse_clock("01:30"))
    assert night.distance_min(parse_clock("07:00")) == 60
    assert night.distance_min(parse_clock("22:00")) == 60


def test_no_departure_preference_gives_everyone_100(ranker):
    offers = [make_offer(departure=datetime(2026, 7, 12, h)) for h in (3, 12, 21)]
    assert ranker.criteria["departure"].score_all(offers, make_request()) == [100.0] * 3


@pytest.mark.parametrize("changeable, refundable, expected", [(True, True, 100), (True, False, 60),
                                                              (False, True, 40), (False, False, 0)])
def test_flexibility_categories(ranker, changeable, refundable, expected):
    offer = make_offer(changeable=changeable, refundable=refundable)
    assert ranker.criteria["flexibility"].score_all([offer], make_request()) == [expected]


@pytest.mark.parametrize("rating, expected", [(1.0, 0.0), (3.0, 50.0), (5.0, 100.0)])
def test_rating_scale(ranker, rating, expected):
    assert ranker.criteria["rating"].score_all([make_offer(rating=rating)], make_request()) == [expected]


@pytest.mark.parametrize("transfers, expected", [(0, 100.0), (1, 50.0), (2, 0.0), (3, 0.0)])
def test_transfers_scale(ranker, transfers, expected):
    assert ranker.criteria["transfers"].score_all([make_offer(transfers=transfers)], make_request()) == [expected]


# ---------------------------------------------------------------- ranking

def test_total_is_the_weighted_sum_of_criterion_scores(ranker):
    offers = [make_offer("A", price=30, duration=400), make_offer("B", price=45, duration=300)]
    result = ranker.rank(offers, make_request(preferred_departure=MORNING), Weights(CORE))
    for r in result.ranked:
        assert r.total == pytest.approx(sum(s.score * s.weight for s in r.scores.values()), abs=0.01)
        assert 0 <= r.total <= 100


def test_weights_change_the_winner(ranker):
    cheap_slow = make_offer("CHEAP", price=25, duration=540, changeable=False)
    dear_fast = make_offer("FAST", price=60, duration=330, changeable=True, refundable=True)
    request = make_request()
    budget = Weights({"price": 80, "duration": 10, "departure": 0, "flexibility": 10})
    speed = Weights({"price": 10, "duration": 60, "departure": 0, "flexibility": 30})
    assert ranker.rank([cheap_slow, dear_fast], request, budget).top.offer.offer_id == "CHEAP"
    assert ranker.rank([cheap_slow, dear_fast], request, speed).top.offer.offer_id == "FAST"


def test_ties_are_flagged_and_broken_deterministically(ranker):
    a = make_offer("CE-MLG-20260712-0800-B")
    b = make_offer("CE-MLG-20260712-0800-A")
    c = make_offer("CE-MLG-20260712-0700-C", departure=datetime(2026, 7, 12, 7, 0))
    weights = Weights({"price": 50, "duration": 50, "departure": 0, "flexibility": 0})
    orders = set()
    for seed in range(5):
        offers = [a, b, c]
        random.Random(seed).shuffle(offers)
        result = ranker.rank(offers, make_request(), weights)
        orders.add(tuple(r.offer.offer_id for r in result.ranked))
        assert all(r.tied for r in result.ranked)
    # Same total and price -> earlier departure first, then offer ID.
    assert orders == {("CE-MLG-20260712-0700-C", "CE-MLG-20260712-0800-A", "CE-MLG-20260712-0800-B")}


def test_missing_values_get_neutral_score_and_a_warning(ranker):
    offers = [make_offer("A", rating=None), make_offer("B", rating=5.0)]
    weights = Weights({**CORE, "price": 30, "rating": 10})
    result = ranker.rank(offers, make_request(), weights)
    missing = next(r for r in result.ranked if r.offer.offer_id == "A").scores["rating"]
    assert missing.missing and missing.score == 50.0
    assert any("no rating data" in w for w in result.warnings)


def test_single_acceptable_offer_produces_a_warning(ranker):
    result = ranker.rank([make_offer()], make_request(), Weights(CORE))
    assert len(result.ranked) == 1
    assert any("Only one offer" in w for w in result.warnings)


def test_no_acceptable_offer_names_the_most_restrictive_constraint(ranker):
    offers = [make_offer("A", price=80), make_offer("B", price=90)]
    result = ranker.rank(offers, make_request(max_price_eur=50), Weights(CORE))
    assert result.top is None
    assert any("'Budget' excluded the most (2 of 2)" in w for w in result.warnings)


def test_dominant_weight_warning(ranker):
    weights = Weights({"price": 70, "duration": 10, "departure": 10, "flexibility": 10})
    result = ranker.rank([make_offer(), make_offer("B", price=50)], make_request(), weights)
    assert any("Price carries 70%" in w for w in result.warnings)


def test_unknown_normalisation_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="normalisation"):
        Ranker(test_settings(tmp_path, normalisation="zscore"))


def test_minmax_normalisation_can_be_selected(tmp_path):
    ranker = Ranker(replace(test_settings(tmp_path), normalisation="minmax"))
    offers = [make_offer("A", price=30), make_offer("B", price=31)]
    weights = Weights({"price": 100, "duration": 0, "departure": 0, "flexibility": 0})
    assert [r.total for r in ranker.rank(offers, make_request(), weights).ranked] == [100.0, 0.0]
