"""T0061 red suite: every T0060 castling fixture case executed against
the castling runtime (tools/castling_runtime - the T0062 deliverable).

NEVER collected by the default gate: this filename intentionally does
not match the python_files pattern (test_*.py), so directory discovery
skips it; tests/test_t0061_castling_red.py runs it by explicit path and
asserts the red signature. When tools/castling_runtime lands, these
tests must turn green and the T0061 harness flips in the same PR.

Runtime API under test (T0062 target, derived from the T0059 castling
contract):
- apply(state: dict, move: dict) -> new state dict (exactly the four
  fixture state fields); never mutates its input, so a rejected castle
  is a true rollback
- identity(state: dict) -> the identity projection (rights participate
  per the contract's identity section)
- turn_effect(state: dict, move: dict) -> {"halfmove_clock": str,
  "fullmove_number": str} per the contract's turn_linkage (exactly one
  transition, halfmove increment never reset, fullmove when black)
- CastlingError(Exception) with .code (closed error enum),
  .failure_class (declared castling failure class), .retryable (exact
  bool)
"""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "castling" / "cases.json").read_text())
_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "castling.yaml").read_text())
_C = _DOC["contract"]
FAILURE_MAPPING = dict(_C["failure_mapping"])
ERROR_ENUM = set(_C["errors"]["closed_enum"])
RIGHTS_PARTICIPATE = _C["identity"]["rights_participate"]
HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]


def _rt():
    """Import the T0062 runtime. A genuine resolution absence (the import
    system itself raising ModuleNotFoundError with .name set to the
    missing module) is re-raised as a marked error so the redness
    harness can distinguish it from a module that EXISTS but raises a
    hand-crafted ModuleNotFoundError internally."""
    try:
        return importlib.import_module("tools.castling_runtime")
    except ModuleNotFoundError as exc:
        if exc.name == "tools.castling_runtime":
            raise RuntimeError(
                "RED-EXPECTED-ABSENT: import system could not resolve "
                "tools.castling_runtime"
            ) from exc
        raise


def _assert_apply_success(rt, case: dict) -> dict:
    """apply on a valid case: a NEW state object, the input bit-identical
    afterward, and exactly the four declared state fields out."""
    before = copy.deepcopy(case["state"])
    got = rt.apply(case["state"], case["move"])
    assert got is not case["state"], f"{case['name']}: apply returned the input object"
    assert case["state"] == before, f"{case['name']}: apply mutated its input"
    assert set(got) == {"rights", "occupied", "attacked", "side_to_move"}, case["name"]
    return got


def _assert_error_shape(rt, err, case: dict) -> None:
    """The T0059 error contract on every rejection: declared class, the
    NORMATIVE class-to-code mapping, closed-enum membership, exact-bool
    retryable, nonempty string message."""
    assert err.failure_class == case["expect_failure"], case["name"]
    assert type(err.code) is str and err.code in ERROR_ENUM, case["name"]
    assert err.code == FAILURE_MAPPING[case["expect_failure"]]["error"], (
        f"{case['name']}: class {err.failure_class} must emit code"
        f" {FAILURE_MAPPING[case['expect_failure']]['error']}, got {err.code}")
    assert type(err.retryable) is bool, case["name"]
    assert type(err.args[0]) is str and err.args[0].strip(), case["name"]


@pytest.mark.parametrize("case", HAPPY, ids=[c["name"] for c in HAPPY])
def test_happy(case):
    rt = _rt()
    got = _assert_apply_success(rt, case)
    assert got["rights"] == case["expect_rights"]
    if case["kind"] == "castle":
        assert got["occupied"] == case["expect_occupied"]
        assert rt.turn_effect(case["state"], case["move"]) == case["expect_turn"]
    else:
        assert got["occupied"] == case["state"]["occupied"]
    assert got["attacked"] == case["state"]["attacked"]
    assert got["side_to_move"] == case["state"]["side_to_move"]


@pytest.mark.parametrize("case", BOUNDARY, ids=[c["name"] for c in BOUNDARY])
def test_boundary(case):
    rt = _rt()
    if case["kind"] in ("grammar", "occupancy"):
        got = _assert_apply_success(rt, case)
        assert got["rights"] == case["expect_rights"]
        if case["kind"] == "occupancy":
            assert got["occupied"] == case["expect_occupied"]
    else:
        # exact projection, not relational: the identity of a state is
        # EXACTLY the contract-declared participating fields
        expected_state = ({"rights": case["state"]["rights"]}
                          if RIGHTS_PARTICIPATE else {})
        expected_other = ({"rights": case["other"]["rights"]}
                          if RIGHTS_PARTICIPATE else {})
        assert rt.identity(case["state"]) == expected_state, case["name"]
        assert rt.identity(case["other"]) == expected_other, case["name"]
        same = expected_state == expected_other
        if case["expect_identity"] == "identical":
            assert same
        else:
            assert not same


@pytest.mark.parametrize("case", MALFORMED, ids=[c["name"] for c in MALFORMED])
def test_malformed(case):
    rt = _rt()
    before = copy.deepcopy(case["state"])
    with pytest.raises(rt.CastlingError) as excinfo:
        rt.apply(case["state"], case["move"])
    _assert_error_shape(rt, excinfo.value, case)
    assert case["state"] == before, f"{case['name']}: rejection mutated the input"


@pytest.mark.parametrize("case", ROLLBACK, ids=[c["name"] for c in ROLLBACK])
def test_rollback(case):
    rt = _rt()
    before = copy.deepcopy(case["state"])
    with pytest.raises(rt.CastlingError) as excinfo:
        rt.apply(case["state"], case["move"])
    _assert_error_shape(rt, excinfo.value, case)
    assert case["state"] == before, f"{case['name']}: rejected castle mutated the input"
    assert case["state"] == case["expect_state_after"], (
        f"{case['name']}: rejected transition mutated the input state")
