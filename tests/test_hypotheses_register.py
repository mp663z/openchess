"""T0002: exact cohort, thresholds, query, owner and date locked for H1-H5.

Every locked threshold is asserted with full equality against the report v5
text (>= normalized from >=, "section" from the section symbol).
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REG = yaml.safe_load((ROOT / "validation/hypotheses-register.yaml").read_text())
HS = {h["id"]: h for h in REG["hypotheses"]}

EXPECTED = {
    "H1": (
        ">=70% of beta users reach first value in <15 min",
        "Median >30 min or <50% complete",
    ),
    "H2": (
        ">=40% open it in 3 of 4 consecutive weeks on the automated product",
        "<25% weekly retention on the automated product",
    ),
    "H3": (
        ">=40% of surfaced deltas produce an accepted repertoire change still in"
        " the repertoire at 30 days, and the model arm beats the naive-baseline"
        " arm by >=15pp",
        "Model arm fails to beat baseline, or accepted changes revert within 30 days",
    ),
    "H4": (
        "Median weekly human time <=25 min at the observed N (decisions/week),"
        " with N measured inside the funnel (section 9)",
        "Median weekly human time >45 min, or N collapses to near zero for most users",
    ),
    "H5": (
        "Reported alongside the landing-page test",
        "n/a - see section 10",
    ),
}


def test_all_five_registered_with_locked_fields():
    assert set(HS) == {"H1", "H2", "H3", "H4", "H5"}
    assert REG["locked_at"] == "2026-09-19"
    assert REG["owner_role"] == "product owner"
    for hid, h in HS.items():
        assert h["cohort"], hid
        assert h["pass_threshold"], hid
        assert h["fail_threshold"], hid
        assert h["measurement_query"], hid
        assert h["status"].startswith("registered"), hid


def test_every_threshold_full_equality():
    for hid, (pass_t, fail_t) in EXPECTED.items():
        assert HS[hid]["pass_threshold"] == pass_t, hid
        assert HS[hid]["fail_threshold"] == fail_t, hid


def test_cohorts_match_report_measurement_plan():
    # H0-H4 are measured in the concierge beta (30 users, 2000-2200 weekly
    # players, v5 section 11); only H2 is gated on the automated product.
    for hid in ("H1", "H3", "H4"):
        assert "concierge beta" in HS[hid]["cohort"], hid
        assert "2000-2200" in HS[hid]["cohort"], hid
    assert "automated" not in HS["H3"]["cohort"]
    assert "automated" not in HS["H4"]["cohort"]
    assert "automated V0.1 product users only" in HS["H2"]["cohort"]


def test_h3_ab_inside_beta_and_h5_demoted():
    assert "naive baseline" in HS["H3"]["cohort"]
    assert "alongside the landing-page test" in HS["H5"]["cohort"]
    assert "directional only" in HS["H2"]["cohort"]
    assert HS["H5"]["status"] == "registered_demoted_signal"
