"""T0002: exact cohort, thresholds, query, owner and date locked for H1-H5."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REG = yaml.safe_load((ROOT / "validation/hypotheses-register.yaml").read_text())


def test_all_five_registered_with_locked_fields():
    hs = {h["id"]: h for h in REG["hypotheses"]}
    assert set(hs) == {"H1", "H2", "H3", "H4", "H5"}
    assert REG["locked_at"] == "2026-09-19"
    assert REG["owner_role"] == "product owner"
    for hid, h in hs.items():
        assert h["cohort"], hid
        assert h["pass_threshold"], hid
        assert h["fail_threshold"] is not None and h["fail_threshold"] != "", hid
        assert h["measurement_query"], hid
        assert h["status"].startswith("registered"), hid


def test_thresholds_match_report_exactly():
    hs = {h["id"]: h for h in REG["hypotheses"]}
    assert hs["H1"]["pass_threshold"] == ">=70% of beta users reach first value in <15 min"
    assert hs["H1"]["fail_threshold"] == "Median >30 min or <50% complete"
    assert "<25% weekly retention" in hs["H2"]["fail_threshold"]
    assert ">=15pp" in hs["H3"]["pass_threshold"]
    assert "<=25 min" in hs["H4"]["pass_threshold"]
    assert hs["H5"]["status"] == "registered_demoted_signal"


def test_h2_concierge_cannot_pass_or_fail():
    h2 = next(h for h in REG["hypotheses"] if h["id"] == "H2")
    assert "directional only" in h2["cohort"]
