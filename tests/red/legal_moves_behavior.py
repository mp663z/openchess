"""T0079 red suite: every T0078 legal-moves fixture case executed
against the legal-moves runtime (tools/legal_moves_runtime - the T0080
deliverable).

NEVER collected by the default gate: this filename intentionally does
not match the python_files pattern (test_*.py), so directory discovery
skips it; tests/test_t0079_legal_moves_red.py runs it by explicit path
and asserts the red signature. When tools/legal_moves_runtime lands,
these tests must turn green and the T0079 harness flips in the same PR.

Runtime API under test (T0080 target, derived from the T0077/T0078
legal_moves contract and conformance fixture):
- legal_moves(state: dict) -> list[dict]: the exact legal move set;
  each move is move_model-shaped (from_square, to_square, optional
  promotion, no other members); promotion expansion and king-safety
  filtering per the contract
- is_attacked(state: dict, square: str, attacker: str) -> bool: the
  contract attack relation (occupancy-irrelevant, attacker king
  safety ignored)
- terminal_status(state: dict) -> str: move-set terminal
  classification (check / checkmate / stalemate)
- apply(state: dict, move: dict) -> new state dict: the validated
  transition; never mutates its input, so a rejected move is a true
  rollback
- LegalMovesError(Exception) with .failure_class (declared class),
  .code (closed error enum, normative class-to-code mapping),
  .retryable (exact bool)
"""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "legal_moves" / "cases.json").read_text())
_DOC = yaml.safe_load(
    (ROOT / "data" / "contracts" / "legal_moves.yaml").read_text())
_C = _DOC["contract"]
FAILURE_MAPPING = dict(_C["failure_mapping"])
ERROR_ENUM = set(_C["errors"]["closed_enum"])

HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
TERMINAL = CASES["terminal"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]


def _rt():
    """Import the T0080 runtime. A genuine absence (the import system
    itself raising ModuleNotFoundError with .name set to the missing
    module) is re-raised as a marked error so the redness harness can
    distinguish it from a module that EXISTS but raises a hand-crafted
    ModuleNotFoundError internally."""
    try:
        return importlib.import_module("tools.legal_moves_runtime")
    except ModuleNotFoundError as exc:
        if exc.name == "tools.legal_moves_runtime":
            raise RuntimeError(
                "RED-EXPECTED-ABSENT: import system could not resolve "
                "tools.legal_moves_runtime"
            ) from exc
        raise


def _member(moves, move):
    return any(
        m["from_square"] == move["from_square"]
        and m["to_square"] == move["to_square"]
        and m.get("promotion") == move.get("promotion")
        for m in moves)


def _assert_error_shape(err, case: dict) -> None:
    """The contract error contract on every rejection: declared class,
    the NORMATIVE class-to-code mapping, closed-enum membership,
    exact-bool retryable, nonempty string message."""
    assert err.failure_class == case["expect_failure"], case["name"]
    assert type(err.code) is str and err.code in ERROR_ENUM, case["name"]
    assert err.code == FAILURE_MAPPING[case["expect_failure"]]["error"], (
        f"{case['name']}: class {err.failure_class} must emit code"
        f" {FAILURE_MAPPING[case['expect_failure']]['error']},"
        f" got {err.code}")
    assert type(err.retryable) is bool, case["name"]
    assert type(err.args[0]) is str and err.args[0].strip(), case["name"]


@pytest.mark.parametrize("case", HAPPY, ids=[c["name"] for c in HAPPY])
def test_happy(case):
    rt = _rt()
    moves = rt.legal_moves(case["state"])
    assert _member(moves, case["move"]), case["name"]


@pytest.mark.parametrize("case", BOUNDARY, ids=[c["name"] for c in BOUNDARY])
def test_boundary(case):
    rt = _rt()
    kind = case["kind"]
    if kind == "move":
        assert _member(rt.legal_moves(case["state"]), case["move"]), (
            case["name"])
    elif kind == "not-move":
        assert not _member(rt.legal_moves(case["state"]), case["move"]), (
            case["name"])
    elif kind == "attack":
        got = rt.is_attacked(case["state"], case["square"],
                             case["attacker"])
        assert type(got) is bool, case["name"]
        assert got == case["expect_attacked"], case["name"]
    elif kind == "move-set-for-square":
        moves = rt.legal_moves(case["state"])
        got = {m["to_square"] for m in moves
               if m["from_square"] == case["from_square"]}
        assert got == set(case["expect_to_squares"]), case["name"]
    elif kind == "expansion":
        moves = rt.legal_moves(case["state"])
        got = sorted(
            m["promotion"] for m in moves
            if m["from_square"] == case["from_square"]
            and m["to_square"] == case["to_square"])
        assert got == case["expect_promotions"], case["name"]
    else:
        raise AssertionError(f"unknown boundary kind {kind}")


@pytest.mark.parametrize("case", TERMINAL, ids=[c["name"] for c in TERMINAL])
def test_terminal(case):
    rt = _rt()
    assert rt.terminal_status(case["state"]) == case["expect_status"], (
        case["name"])


@pytest.mark.parametrize("case", MALFORMED, ids=[c["name"] for c in MALFORMED])
def test_malformed(case):
    rt = _rt()
    before = copy.deepcopy(case["state"])
    with pytest.raises(rt.LegalMovesError) as excinfo:
        rt.apply(case["state"], case["move"])
    _assert_error_shape(excinfo.value, case)
    assert case["state"] == before, (
        f"{case['name']}: rejection mutated the input")


@pytest.mark.parametrize("case", ROLLBACK, ids=[c["name"] for c in ROLLBACK])
def test_rollback(case):
    rt = _rt()
    before = copy.deepcopy(case["state"])
    with pytest.raises(rt.LegalMovesError) as excinfo:
        rt.apply(case["state"], case["move"])
    _assert_error_shape(excinfo.value, case)
    assert case["state"] == before, (
        f"{case['name']}: rejected move mutated the input")
    assert case["state"] == case["expect_state_after"], (
        f"{case['name']}: rejected transition mutated the input state")
