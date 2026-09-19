"""T3762: funnel identity - the calculator enforces the cohort identity
exactly, rejects malformed inputs, and reproduces the contract
scenario. Verification mode: independent analyst (routed after PR)."""

from __future__ import annotations

from fractions import Fraction

import pytest

from tools.funnel_calculator import FunnelError, check_target, new_paid, paid_end, simulate

HALF = Fraction(1, 2)


def test_identity_zero_churn_is_plain_sum():
    cohorts = [Fraction(10), Fraction(20), Fraction(30)]
    assert paid_end(100, Fraction(0), cohorts) == Fraction(160)


def test_identity_zero_visitors_is_pure_decay():
    assert paid_end(1000, HALF, [Fraction(0)] * 3) == Fraction(125)


def test_identity_exact_cohort_math():
    # start 100, churn 1/2, cohorts [40, 80]:
    # end = 100*(1/2)^2 + 40*(1/2)^1 + 80*(1/2)^0 = 25 + 20 + 80 = 125
    assert paid_end(100, HALF, [Fraction(40), Fraction(80)]) == 125


def test_new_paid_three_factor_chain():
    assert new_paid(10000, download_or_signup=Fraction(1, 20),
                    activation=Fraction(1, 2),
                    trial_to_paid=Fraction(2, 5)) == Fraction(100)


def test_combined_rate_equals_factored_rate():
    factored = new_paid(1000, download_or_signup=Fraction(1, 20),
                        activation=Fraction(1, 2),
                        trial_to_paid=Fraction(2, 5))
    combined = new_paid(1000, download_or_signup=Fraction(1, 20),
                        activation=Fraction(1),
                        trial_to_paid=Fraction(1, 5))
    assert factored == combined


def test_monotonic_in_visitors():
    base = simulate(months=2, paid_start=0, churn=Fraction(1, 10),
                    monthly_visitors=[100, 100],
                    download_or_signup=Fraction(1, 20),
                    activation=Fraction(1),
                    trial_to_paid=Fraction(1, 5))
    more = simulate(months=2, paid_start=0, churn=Fraction(1, 10),
                    monthly_visitors=[200, 100],
                    download_or_signup=Fraction(1, 20),
                    activation=Fraction(1),
                    trial_to_paid=Fraction(1, 5))
    assert more["paid_end"] >= base["paid_end"]


def test_deterministic_versioned_output():
    kwargs = dict(months=1, paid_start=0, churn=Fraction(0),
                  monthly_visitors=[100],
                  download_or_signup=Fraction(1, 20),
                  activation=Fraction(1), trial_to_paid=Fraction(1, 5))
    first, second = simulate(**kwargs), simulate(**kwargs)
    assert first == second
    assert first["schema_version"] == 1


def test_contract_scenario_meets_target_exactly():
    result = check_target()
    assert result["meets_target"] is True
    assert result["paid_end_exact"] == "1000"


@pytest.mark.parametrize("bad", [
    dict(months=-1, paid_start=0, churn=Fraction(0),
         monthly_visitors=[], download_or_signup=Fraction(0),
         activation=Fraction(0), trial_to_paid=Fraction(0)),
    dict(months=2, paid_start=0, churn=Fraction(0),
         monthly_visitors=[1], download_or_signup=Fraction(0),
         activation=Fraction(0), trial_to_paid=Fraction(0)),
    dict(months=1, paid_start=-5, churn=Fraction(0),
         monthly_visitors=[0], download_or_signup=Fraction(0),
         activation=Fraction(0), trial_to_paid=Fraction(0)),
])
def test_malformed_simulation_inputs_rejected(bad):
    with pytest.raises(FunnelError):
        simulate(**bad)


@pytest.mark.parametrize("rate", [-0.1, 1.0, 1.5, "high", True])
def test_bad_rates_rejected(rate):
    from tools.funnel_calculator import _rate
    with pytest.raises(FunnelError):
        _rate(rate, "test")
