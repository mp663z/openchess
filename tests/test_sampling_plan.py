"""T2230: sampling plan preregistration completeness."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLAN = yaml.safe_load((ROOT / "validation/backtest-sampling-plan.yaml").read_text())


def test_frozen_before_run():
    assert PLAN["frozen_before_run"] is True
    assert PLAN["amendments"] == []


def test_fifty_repertoires_and_bands():
    assert PLAN["corpus"]["repertoires"]["count"] == 50
    assert PLAN["selection_rules"]["rating_bands"] == [
        "1800-1999",
        "2000-2199",
        "2200-2399",
        "2400+",
    ]


def test_minimum_games_and_exclusions():
    mg = PLAN["selection_rules"]["minimum_games"]
    assert mg["rated_standard_games_in_window"] >= 100
    assert PLAN["selection_rules"]["exclusions"], "exclusions must be preregistered"


def test_leakage_control_present():
    lc = PLAN["leakage_control"]
    assert "<= t" in lc["rule"]
    assert "label" in lc["label_window"]


def test_kill_rule_matches_report():
    assert "15 percentage points" in PLAN["kill_rule"]["margin"]
    assert PLAN["kill_rule"]["bands"] == ["1800-1999", "2000-2199"]


def test_rights_substitution_documented():
    assert "TWIC" in PLAN["corpus"]["delta_feed"]["substitution_note"]
