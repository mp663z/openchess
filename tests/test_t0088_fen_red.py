"""T0088: FEN red tests that expose concrete permissive-parser defects."""
from __future__ import annotations

import copy

import pytest

from tests.test_t0086_fen_contract import FenError, emit_fen, parse_fen
from tests.test_t0087_fen_fixture import CASES, C

MUTATION_MANIFEST = {
    "field-count-five": ("field-count", "malformed_fen"),
    "rank-sum-nine": ("placement-grammar", "malformed_fen"),
    "rank-count-seven": ("placement-grammar", "malformed_fen"),
    "adjacent-digits": ("placement-grammar", "malformed_fen"),
    "active-color-x": ("active-color-grammar", "malformed_fen"),
    "castling-bad-letter": ("castling-grammar", "malformed_fen"),
    "castling-order": ("castling-grammar", "malformed_fen"),
    "ep-rank-wrong": ("ep-grammar", "malformed_fen"),
    "halfmove-leading-zero": ("counter-grammar", "malformed_fen"),
    "fullmove-zero": ("counter-grammar", "malformed_fen"),
    "halfmove-non-ascii-digit": ("counter-grammar", "malformed_fen"),
    "kingless-black": ("position-rules-kings", "impossible_position"),
    "kings-adjacent": ("position-rules-kings", "impossible_position"),
    "pawn-on-back-rank": ("position-rules-pawns", "impossible_position"),
    "non-mover-king-attacked": ("position-rules-check", "impossible_position"),
    "castling-rook-missing": ("castling-consistency", "impossible_position"),
    "ep-halfmove-nonzero": ("ep-consistency", "impossible_position"),
    "ep-mover-pawn-missing": ("ep-consistency", "impossible_position"),
}


def _case(name):
    return next(case for case in CASES["malformed"] if case["name"] == name)


def _repair(case):
    repair = case["minimal_repair"]
    return case["input_fen"].replace(repair["find"], repair["replace"])


def _permissive_mutant(case):
    """Executable mutant: silently repairs its pinned defect then parses."""
    return parse_fen(C, _repair(case))


def _required_external_parse(payload):
    """Required external boundary: all hostile payloads map to FenError."""
    if type(payload) is not str:
        mapping = C["failure_mapping"]["malformed_fen"]
        raise FenError("malformed_fen", mapping["error"])
    return parse_fen(C, payload)


def test_mutation_manifest_is_closed_over_every_malformed_fixture_case():
    observed = {
        case["name"]: (case["layer"], case["expect_failure"])
        for case in CASES["malformed"]
    }
    assert observed == MUTATION_MANIFEST


@pytest.mark.parametrize("name", MUTATION_MANIFEST)
def test_red_each_permissive_mutant_accepts_what_real_parser_rejects(name):
    case = _case(name)
    mutant_position = _permissive_mutant(case)
    assert emit_fen(C, mutant_position) == _repair(case)
    with pytest.raises(FenError) as caught:
        parse_fen(C, case["input_fen"])
    assert caught.value.failure_class == case["expect_failure"]
    assert caught.value.code == C["failure_mapping"][case["expect_failure"]]["error"]


def test_red_rejection_cannot_poison_following_valid_parse():
    case = next(case for case in CASES["rollback"] if case["expect_failure"] == "malformed_fen")
    sentinel = {"position": "unchanged"}
    before = copy.deepcopy(sentinel)
    with pytest.raises(FenError):
        parse_fen(C, case["reject_fen"])
    assert sentinel == before
    parsed = parse_fen(C, case["then_fen"])
    assert emit_fen(C, parsed) == case["expect_fen"]


@pytest.mark.parametrize("payload", [None, True, 0, [], {}, b"fen"])
def test_required_external_oracle_maps_hostile_payloads_to_fen_error(payload):
    with pytest.raises(FenError) as caught:
        _required_external_parse(payload)
    assert caught.value.failure_class == "malformed_fen"
    assert caught.value.code == C["failure_mapping"]["malformed_fen"]["error"]


@pytest.mark.parametrize("payload", [None, True, 0, [], {}, b"fen"])
def test_executable_raw_parser_mutant_is_killed_for_hostile_payloads(payload):
    """The pre-implementation parser leaks raw exceptions; the required
    oracle above does not accept them. This pins the exact red delta
    without treating implementation-language tracebacks as valid."""
    with pytest.raises((AttributeError, TypeError)):
        parse_fen(C, payload)
    with pytest.raises(FenError):
        _required_external_parse(payload)
