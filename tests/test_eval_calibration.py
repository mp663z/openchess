"""T2720: eval orientation, mate encoding, depth/knodes, engine semantics."""

import pytest

from ingest.eval_calibration import (
    MATE_CP_BASE,
    calibrate_eval_row,
    mate_to_cp,
    quality_tier,
    to_white_perspective,
)

STARTPOS_W = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
STARTPOS_B = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"


def row(fen, cp=None, mate=None, depth=40, knodes=1000, engine="Stockfish 16"):
    return {
        "fen": fen,
        "engine": engine,
        "evals": [
            {"knodes": knodes, "depth": depth, "pvs": [{"cp": cp, "mate": mate, "line": "d2d4"}]}
        ],
    }


def test_orientation_white_to_move():
    c = calibrate_eval_row(row(STARTPOS_W, cp=15))
    assert c.white_perspective_cp == 15


def test_orientation_black_to_move_flips():
    c = calibrate_eval_row(row(STARTPOS_B, cp=15))
    assert c.white_perspective_cp == -15


def test_mate_encoding_normalized_onto_cp_scale():
    assert mate_to_cp(1) == MATE_CP_BASE - 1
    assert mate_to_cp(-1) == -(MATE_CP_BASE - 1)
    assert mate_to_cp(5) > mate_to_cp(10)  # faster mate scores higher
    with pytest.raises(ValueError):
        mate_to_cp(0)


def test_mate_rows_use_mate_not_mixed_with_cp():
    c_mate = calibrate_eval_row(row(STARTPOS_W, mate=3))
    assert c_mate.cp is None and c_mate.mate == 3
    assert c_mate.white_perspective_cp == MATE_CP_BASE - 3
    c_mated = calibrate_eval_row(row(STARTPOS_B, mate=-2))
    assert c_mated.white_perspective_cp == MATE_CP_BASE - 2  # black mating => good for black


def test_exactly_one_of_cp_mate():
    with pytest.raises(ValueError):
        to_white_perspective("w", cp=10, mate=2)
    with pytest.raises(ValueError):
        to_white_perspective("w", cp=None, mate=None)


def test_depth_knodes_quality_tiers():
    assert quality_tier(44, 3558) == "deep"
    assert quality_tier(30, 1000) == "deep"
    assert quality_tier(25, 500) == "standard"
    assert quality_tier(20, 100) == "standard"
    assert quality_tier(10, 5) == "shallow"


def test_engine_carried_and_bad_fen_rejected():
    c = calibrate_eval_row(row(STARTPOS_W, cp=0, engine="Stockfish 16.1"))
    assert c.engine == "Stockfish 16.1"
    with pytest.raises(ValueError):
        calibrate_eval_row(row("no-side-to-move", cp=0))
