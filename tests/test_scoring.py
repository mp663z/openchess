"""T2235: scoring equation matches Appendix D; arithmetic is inspectable."""

import pytest

from backtest.scoring import (
    ScoringInputs,
    band_of,
    cold_start_threshold,
    f_line_frequency,
    g_attenuation,
    load_spec,
    norm_elite_adoption,
    norm_engine_drift,
    norm_own_results,
    norm_review_age,
    norm_theory_activity,
    score,
)


def inputs(**kw):
    base = dict(
        game_count=14,
        trailing_year_baseline=10.0,
        adopter_strength=0.6,
        elite_n=12,
        engine_drift=0.2,
        personal_score=0.25,
        overall_score=0.55,
        review_age_days=210,
        user_rating=2250,
        line_frequency=8.0,
        repertoire_median_frequency=6.0,
        evidence_quality=1.0,
    )
    base.update(kw)
    return ScoringInputs(**base)


def test_reference_weights_sum_to_one():
    spec = load_spec()
    total = sum(float(v) for v in spec["weights_reference_band_2200plus"].values())
    assert abs(total - 1.0) < 1e-9


def test_spec_is_versioned_and_inputs_named():
    spec = load_spec()
    assert spec["version"] == "0.1"
    assert set(spec["inputs_versioned"]) == {
        "game_count",
        "adopter_strength",
        "engine_drift",
        "personal_result",
        "review_age",
        "user_rating",
        "line_frequency",
        "evidence_quality",
    }


def test_normalization_bounds():
    assert 0.0 <= norm_theory_activity(100, 10.0, 1.0) <= 1.0
    assert norm_theory_activity(0, 10.0, 1.0) == 0.0
    assert norm_elite_adoption(1.0, 10) == 1.0
    assert norm_elite_adoption(-1.0, 10) == 0.0
    assert norm_elite_adoption(0.9, 3) == 0.5  # low-n guard
    assert norm_engine_drift(1.0) == 1.0  # capped at 0.5
    assert norm_own_results(None, 0.5) == 0.0  # suppressed below 5 games
    assert norm_review_age(730) == 1.0  # saturates at 12 months
    assert norm_review_age(0) == 0.0


def test_f_clamped():
    assert f_line_frequency(100.0, 1.0) == 2.0
    assert f_line_frequency(0.01, 100.0) == 0.25
    assert f_line_frequency(6.0, 6.0) == 1.0


def test_band_attenuation_renormalizes_to_one():
    spec = load_spec()
    w_ref = {k: float(v) for k, v in spec["weights_reference_band_2200plus"].items()}
    for band, factor in (("2000-2199", 0.6), ("1800-1999", 0.4), ("2400+", 1.0)):
        w = g_attenuation(band, w_ref)
        assert abs(sum(w.values()) - 1.0) < 1e-9
        if factor < 1.0:
            assert w["T_theory_activity"] < w_ref["T_theory_activity"]
            assert w["R_own_results"] > w_ref["R_own_results"]


def test_score_exposes_arithmetic_and_is_deterministic():
    r1 = score(inputs())
    r2 = score(inputs())
    assert r1["score"] == r2["score"]
    assert "S = (" in r1["arithmetic"]
    assert r1["spec_version"] == "0.1"
    manual = sum(r1["weights"][k] * r1["components"][k] for k in r1["weights"]) * r1["F"]
    assert abs(r1["score"] - manual) < 1e-12


def test_same_delta_scores_differently_by_band():
    hi = score(inputs(user_rating=2350))
    lo = score(inputs(user_rating=1850))
    assert hi["score"] != lo["score"]
    assert lo["weights"]["R_own_results"] > hi["weights"]["R_own_results"]


def test_band_of():
    assert band_of(1899) == "1800-1999"
    assert band_of(2000) == "2000-2199"
    assert band_of(2200) == "2200-2399"
    assert band_of(2400) == "2400+"


def test_cold_start_threshold_75th_percentile():
    assert cold_start_threshold([0.1, 0.2, 0.3, 0.4]) == pytest.approx(0.3)
    assert cold_start_threshold([]) == float("inf")
