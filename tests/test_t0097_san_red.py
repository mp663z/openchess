"""T0097: SAN red tests that kill concrete rule mutants.

Each mutant wraps the T0095 contract reference (resolve_san/emit_san)
and implements exactly one named defect. The T0096 fixture is the
oracle: the reference passes every row, and every mutant fails exactly
its pinned set of rows (never an empty set). The T0098 runtime binds
here later through RESOLVE/EMIT.
"""
from __future__ import annotations

import copy
import json
import re

import pytest

import tests.test_t0095_san_contract as san_ref
from tests.test_t0096_san_fixture import CASES, DOC, SECTIONS

CONTRACT = DOC["contract"]
MAPPING = CONTRACT["failure_mapping"]
SanError = san_ref.SanError
RESOLVE = san_ref.resolve_san
EMIT = san_ref.emit_san
SUCCESS = ("happy", "boundary")


def _rows():
    return [(section, case) for section in SECTIONS for case in CASES[section]]


def _failure(resolve, text, state):
    """(failure_class, code) of a rejection, or None when it resolves."""
    try:
        resolve(CONTRACT, text, state)
    except SanError as err:
        return err.failure_class, err.code
    return None


def _row_fails(resolve, emit, section, case):
    """True when (resolve, emit) disagrees with the fixture row."""
    state = copy.deepcopy(case["state"])
    before = json.dumps(state)
    try:
        if section in SUCCESS:
            if resolve(CONTRACT, case["san"], state) != case["expect_move"]:
                return True
            if emit(CONTRACT, copy.deepcopy(case["expect_move"]),
                    copy.deepcopy(case["state"])) != case["san"]:
                return True
        else:
            want = (case["expect_failure"], MAPPING[case["expect_failure"]]["error"])
            if _failure(resolve, case["san"], state) != want:
                return True
            if section == "malformed":
                fixed = copy.deepcopy(case["state"])
                move = resolve(CONTRACT, case["repair_san"], fixed)
                if emit(CONTRACT, move, copy.deepcopy(case["state"])) != case["repair_san"]:
                    return True
    except Exception:  # noqa: BLE001 - any raw escape disagrees with the row
        return True
    return json.dumps(state) != before or state != case["state"]


def _oracle(resolve=None, emit=None):
    resolve = resolve or RESOLVE
    emit = emit or EMIT
    return sorted(case["name"] for section, case in _rows()
                  if _row_fails(resolve, emit, section, case))


# -- executable mutants, each implementing exactly its named defect ------------


def _piece_of(state, move):
    return state["occupied"][move["from_square"]][1]


def _m_accept_lowercase_piece(contract, text, state):
    if text and text[0] in "nbrqk":
        text = text[0].upper() + text[1:]
    return RESOLVE(contract, text, state)


def _m_accept_digit_castle(contract, text, state):
    return RESOLVE(contract, text.replace("0", "O"), state)


def _m_ambiguous_picks_first(contract, text, state):
    try:
        return RESOLVE(contract, text, state)
    except SanError as err:
        if err.failure_class != "ambiguous_san":
            raise
        body = text.rstrip("+#")
        letter = body[0].lower() if body[0] in "NBRQK" else "p"
        return next(m for m in san_ref._all_moves(state)
                    if m["to_square"] == body[-2:] and _piece_of(state, m) == letter)


def _m_ignore_suffix_claim(contract, text, state):
    try:
        return RESOLVE(contract, text, state)
    except SanError as err:
        if err.failure_class != "no_legal_match":
            raise
        body = text.rstrip("+#")
        for suffix in ("", "+", "#"):
            try:
                return RESOLVE(contract, body + suffix, state)
            except SanError:
                pass
        raise


def _m_accept_overdisambiguation(contract, text, state):
    try:
        return RESOLVE(contract, text, state)
    except SanError as err:
        match = re.fullmatch(r"([NBRQK])([a-h1-8]{1,2})(x?[a-h][1-8].*)", text)
        if err.failure_class != "malformed_san" or match is None:
            raise
        return RESOLVE(contract, match.group(1) + match.group(3), state)


def _m_accept_annotation(contract, text, state):
    return RESOLVE(contract, text.rstrip("!?"), state)


def _m_nearest_move_fallback(contract, text, state):
    try:
        return RESOLVE(contract, text, state)
    except SanError as err:
        if err.failure_class != "no_legal_match":
            raise
        letter = text[0].lower() if text[0] in "NBRQK" else "p"
        return next(m for m in san_ref._all_moves(state) if _piece_of(state, m) == letter)


def _m_reject_mutates_input(contract, text, state):
    try:
        return RESOLVE(contract, text, state)
    except SanError:
        state["side_to_move"] = "b" if state["side_to_move"] == "w" else "w"
        raise


def _m_promotion_dropped(contract, text, state):
    move = dict(RESOLVE(contract, text, state))
    move.pop("promotion", None)
    return move


def _m_failure_class_collapsed(contract, text, state):
    try:
        return RESOLVE(contract, text, state)
    except SanError:
        raise SanError("no_legal_match", MAPPING["no_legal_match"]["error"]) from None


def _m_wrong_error_code(contract, text, state):
    try:
        return RESOLVE(contract, text, state)
    except SanError as err:
        raise SanError(err.failure_class, "malformed_request") from None


def _m_emit_drops_suffix(contract, move, state):
    return EMIT(contract, move, state).rstrip("+#")


def _m_emit_rank_first(contract, move, state):
    swapped = copy.deepcopy(contract)
    swapped["disambiguation"]["preference_order"] = ["rank", "file", "both"]
    return EMIT(swapped, move, state)


def _m_emit_pawn_capture_without_file(contract, move, state):
    out = EMIT(contract, move, state)
    return out[1:] if re.match(r"[a-h]x", out) else out


def _no_rook_hop(fn):
    """Castling applied as a bare king move: the rook never hops."""
    def wrapped(contract, arg, state):
        saved = san_ref._apply_move
        san_ref._apply_move = lambda occ, move, side: san_ref._apply(occ, move)
        try:
            return fn(contract, arg, state)
        finally:
            san_ref._apply_move = saved
    return wrapped


# name -> (resolve, emit); None keeps the reference side
MUTANTS = {
    "accept-lowercase-piece": (_m_accept_lowercase_piece, None),
    "accept-digit-castle": (_m_accept_digit_castle, None),
    "ambiguous-picks-first": (_m_ambiguous_picks_first, None),
    "ignore-suffix-claim": (_m_ignore_suffix_claim, None),
    "accept-overdisambiguation": (_m_accept_overdisambiguation, None),
    "accept-annotation": (_m_accept_annotation, None),
    "nearest-move-fallback": (_m_nearest_move_fallback, None),
    "reject-mutates-input": (_m_reject_mutates_input, None),
    "promotion-dropped": (_m_promotion_dropped, None),
    "failure-class-collapsed": (_m_failure_class_collapsed, None),
    "wrong-error-code": (_m_wrong_error_code, None),
    "emit-drops-suffix": (None, _m_emit_drops_suffix),
    "emit-rank-first": (None, _m_emit_rank_first),
    "emit-pawn-capture-without-file": (None, _m_emit_pawn_capture_without_file),
    "castling-without-rook-hop": (_no_rook_hop(RESOLVE), _no_rook_hop(EMIT)),
}

# exact fixture rows each mutant fails; a mutant that starts failing more
# or fewer rows is a changed defect, not a silent pass
EXPECTED_KILLS = {
    "accept-lowercase-piece": ["lowercase-piece"],
    "accept-digit-castle": ["digit-castle"],
    "ambiguous-picks-first": ["ambiguous-rook"],
    "ignore-suffix-claim": ["false-check"],
    "accept-overdisambiguation": ["overdisambiguated"],
    "accept-annotation": ["malformed-token-no-trace"],
    "nearest-move-fallback": ["false-check", "illegal-destination-no-trace"],
    "reject-mutates-input": [
        "ambiguous-rook", "digit-castle", "false-check",
        "illegal-destination-no-trace", "lowercase-piece",
        "malformed-token-no-trace", "overdisambiguated"],
    "promotion-dropped": ["promotion"],
    "failure-class-collapsed": [
        "ambiguous-rook", "digit-castle", "lowercase-piece",
        "malformed-token-no-trace", "overdisambiguated"],
    "wrong-error-code": [
        "ambiguous-rook", "false-check", "illegal-destination-no-trace"],
    "emit-drops-suffix": ["castling-rook-hop-check"],
    "emit-rank-first": ["ambiguous-rook", "minimal-file-disambiguation"],
    "emit-pawn-capture-without-file": ["pawn-capture"],
    "castling-without-rook-hop": ["castling-rook-hop-check"],
}


def _case(name):
    return next(case for _s, case in _rows() if case["name"] == name)


def test_reference_passes_every_fixture_row():
    assert _oracle() == []


def test_mutant_table_is_closed():
    assert list(MUTANTS) == list(EXPECTED_KILLS)
    killed = {name for rows in EXPECTED_KILLS.values() for name in rows}
    # every malformed/rollback row and every grammar family is exercised
    assert {case["name"] for s, case in _rows() if s not in SUCCESS} <= killed


@pytest.mark.parametrize("name", list(MUTANTS))
def test_red_mutant_fails_exactly_its_rows(name):
    resolve, emit = MUTANTS[name]
    assert EXPECTED_KILLS[name], name
    assert _oracle(resolve, emit) == EXPECTED_KILLS[name]


@pytest.mark.parametrize("name", list(MUTANTS))
def test_red_mutant_restores_the_reference_module(name):
    resolve, emit = MUTANTS[name]
    saved = san_ref._apply_move
    _oracle(resolve, emit)
    assert san_ref._apply_move is saved
    assert _oracle() == []


def test_red_lowercase_piece_is_malformed_not_a_knight_move():
    case = _case("lowercase-piece")
    assert _m_accept_lowercase_piece(CONTRACT, case["san"], copy.deepcopy(case["state"])) \
        == {"from_square": "g1", "to_square": "f3"}
    assert _failure(RESOLVE, case["san"], copy.deepcopy(case["state"])) == \
        ("malformed_san", "malformed_request")


def test_red_ambiguous_san_is_never_guessed():
    case = _case("ambiguous-rook")
    guess = _m_ambiguous_picks_first(CONTRACT, case["san"], copy.deepcopy(case["state"]))
    assert guess["to_square"] == "d1"
    assert _failure(RESOLVE, case["san"], copy.deepcopy(case["state"])) == \
        ("ambiguous_san", "ambiguous_move")


def test_red_false_suffix_claim_is_no_legal_match():
    case = _case("false-check")
    assert _m_ignore_suffix_claim(CONTRACT, case["san"], copy.deepcopy(case["state"])) \
        == {"from_square": "e2", "to_square": "e4"}
    assert _failure(RESOLVE, case["san"], copy.deepcopy(case["state"])) == \
        ("no_legal_match", "illegal_move")


def test_red_castling_suffix_needs_the_rook_hop():
    case = _case("castling-rook-hop-check")
    state = copy.deepcopy(case["state"])
    assert _no_rook_hop(EMIT)(CONTRACT, case["expect_move"], state) == "O-O-O"
    with pytest.raises(SanError):
        _no_rook_hop(RESOLVE)(CONTRACT, case["san"], copy.deepcopy(case["state"]))
    assert EMIT(CONTRACT, case["expect_move"], state) == "O-O-O+"
    assert RESOLVE(CONTRACT, case["san"], state) == case["expect_move"]


def test_red_minimal_disambiguation_prefers_the_file():
    case = _case("minimal-file-disambiguation")
    state = copy.deepcopy(case["state"])
    assert _m_emit_rank_first(CONTRACT, case["expect_move"], state) == "Rh1d1"
    assert EMIT(CONTRACT, case["expect_move"], state) == "Rhd1"


def test_red_rejection_is_atomic_with_nonvacuous_mutation_witness():
    for name in ("illegal-destination-no-trace", "malformed-token-no-trace"):
        case = _case(name)
        state = copy.deepcopy(case["state"])
        with pytest.raises(SanError):
            _m_reject_mutates_input(CONTRACT, case["san"], state)
        assert state != case["state"], "mutant must realize the corruption"
        state = copy.deepcopy(case["state"])
        before = json.dumps(state)
        with pytest.raises(SanError) as err:
            RESOLVE(CONTRACT, case["san"], state)
        assert err.value.failure_class == case["expect_failure"]
        assert json.dumps(state) == before and state == case["state"]


def _sweep():
    """Every legal move (castling included) of every distinct fixture state."""
    seen, out = set(), []
    for _s, case in _rows():
        key = json.dumps(case["state"], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        for move in san_ref._all_moves(case["state"]):
            out.append((case["state"], move))
    return out


def test_sweep_is_non_trivial():
    assert len(_sweep()) >= 60


@pytest.mark.parametrize("i", range(len(_sweep())))
def test_every_legal_move_round_trips_through_the_reference(i):
    state, move = _sweep()[i]
    text = EMIT(CONTRACT, copy.deepcopy(move), copy.deepcopy(state))
    frozen = copy.deepcopy(state)
    assert RESOLVE(CONTRACT, text, frozen) == move
    assert frozen == state
