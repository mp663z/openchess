"""T0088: FEN red tests that expose concrete permissive-parser defects."""
from __future__ import annotations

import copy

import pytest

from tests.test_t0086_fen_contract import FenError, emit_fen, parse_fen
from tests.test_t0087_fen_fixture import CASES, C


def _case(name):
    return next(case for case in CASES["malformed"] if case["name"] == name)


def _repair(case):
    repair = case["minimal_repair"]
    return case["input_fen"].replace(repair["find"], repair["replace"])


def _permissive_mutant(case):
    """Executable mutant: silently repairs its pinned defect then parses."""
    return parse_fen(C, _repair(case))


@pytest.mark.parametrize(
    "name",
    [
        "field-count-five",
        "rank-sum-nine",
        "active-color-x",
        "castling-bad-letter",
        "ep-rank-wrong",
        "halfmove-leading-zero",
        "kingless-black",
        "kings-adjacent",
        "pawn-on-back-rank",
        "castling-rook-missing",
        "ep-halfmove-nonzero",
    ],
)
def test_red_each_permissive_mutant_accepts_what_real_parser_rejects(name):
    case = _case(name)
    mutant_position = _permissive_mutant(case)
    assert emit_fen(C, mutant_position) == _repair(case)
    with pytest.raises(FenError) as caught:
        parse_fen(C, case["input_fen"])
    assert caught.value.failure_class == case["expect_failure"]


def test_red_rejection_cannot_poison_following_valid_parse():
    case = next(case for case in CASES["rollback"] if case["expect_failure"] == "malformed_fen")
    sentinel = {"position": "unchanged"}
    before = copy.deepcopy(sentinel)
    with pytest.raises(FenError):
        parse_fen(C, case["reject_fen"])
    assert sentinel == before
    parsed = parse_fen(C, case["then_fen"])
    assert emit_fen(C, parsed) == case["expect_fen"]


def test_red_bool_and_non_string_payloads_fail_closed_without_tracebacks():
    for payload in (None, True, 0, [], {}, b"fen"):
        with pytest.raises((FenError, AttributeError, TypeError)) as caught:
            parse_fen(C, payload)
        assert not isinstance(caught.value, (SystemExit, KeyboardInterrupt))
