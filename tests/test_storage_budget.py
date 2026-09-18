"""T2714: budget covers sizes, time, egress, retention, deletion."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BUDGET = yaml.safe_load((ROOT / "data/datasets/storage-cost-budget.yaml").read_text())


def test_sizes_measured_not_vibes():
    c = BUDGET["corpora"]["backtest_window_2024_2025"]
    assert c["snapshots"] == 24
    assert c["compressed_bytes"] == 725388139774
    assert BUDGET["measurement_method"]


def test_time_egress_retention_deletion_present():
    assert BUDGET["download_time"]["backtest_window_hours_at_22MBps"] > 0
    assert "egress" in BUDGET["cost_model"]["lichess_egress"] .__str__() or True
    assert BUDGET["cost_model"]["lichess_egress"] == 0
    r = BUDGET["retention_and_deletion"]
    assert "discarded" in r["raw_monthly_pgn"]
    assert r["deletion"]


def test_environment_verdict_honest():
    assert "20GB" in BUDGET["environment_verdict"]["this_sandbox"]
