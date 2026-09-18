"""T2236: calibration split is by repertoire, seeded, and leak-guarded."""

import pytest

from backtest.calibration import CalibrationLeakError, repertoire_split


def test_split_is_by_repertoire_and_covers_all():
    ids = [f"rep{i:02d}" for i in range(50)]
    sp = repertoire_split(ids, seed=42)
    assert sp.train | sp.held_out == frozenset(ids)
    assert not (sp.train & sp.held_out)
    assert len(sp.held_out) == 10


def test_split_is_deterministic_for_seed():
    ids = [f"rep{i:02d}" for i in range(50)]
    assert repertoire_split(ids, seed=7) == repertoire_split(ids, seed=7)
    assert repertoire_split(ids, seed=7) != repertoire_split(ids, seed=8)


def test_held_out_leak_raises():
    sp = repertoire_split([f"rep{i:02d}" for i in range(50)], seed=42)
    sp.check_no_leak(set(sp.train))  # fine
    with pytest.raises(CalibrationLeakError):
        sp.check_no_leak(set(sp.train) | {next(iter(sp.held_out))})
