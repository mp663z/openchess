"""T3762 v2: funnel identity - exact cohort math, public-boundary
validation for every rate and count, exact-string versioned output.
Verification mode: independent analyst (routed after PR)."""

from __future__ import annotations

from fractions import Fraction

import pytest

from tools.funnel_calculator import FunnelError, _new_paid, _paid_end, check_target, simulate

HALF = Fraction(1, 2)

BASE = dict(months=2, paid_start=100, churn=Fraction(1, 10),
            monthly_visitors=[1000, 1000],
            download_or_signup=Fraction(1, 20),
            activation=Fraction(1, 2), trial_to_paid=Fraction(2, 5))


# --- pure cohort math (unit) ------------------------------------------

def test_identity_zero_churn_is_plain_sum():
    cohorts = [Fraction(10), Fraction(20), Fraction(30)]
    assert _paid_end(100, Fraction(0), cohorts) == Fraction(160)


def test_identity_zero_visitors_is_pure_decay():
    assert _paid_end(1000, HALF, [Fraction(0)] * 3) == Fraction(125)


def test_identity_exact_cohort_math():
    # start 100, churn 1/2, cohorts [40, 80]:
    # end = 100*(1/2)^2 + 40*(1/2)^1 + 80*(1/2)^0 = 25 + 20 + 80 = 125
    assert _paid_end(100, HALF, [Fraction(40), Fraction(80)]) == 125


def test_new_paid_three_factor_chain():
    assert _new_paid(10000, download_or_signup=Fraction(1, 20),
                     activation=Fraction(1, 2),
                     trial_to_paid=Fraction(2, 5)) == Fraction(100)


# --- behavior through the public boundary ------------------------------

def test_combined_rate_equals_factored_rate():
    combined = simulate(**{**BASE, "activation": None,
                           "trial_to_paid": None,
                           "free_to_paid": Fraction(1, 5)})
    assert combined["paid_end_exact"] == simulate(**BASE)[
        "paid_end_exact"]
    assert combined["activation_exact"] == "1"
    assert combined["trial_to_paid_exact"] == "1/5"


def test_monotonic_in_visitors():
    more = simulate(**{**BASE, "monthly_visitors": [2000, 1000]})
    assert more["paid_end"] >= simulate(**BASE)["paid_end"]


def test_deterministic_versioned_output():
    first, second = simulate(**BASE), simulate(**BASE)
    assert first == second
    assert first["schema_version"] == 1


def test_exact_strings_for_every_identity_input():
    result = simulate(**BASE)
    assert result["churn_exact"] == "1/10"
    assert result["download_or_signup_exact"] == "1/20"
    assert result["activation_exact"] == "1/2"
    assert result["trial_to_paid_exact"] == "2/5"
    assert result["monthly_visitors"] == [1000, 1000]
    assert result["monthly_new_paid_exact"] == ["10", "10"]
    # 100*(9/10)^2 + 10*(9/10) + 10 = 81 + 9 + 10 = 100
    assert result["paid_end_exact"] == "100"


def test_contract_scenario_meets_target_exactly():
    result = check_target()
    assert result["meets_target"] is True
    assert result["paid_end_exact"] == "1000"


# --- public-boundary rejection: every rate, every failure class --------

BAD_RATES = [-0.1, 1.0, 1.5, "high", True, float("nan"),
             float("inf"), [0.1], None]


@pytest.mark.parametrize("rate_name", ["churn", "download_or_signup",
                                       "activation", "trial_to_paid",
                                       "free_to_paid"])
@pytest.mark.parametrize("bad", BAD_RATES)
def test_every_rate_validated_at_public_boundary(rate_name, bad):
    kwargs = dict(BASE)
    if rate_name == "free_to_paid":
        kwargs["activation"] = None
        kwargs["trial_to_paid"] = None
    kwargs[rate_name] = bad
    with pytest.raises(FunnelError):
        simulate(**kwargs)


def test_free_to_paid_exclusive_with_factored_rates():
    with pytest.raises(FunnelError):
        simulate(**{**BASE, "free_to_paid": Fraction(1, 5)})


def test_factored_rates_required_together():
    with pytest.raises(FunnelError):
        simulate(**{**BASE, "trial_to_paid": None})
    with pytest.raises(FunnelError):
        simulate(**{**BASE, "activation": None})


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3", None])
def test_months_rejected(bad):
    with pytest.raises(FunnelError):
        simulate(**{**BASE, "months": bad})


@pytest.mark.parametrize("bad", [-5, True, 2.5, "0", None])
def test_paid_start_rejected(bad):
    with pytest.raises(FunnelError):
        simulate(**{**BASE, "paid_start": bad})


@pytest.mark.parametrize("bad", [[-1, 0], [True, 0], [0.5, 0],
                                 ["x", 0], [None, 0]])
def test_malformed_cohort_entries_rejected(bad):
    with pytest.raises(FunnelError):
        simulate(**{**BASE, "monthly_visitors": bad})


@pytest.mark.parametrize("bad", [[1], [1, 2, 3], "1000", None, []])
def test_visitors_shape_rejected(bad):
    with pytest.raises(FunnelError):
        simulate(**{**BASE, "monthly_visitors": bad})
