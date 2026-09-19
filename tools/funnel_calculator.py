"""T3762: funnel identity calculator (versioned).

Enforces the funnel identity from the revenue-target contract:
    paid_end = paid_start*(1-churn)^months
               + sum over months i of new_paid_i*(1-churn)^(months-1-i)
    new_paid_i = eligible_visitors_i * download_or_signup * activation
                 * trial_to_paid
The revenue-target contract (report-v5) collapses the last two rates
into free_to_paid = activation * trial_to_paid; the calculator accepts
either the three-factor form or the combined free_to_paid, and treats
their product identically.

All arithmetic is exact Fraction math internally; output rounds only at
presentation. Same input -> same versioned output, always.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGET_CONTRACT = ROOT / "data" / "contracts" / "revenue-target.yaml"

SCHEMA_VERSION = 1


class FunnelError(Exception):
    pass


def _rate(value: object, name: str) -> Fraction:
    if type(value) is bool or type(value) not in (int, float, str,
                                                  Fraction):
        raise FunnelError(f"{name}: rate must be numeric")
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise FunnelError(f"{name}: unparseable rate {value!r}") from exc
    if rate < 0 or rate >= 1:
        raise FunnelError(f"{name}: rate must be in [0, 1), got {rate}")
    return rate


def _nonneg_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise FunnelError(f"{name}: non-negative int required")
    return value


def new_paid(eligible_visitors: int, *, download_or_signup: Fraction,
             activation: Fraction, trial_to_paid: Fraction) -> Fraction:
    """new_paid = eligible_visitors * the three-factor rate chain."""
    return (Fraction(eligible_visitors) * download_or_signup
            * activation * trial_to_paid)


def paid_end(paid_start: int, churn: Fraction,
             monthly_new_paid: list[Fraction]) -> Fraction:
    """The cohort identity: decayed start plus each new cohort decayed
    over its remaining months."""
    months = len(monthly_new_paid)
    total = Fraction(paid_start) * (1 - churn) ** months
    for i, cohort in enumerate(monthly_new_paid):
        total += cohort * (1 - churn) ** (months - 1 - i)
    return total


def simulate(*, months: int, paid_start: int, churn: Fraction,
             monthly_visitors: list[int], download_or_signup: Fraction,
             activation: Fraction, trial_to_paid: Fraction) -> dict:
    _nonneg_int(months, "months")
    _nonneg_int(paid_start, "paid_start")
    if len(monthly_visitors) != months:
        raise FunnelError("monthly_visitors must have months entries")
    for v in monthly_visitors:
        _nonneg_int(v, "monthly_visitors[]")
    cohorts = [new_paid(v, download_or_signup=download_or_signup,
                        activation=activation,
                        trial_to_paid=trial_to_paid)
               for v in monthly_visitors]
    end = paid_end(paid_start, churn, cohorts)
    return {
        "schema_version": SCHEMA_VERSION,
        "months": months,
        "paid_start": paid_start,
        "churn": float(churn),
        "monthly_new_paid": [float(c) for c in cohorts],
        "paid_end": float(end),
        "paid_end_exact": str(end),
    }


def check_target() -> dict:
    """Reproduce the contract scenario: launch Oct 2026, target instant
    2026-12-31 (3 monthly cohorts), report-v5 rates, zero churn (the
    report models none). The identity must land >= the 1,000 target."""
    doc = yaml.safe_load(TARGET_CONTRACT.read_text())
    funnel = doc["funnel"]
    visitors = _nonneg_int(funnel["visitors_during_target_window"],
                           "visitors_during_target_window")
    visitor_to_free = _rate(funnel["visitor_to_free"], "visitor_to_free")
    free_to_paid = _rate(funnel["free_to_paid"], "free_to_paid")
    target = _nonneg_int(
        doc["target"]["active_paying_users"]["value"], "target")
    months = 3  # Oct, Nov, Dec 2026 per schedule.yaml launch window
    monthly = [visitors // months] * months
    monthly[-1] += visitors - sum(monthly)
    result = simulate(
        months=months, paid_start=0, churn=Fraction(0),
        monthly_visitors=monthly,
        download_or_signup=visitor_to_free,
        activation=Fraction(1),  # collapsed into free_to_paid
        trial_to_paid=free_to_paid)
    result["target_active_paying"] = target
    result["meets_target"] = result["paid_end"] >= target
    return result


def main(argv: list[str]) -> int:
    import json
    if "--check-target" in argv:
        result = check_target()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["meets_target"] else 1
    print("usage: funnel_calculator.py --check-target")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
