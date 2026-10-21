"""Traveller profiles, generated explanations and weight sensitivity."""

import json
from datetime import date

import pytest

from busbidder.exceptions import DataFileError, ProfileError
from busbidder.explain import explain_recommendation
from busbidder.marketplace import Marketplace
from busbidder.profiles import load_profiles
from busbidder.scoring import Ranker, Weights
from busbidder.sensitivity import weight_sensitivity
from tests.factories import make_offer, make_request, test_settings


@pytest.fixture
def market(tmp_path):
    return Marketplace(test_settings(tmp_path))


def write_profiles(tmp_path, entry):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"p": entry}), encoding="utf-8")
    return path


VALID_PROFILE = {"name": "P", "weights": {"price": 40, "duration": 25, "departure": 15, "flexibility": 20}}


# ---------------------------------------------------------------- profiles

def test_at_least_three_profiles_with_valid_weights(market):
    assert len(market.profiles) >= 3
    for profile in market.profiles.values():
        assert sum(profile.weights.values()) == pytest.approx(1.0)


def test_same_offers_give_different_recommendations_per_profile(market):
    winners = {}
    for key, profile in market.profiles.items():
        request = market.build_request(profile, "Malaga", date(2026, 7, 12), date(2026, 7, 14))
        winners[key] = market.rank(request, profile.weights).top.offer.offer_id
    assert len(set(winners.values())) >= 4, winners
    assert winners["business"].startswith("CE-")   # fast, direct, early
    assert winners["budget"].startswith("SR-")     # cheapest
    assert winners["eco"].startswith("VB-")        # lowest emissions


def test_ranking_is_reproducible(market):
    profile = market.profile("example")
    request = market.build_request(profile, "Malaga", date(2026, 7, 12), date(2026, 7, 14))
    first = [(r.offer.offer_id, r.total) for r in market.rank(request, profile.weights).ranked]
    second = [(r.offer.offer_id, r.total) for r in market.rank(request, profile.weights).ranked]
    assert first == second


@pytest.mark.parametrize("changes, message", [
    ({"constraints": {"max_speed": 1}}, "unknown constraint"),
    ({"constraints": {"max_transfers": "one"}}, "wrong type"),
    ({"constraints": {"require_changeable": 1}}, "wrong type"),
    ({"constraints": {"max_price_eur": True}}, "wrong type"),
    ({"weights": {"price": 50, "duration": 25, "departure": 15, "flexibility": 20}}, "must total 100"),
    ({"preferred_departure": "brunch"}, "Unknown departure preference"),
    ({"colour": "red"}, "unknown field"),
    ({"name": ""}, "has no name"),
])
def test_invalid_profiles_are_rejected(tmp_path, market, changes, message):
    path = write_profiles(tmp_path, {**VALID_PROFILE, **changes})
    with pytest.raises(ProfileError, match=message):
        load_profiles(path, market.settings.departure_windows)


def test_custom_departure_window_in_profile(tmp_path, market):
    path = write_profiles(tmp_path, {**VALID_PROFILE, "preferred_departure": "07:00-09:30"})
    window = load_profiles(path, market.settings.departure_windows)["p"].preferred_departure
    assert (window.start.hour, window.end.minute) == (7, 30)


def test_missing_profiles_file(tmp_path, market):
    with pytest.raises(DataFileError):
        load_profiles(tmp_path / "nope.json", market.settings.departure_windows)


def test_unknown_profile_key(market):
    with pytest.raises(ProfileError, match="Choose from"):
        market.profile("astronaut")


# ---------------------------------------------------------------- explanations

def explain(tmp_path, offers, weights=None, **request_fields):
    ranker = Ranker(test_settings(tmp_path))
    weights = weights or Weights({"price": 40, "duration": 25, "departure": 15, "flexibility": 20})
    result = ranker.rank(offers, make_request(**request_fields), weights)
    return "\n".join(explain_recommendation(result, ranker.criteria, "Tester"))


def test_explanation_names_strengths_disadvantage_and_caveat(tmp_path):
    text = explain(tmp_path, [
        make_offer("FAST", price=45, duration=300, changeable=True, refundable=True),
        make_offer("CHEAP", price=30, duration=480, changeable=False),
    ])
    assert "Why FAST" in text
    assert "+ Flexibility 100/100" in text
    assert "- Price" in text and "above the cheapest acceptable offer" in text
    assert "Lead over #2 (CHEAP" in text
    assert "not an objectively best ticket" in text


def test_explanation_for_a_single_offer(tmp_path):
    text = explain(tmp_path, [make_offer()])
    assert "only offer that meets all hard constraints" in text


def test_explanation_flags_a_near_tie(tmp_path):
    text = explain(tmp_path, [make_offer("A", price=40.00), make_offer("B", price=40.10)])
    assert "practically equivalent" in text


def test_no_explanation_without_offers(tmp_path):
    assert explain(tmp_path, []) == ""


# ---------------------------------------------------------------- sensitivity

def test_sensitivity_scenarios(tmp_path):
    ranker = Ranker(test_settings(tmp_path))
    offers = [make_offer("CHEAP", price=25, duration=500, changeable=False),
              make_offer("FAST", price=45, duration=320)]
    weights = Weights({"price": 50, "duration": 30, "departure": 0, "flexibility": 20})
    baseline, scenarios = weight_sensitivity(ranker, offers, make_request(), weights)
    # 3 positive weights x 2 directions, plus +10 for the zero-weight criterion
    assert len(scenarios) == 7
    for s in scenarios:
        assert sum(s.weights.values()) == pytest.approx(1.0)
        assert s.changed == (s.top.offer.offer_id != baseline.top.offer.offer_id)
