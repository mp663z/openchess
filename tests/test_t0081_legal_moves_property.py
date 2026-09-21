"""T0081: legal-move unit and deterministic property checks."""
from __future__ import annotations

import copy
import random

import pytest

from tests.test_t0078_legal_moves_fixture import (
    CASES,
    LegalMoveFailure,
    _apply,
    _legal_moves,
    _validate_move,
)

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


@pytest.mark.parametrize("section", ["happy", "boundary", "terminal"])
def test_color_rank_mirror_is_a_legal_move_bijection(section):
    for case in CASES[section]:
        state = case["state"]
        mirrored = _mirror_state(state)
        original = {_key(m) for m in _legal_moves(state)}
        transformed = {_key(m) for m in _legal_moves(mirrored)}
        expected = {_key(_mirror_move(dict(m))) for m in map(dict, original)}
        assert expected == transformed, case["name"]


def test_every_generated_legal_move_validates_and_preserves_input():
    rng = random.Random(SEED)
    states = [c["state"] for section in ("happy", "boundary", "terminal") for c in CASES[section]]
    counts = {"validated": 0, "applied": 0}
    for _ in range(TRIALS):
        state = copy.deepcopy(rng.choice(states))
        before = copy.deepcopy(state)
        moves = _legal_moves(state)
        if not moves:
            continue
        move = rng.choice(moves)
        _validate_move(state, move)
        counts["validated"] += 1
        result = _apply(state["occupied"], move)
        counts["applied"] += 1
        assert state == before
        assert move["to_square"] in result and move["from_square"] not in result
    assert counts == {"validated": 464, "applied": 464}


def test_rejected_fixture_moves_are_immutable_and_classified():
    for case in [*CASES["malformed"], *CASES["rollback"]]:
        state = copy.deepcopy(case["state"])
        move = copy.deepcopy(case["move"])
        before_state, before_move = copy.deepcopy(state), copy.deepcopy(move)
        with pytest.raises(LegalMoveFailure) as caught:
            _validate_move(state, move)
        assert caught.value.failure_class == case["expect_failure"]
        assert state == before_state
        assert move == before_move
