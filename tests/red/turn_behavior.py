"""T0052 red suite: every T0051 turn fixture case executed against the
turn runtime (tools/turn_runtime - the T0053 deliverable).

NEVER collected by the default gate: this filename intentionally does
not match the python_files pattern (test_*.py), so directory discovery
skips it; tests/test_t0052_turn_red.py runs it by explicit path and
asserts the red signature. When tools/turn_runtime lands, these tests
must turn green and the T0052 harness flips in the same PR.

Runtime API under test (T0053 target, derived from the T0050 contract):
- apply_move(state: dict, move: str, terminated: str | None = None)
  -> new state dict; never mutates its input
- identity(state: dict) -> the identity projection (side_to_move
  participates; counters never do)
- termination_for(state: dict) -> None | {"state": str, "automatic":
  bool} (fifty-move claim at 100 non-automatic, seventyfive-move
  automatic at 150)
- TurnError(Exception) with .code (closed error enum), .failure_class
  (declared turn failure class or None), .retryable (exact bool)
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "turn" / "cases.json").read_text())
HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]


def _rt():
    """Import the T0053 runtime. A genuine resolution absence (the import
    system itself raising ModuleNotFoundError with .name set to the
    missing module) is re-raised as a marked error so the redness
    harness can distinguish it from a module that EXISTS but raises a
    hand-crafted ModuleNotFoundError internally."""
    try:
        return importlib.import_module("tools.turn_runtime")
    except ModuleNotFoundError as exc:
        if exc.name == "tools.turn_runtime":
            raise RuntimeError(
                "RED-EXPECTED-ABSENT: import system could not resolve "
                "tools.turn_runtime"
            ) from exc
        raise


@pytest.mark.parametrize("case", HAPPY, ids=[c["name"] for c in HAPPY])
def test_happy(case):
    rt = _rt()
    got = rt.apply_move(case["state"], case["move"])
    assert got == case["expect_state"]
    assert got["side_to_move"] != case["state"]["side_to_move"]


@pytest.mark.parametrize("case", BOUNDARY, ids=[c["name"] for c in BOUNDARY])
def test_boundary(case):
    rt = _rt()
    if case["kind"] == "threshold":
        got = rt.apply_move(case["state"], case["move"])
        assert got == case["expect_state"]
        status = rt.termination_for(got)
        assert status is not None, f"{case['name']}: no termination available"
        assert status["state"] == case["expect_termination_available"]
        assert status["automatic"] is case["expect_automatic"]
    elif case["kind"] == "transition":
        got = rt.apply_move(case["state"], case["move"])
        assert got == case["expect_state"]
    else:
        same = rt.identity(case["state"]) == rt.identity(case["other"])
        if case["expect_identity"] == "identical":
            assert same
        else:
            assert not same


@pytest.mark.parametrize("case", MALFORMED, ids=[c["name"] for c in MALFORMED])
def test_malformed(case):
    rt = _rt()
    with pytest.raises(rt.TurnError) as excinfo:
        rt.apply_move(case["state"], case["move"], case.get("terminated"))
    assert excinfo.value.failure_class == case["expect_failure"]


@pytest.mark.parametrize("case", ROLLBACK, ids=[c["name"] for c in ROLLBACK])
def test_rollback(case):
    rt = _rt()
    with pytest.raises(rt.TurnError) as excinfo:
        rt.apply_move(case["state"], case["move"], case.get("terminated"))
    assert excinfo.value.failure_class == case["expect_failure"]
    assert case["state"] == case["expect_state_after"], (
        f"{case['name']}: rejected transition mutated the input state")
