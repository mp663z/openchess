"""T0080 behavior suite: every T0078 legal-moves fixture case executed
against the T0080 legal-moves runtime (tools/legal_moves_runtime). This
is the T0079 red suite turned green and gate-collected in the same
change that lands the runtime; tests/test_t0080_continuity.py pins its
exact bytes and collected node count so the flip cannot silently
reshape it.

Runtime API under test (derived from the T0077/T0078 legal_moves
contract and conformance fixture):
- legal_moves(state: dict) -> list[dict]: the exact legal move set;
  each move is move_model-shaped (from_square, to_square, optional
  promotion, no other members); promotion expansion and king-safety
  filtering per the contract
- is_attacked(state: dict, square: str, attacker: str) -> bool: the
  contract attack relation (occupancy-irrelevant, attacker king
  safety ignored)
- terminal_status(state: dict) -> str: move-set terminal
  classification (check / checkmate / stalemate / none)
- apply(state: dict, move: dict) -> new state dict: the validated
  transition; never mutates its input, so a rejected move is a true
  rollback
- LegalMovesError(Exception) with .failure_class (declared class),
  .code (closed error enum, normative class-to-code mapping),
  .retryable (exact bool)
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from tools import legal_moves_runtime as _runtime

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "legal_moves" / "cases.json").read_text())
_DOC = yaml.safe_load(
    (ROOT / "data" / "contracts" / "legal_moves.yaml").read_text())
_C = _DOC["contract"]
FAILURE_MAPPING = dict(_C["failure_mapping"])
ERROR_ENUM = set(_C["errors"]["closed_enum"])
EXPANSION = dict(_C["move_model"]["promotion_expansion"])

HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
TERMINAL = CASES["terminal"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]


def _rt():
    return _runtime


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
        # NOTE: the T0079 red suite compared against the RAW
        # expect_promotions list; the T0078 fixture assertion this
        # mirrors compares sorted-to-sorted plus the expansion count
        # and the no-unpromoted-move pin. The raw comparison can never
        # hold (sorted output is alphabetical, the fixture lists the
        # contract enum order), so the green flip restores the T0078
        # form - the red suite was approved for its red signature and
        # never executed green.
        moves = rt.legal_moves(case["state"])
        got = sorted(
            m["promotion"] for m in moves
            if m["from_square"] == case["from_square"]
            and m["to_square"] == case["to_square"])
        assert got == sorted(case["expect_promotions"]), case["name"]
        assert len(got) == EXPANSION["expands_to"], case["name"]
        # no unpromoted move to the promotion rank exists
        assert {"from_square": case["from_square"],
                "to_square": case["to_square"]} not in moves, case["name"]
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



# --- T0080: structural API battery -----------------------------------
# The runtime pins a public choice: structural defects outside the
# declared failure classes are LegalMovesError(code="malformed_request",
# failure_class=None) - NEVER a raw TypeError/KeyError/StopIteration.
# The fixture carries only shaped states/moves, so these siblings live
# here.

_STRUCT_GOOD = {
    "occupied": {"e1": "wk", "e8": "bk"},
    "side_to_move": "w",
}


def _assert_structural_rejection(fn, *args):
    with pytest.raises(_runtime.LegalMovesError) as ei:
        fn(*args)
    err = ei.value
    assert err.code == "malformed_request"
    assert err.failure_class is None
    assert type(err.retryable) is bool
    assert type(err.args[0]) is str and err.args[0].strip()


def test_structural_state_container_siblings():
    """Wrong-container and wrong-shape states reject as the pinned
    structural choice on every state-taking entry point."""
    for bad in ([], "state", 7, None, True, {"occupied": {}},
                {"occupied": {}, "side_to_move": "w", "extra": 1},
                {**_STRUCT_GOOD, "occupied": []},
                {**_STRUCT_GOOD, "occupied": {"e1": "wk"}},
                {**_STRUCT_GOOD, "occupied": {"e1": "wk", "e8": "bk",
                                              "a1": "wk"}},
                {**_STRUCT_GOOD, "occupied": {"e1": "wk", "e9": "bk"}},
                {**_STRUCT_GOOD, "occupied": {"e1": "wk", "e8": "bx"}},
                {**_STRUCT_GOOD, "occupied": {"e1": "wk", "e8": "bk"},
                 "side_to_move": "x"},
                {**_STRUCT_GOOD, "side_to_move": ["w"]}):
        for fn in (_runtime.legal_moves, _runtime.terminal_status):
            _assert_structural_rejection(fn, bad)
        _assert_structural_rejection(
            _runtime.apply, bad, {"from_square": "e1", "to_square": "e2"})
        _assert_structural_rejection(
            _runtime.is_attacked, bad, "e1", "w")


def test_structural_is_attacked_argument_siblings():
    """Bad square and attacker arguments reject structurally; an
    unhashable square never escapes as a raw TypeError."""
    for bad_square in ([], {}, set(), ["e1"], ("e1",), 7, 1.5, None,
                       True, "e9", "i1", "e1 ", "E1", "e11", "a0"):
        _assert_structural_rejection(
            _runtime.is_attacked, _STRUCT_GOOD, bad_square, "w")
    for bad_attacker in ([], {}, 7, None, True, "x", "W", "white"):
        _assert_structural_rejection(
            _runtime.is_attacked, _STRUCT_GOOD, "e1", bad_attacker)
    # exact-bool verdicts on valid arguments
    assert _runtime.is_attacked(_STRUCT_GOOD, "e1", "w") is False
    assert _runtime.is_attacked(_STRUCT_GOOD, "e2", "w") is True


def test_structural_move_container_is_declared_class():
    """A non-mapping move FAILS the move_model shape schema, so it is
    the declared malformed_move class - not the structural choice and
    never a raw escape. Unhashable/wrong-container members reject the
    same way."""
    for bad_move in ([], "e2e4", 7, None, True,
                     {"from_square": "e1"},
                     {"from_square": "e1", "to_square": "e2",
                      "promotion": ["q"]},
                     {"from_square": ["e1"], "to_square": "e2"},
                     {"from_square": "e1", "to_square": {"sq": "e2"}},
                     {"from_square": "e1", "to_square": "e1"},
                     {"from_square": "e1", "to_square": "e2",
                      "extra": 1}):
        with pytest.raises(_runtime.LegalMovesError) as ei:
            _runtime.apply(_STRUCT_GOOD, bad_move)
        assert ei.value.failure_class == "malformed_move"
        assert ei.value.code == FAILURE_MAPPING["malformed_move"]["error"]


def test_apply_success_returns_new_state_without_mutation():
    """apply on a legal move: a NEW state object, the input
    bit-identical, exactly the two declared state fields out, the move
    applied, side_to_move carried (turn advancement is the turn
    contract's)."""
    state = {"occupied": {"e1": "wk", "e8": "bk", "a2": "wp"},
             "side_to_move": "w"}
    before = copy.deepcopy(state)
    got = _runtime.apply(state, {"from_square": "a2", "to_square": "a4"})
    assert got is not state
    assert got["occupied"] is not state["occupied"]
    assert state == before
    assert set(got) == {"occupied", "side_to_move"}
    assert got["occupied"] == {"e1": "wk", "e8": "bk", "a4": "wp"}
    assert got["side_to_move"] == "w"
    # promotion expansion: four distinct move objects, never one
    promo_state = {"occupied": {"e1": "wk", "e8": "bk", "a7": "wp"},
                   "side_to_move": "w"}
    promos = [m for m in _runtime.legal_moves(promo_state)
              if m["from_square"] == "a7" and m["to_square"] == "a8"]
    assert sorted(m["promotion"] for m in promos) == ["b", "n", "q", "r"]
    assert {"from_square": "a7", "to_square": "a8"} not in [
        {k: v for k, v in m.items() if k != "promotion"}
        for m in _runtime.legal_moves(promo_state) if "promotion" not in m]


def test_error_constructor_enforces_class_to_code_mapping():
    """The public constructor cannot be built with a mismatched
    class/code pair - the normative mapping holds everywhere, not only
    through the internal raise path."""
    for cls, m in FAILURE_MAPPING.items():
        ok = _runtime.LegalMovesError(m["error"], "msg", cls, False)
        assert ok.failure_class == cls and ok.code == m["error"]
        for code in ERROR_ENUM - {m["error"]}:
            with pytest.raises(ValueError):
                _runtime.LegalMovesError(code, "msg", cls, False)
    for code in ERROR_ENUM:
        ok = _runtime.LegalMovesError(code, "msg")
        assert ok.failure_class is None
