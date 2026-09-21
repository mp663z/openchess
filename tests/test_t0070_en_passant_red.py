"""T0070: en-passant red tests that kill concrete rule mutants."""
from __future__ import annotations

import copy

import pytest

from tests.test_t0069_en_passant_fixture import (
    CASES,
    EnPassantFailure,
    _apply,
)


def _case(section, name):
    return next(c for c in CASES[section] if c["name"] == name)


def _mutant_apply(state, move, defect):
    """Executable mutants, each implementing exactly its named defect."""
    if defect == "retain-target-after-quiet":
        out = _apply(state, move)
        out["ep_target"] = state["ep_target"]
        return out
    if defect == "capture-keeps-victim":
        out = _apply(state, move)
        captured = move["to"][0] + move["from"][1]
        out["occupied"][captured] = ("b" if state["side_to_move"] == "w" else "w") + "p"
        return out
    if defect == "reject-mutates-input":
        try:
            return _apply(state, move)
        except EnPassantFailure:
            state["side_to_move"] = "b" if state["side_to_move"] == "w" else "w"
            raise
    if defect == "pinned-capture-accepted":
        changed = copy.deepcopy(state)
        side = state["side_to_move"]
        captured = move["to"][0] + move["from"][1]
        changed["occupied"].pop(move["from"])
        changed["occupied"].pop(captured)
        changed["occupied"][move["to"]] = side + "p"
        changed["ep_target"] = "-"
        changed["side_to_move"] = "b" if side == "w" else "w"
        return changed
    raise AssertionError(defect)


def test_red_target_expires_after_any_other_move():
    case = _case("boundary", "lifetime-cleared-by-other-move")
    mutant = _mutant_apply(copy.deepcopy(case["state"]), case["move"], "retain-target-after-quiet")
    assert mutant["ep_target"] != case["expect_target"]
    assert _apply(case["state"], case["move"])["ep_target"] == case["expect_target"]


def test_red_capture_removes_the_bypassed_pawn():
    case = _case("happy", "capture-white")
    mutant = _mutant_apply(copy.deepcopy(case["state"]), case["move"], "capture-keeps-victim")
    assert mutant["occupied"] != case["expect_occupied"]
    assert _apply(case["state"], case["move"])["occupied"] == case["expect_occupied"]


def test_red_pinned_capture_cannot_be_laundered_by_dropping_the_king():
    case = _case("rollback", "rollback-pinned-capture")
    mutant = _mutant_apply(copy.deepcopy(case["state"]), case["move"], "pinned-capture-accepted")
    assert mutant != case["expect_state_after"]
    with pytest.raises(EnPassantFailure, match="pinned_capture"):
        _apply(case["state"], case["move"])


def test_red_rejection_is_atomic_with_nonvacuous_mutation_witness():
    case = _case("rollback", "rollback-stale-target")
    state = copy.deepcopy(case["state"])
    before = copy.deepcopy(state)
    with pytest.raises(EnPassantFailure):
        _mutant_apply(state, case["move"], "reject-mutates-input")
    assert state != before, "mutant must actually realize partial-state corruption"

    state = copy.deepcopy(case["state"])
    before = copy.deepcopy(state)
    with pytest.raises(EnPassantFailure, match=case["expect_failure"]):
        _apply(state, case["move"])
    assert state == before == case["expect_state_after"]
