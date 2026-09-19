"""T0062 behavior suite: every T0060 castling fixture case executed
against the T0062 castling runtime (tools/castling_runtime). This is
the T0061 red suite turned green and gate-collected in the same change
that lands the runtime; tests/test_t0062_continuity.py pins its exact
bytes and collected node count so the flip cannot silently reshape it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from tools import castling_runtime as _runtime

ROOT = Path(__file__).resolve().parents[1]
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
    return _runtime


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


# --- T0062 v2: structural API battery (verifier remediation) ---------
# The module pins a public choice: structural defects outside the five
# declared failure classes are CastlingError(code="malformed_request",
# failure_class=None) - NEVER a raw TypeError/KeyError/traceback. The
# fixture carries only shaped states/moves, so these siblings live here.

_STRUCT_GOOD = {
    "rights": "K",
    "occupied": {"e1": "wk", "h1": "wr"},
    "attacked": [],
    "side_to_move": "w",
}


def _assert_structural_rejection(fn, *args):
    with pytest.raises(_runtime.CastlingError) as ei:
        fn(*args)
    err = ei.value
    assert err.code == "malformed_request"
    assert err.failure_class is None
    assert type(err.retryable) is bool
    assert type(err.args[0]) is str and err.args[0].strip()


def test_structural_unhashable_attacked_siblings():
    """Unhashable attacked entries must not escape as raw TypeError from
    the duplicate check - type validation precedes hashing."""
    for bad in ([[]], [{}], [set()], [["e1"]], [{"sq": "e1"}]):
        state = {**_STRUCT_GOOD, "attacked": bad}
        before = copy.deepcopy(state)
        _assert_structural_rejection(
            _runtime.apply, state, {"type": "castle", "right": "K"})
        assert state == before, "apply mutated a structurally rejected input"
        _assert_structural_rejection(_runtime.identity, state)
        _assert_structural_rejection(
            _runtime.turn_effect, state, {"type": "castle", "right": "K"})


def test_structural_move_square_siblings():
    """Unhashable AND non-string hashable squares on both rook loss moves
    reject as the pinned structural choice, never raw TypeError."""
    for t in ("rook_move_from", "rook_capture_on"):
        for bad in ([], {}, set(), ["h1"], ("h1",), 7, 1.5, None, True):
            _assert_structural_rejection(
                _runtime.apply, _STRUCT_GOOD, {"type": t, "square": bad})


def test_structural_state_and_move_container_siblings():
    """The wider unhashable/wrong-container sibling class across state
    fields and the move itself."""
    for bad_state in ([], "state", 7, None, {"rights": "K"}):
        _assert_structural_rejection(
            _runtime.apply, bad_state, {"type": "castle", "right": "K"})
    for bad_field in ([], set(), 7, None, True):
        state = {**_STRUCT_GOOD, "occupied": bad_field}
        _assert_structural_rejection(
            _runtime.apply, state, {"type": "castle", "right": "K"})
        state = {**_STRUCT_GOOD, "attacked": bad_field if not isinstance(bad_field, list) else "e1"}
        _assert_structural_rejection(
            _runtime.apply, state, {"type": "castle", "right": "K"})
    # an empty occupied mapping is STRUCTURALLY valid: the rejection is
    # the declared semantic class, not the structural choice
    with pytest.raises(_runtime.CastlingError) as ei:
        _runtime.apply({**_STRUCT_GOOD, "occupied": {}},
                       {"type": "castle", "right": "K"})
    assert ei.value.failure_class == "rights_inconsistent"
    assert ei.value.code == FAILURE_MAPPING["rights_inconsistent"]["error"]
    for bad_move in ([], "castle", 7, None, {"type": "castle"},
                     {"type": "castle", "right": ["K"]},
                     {"type": "king_move", "side": ["w"]},
                     {"type": ["castle"], "right": "K"}):
        _assert_structural_rejection(_runtime.apply, _STRUCT_GOOD, bad_move)


def test_error_constructor_enforces_class_to_code_mapping():
    """The public constructor cannot be built with a mismatched
    class/code pair - the normative mapping holds everywhere, not only
    through _fail."""
    for cls, m in FAILURE_MAPPING.items():
        # the mapped pair constructs fine
        ok = _runtime.CastlingError(m["error"], "msg", cls, False)
        assert ok.failure_class == cls and ok.code == m["error"]
        # every other in-enum code with this class is refused
        for code in ERROR_ENUM - {m["error"]}:
            with pytest.raises(ValueError):
                _runtime.CastlingError(code, "msg", cls, False)
    # class None still allows any in-enum code (structural path)
    for code in ERROR_ENUM:
        ok = _runtime.CastlingError(code, "msg")
        assert ok.failure_class is None
