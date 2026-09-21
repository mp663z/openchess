"""T0081: production legal-move unit and deterministic property checks."""
from __future__ import annotations

import copy
import inspect
import random

import pytest

from tests.test_t0078_legal_moves_fixture import CASES
from tools import legal_moves_runtime as runtime

SEED = 20260921
TRIALS = 500


def _mirror_square(square):
    return square[0] + str(9 - int(square[1]))


def _mirror_state(state):
    return {
        "occupied": {
            _mirror_square(square): ("b" if token[0] == "w" else "w") + token[1]
            for square, token in state["occupied"].items()
        },
        "side_to_move": "b" if state["side_to_move"] == "w" else "w",
    }


def _mirror_move(move):
    return {
        key: (_mirror_square(value) if key in {"from_square", "to_square"} else value)
        for key, value in move.items()
    }


def _key(move):
    return tuple(sorted(move.items()))


def test_property_surface_is_production_runtime_not_fixture_helpers():
    assert runtime.legal_moves.__module__ == "tools.legal_moves_runtime"
    assert runtime.apply.__module__ == "tools.legal_moves_runtime"
    assert runtime.LegalMovesError.__module__ == "tools.legal_moves_runtime"
    module = inspect.getmodule(
        test_property_surface_is_production_runtime_not_fixture_helpers)
    source = inspect.getsource(module)
    assert "from tests.test_t0078_legal_moves_fixture import CASES" in source
    for forbidden in ("_legal_moves", "_validate_move", "_apply"):
        assert f"import {forbidden}" not in source


@pytest.mark.parametrize("section", ["happy", "boundary", "terminal"])
def test_color_rank_mirror_is_a_production_legal_move_bijection(section):
    for case in CASES[section]:
        state = case["state"]
        mirrored = _mirror_state(state)
        original = {_key(m) for m in runtime.legal_moves(state)}
        transformed = {_key(m) for m in runtime.legal_moves(mirrored)}
        expected = {_key(_mirror_move(dict(m))) for m in map(dict, original)}
        assert expected == transformed, case["name"]


def test_generated_production_moves_apply_and_preserve_input():
    rng = random.Random(SEED)
    states = [
        case["state"]
        for section in ("happy", "boundary", "terminal")
        for case in CASES[section]
    ]
    applied = 0
    for _ in range(TRIALS):
        state = copy.deepcopy(rng.choice(states))
        before = copy.deepcopy(state)
        moves = runtime.legal_moves(state)
        if not moves:
            continue
        move = rng.choice(moves)
        result = runtime.apply(state, move)
        applied += 1
        assert state == before
        assert result is not state and result["occupied"] is not state["occupied"]
        assert result["side_to_move"] == state["side_to_move"]
        assert move["to_square"] in result["occupied"]
        assert move["from_square"] not in result["occupied"]
    assert applied == 464


def test_fixture_rejections_use_production_classification_and_rollback():
    for case in [*CASES["malformed"], *CASES["rollback"]]:
        state = copy.deepcopy(case["state"])
        move = copy.deepcopy(case["move"])
        before_state, before_move = copy.deepcopy(state), copy.deepcopy(move)
        with pytest.raises(runtime.LegalMovesError) as caught:
            runtime.apply(state, move)
        error = caught.value
        assert error.failure_class == case["expect_failure"], case["name"]
        assert error.code == runtime.FAILURE_MAPPING[case["expect_failure"]]["error"]
        assert state == before_state
        assert move == before_move
        if "expect_state_after" in case:
            assert state == case["expect_state_after"]
