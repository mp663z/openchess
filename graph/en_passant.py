"""En-passant runtime for data/contracts/en_passant.yaml (T0071).

Applies one move to an en-passant state `{ep_target, occupied,
side_to_move}` and derives the canonical en-passant identity value. The
geometry (target grammar, set-on ranks, mover and captured ranks) and the
failure mapping are read from the contract, never restated here.

Semantics match the T0069 fixture reference interpreter: a two-square pawn
advance records the target; every move clears it; an en-passant capture
must meet every precondition and is judged on the RESULTING position
(pinned_capture); a rejection leaves the input untouched. Inputs are never
mutated and outputs never alias them.

The runtime is total over hostile input: anything that is not an exact
state/move in the fixture's closed domain (exact dicts with exact str keys
and values, declared squares and piece tokens, one king per side, a piece
on a non-capture's from-square) fails closed as `target_malformed`, the
contract's malformed-request class.

Non-capture moves are trusted input from the legal-moves layer: side-to-move
ownership, from != to, and king-capture are not re-validated here; a
non-capture move clears any en-passant target, as cleared_by any-move pins.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "en_passant.yaml"

_C = yaml.safe_load(CONTRACT.read_text())["contract"]
_GRAMMAR = _C["target"]["grammar"]
NONE = _GRAMMAR["none_sentinel"]
_FILES = tuple(_GRAMMAR["files"])
_RANKS = tuple(_GRAMMAR["ranks"])
_SET_ON = {side: _C["target"]["set_on"][name] for side, name in (("w", "white"), ("b", "black"))}
_MOVER = {side: _C["capture"]["mover"][name] for side, name in (("w", "white"), ("b", "black"))}
_TURN = _C["turn_linkage"]
FAILURE_MAPPING = {cls: entry["error"] for cls, entry in _C["failure_mapping"].items()}

_OTHER = {"w": "b", "b": "w"}
_STATE_KEYS = frozenset({"ep_target", "occupied", "side_to_move"})
_MOVE_KEYS = frozenset({"type", "from", "to"})
_MOVE_TYPES = frozenset({"pawn-advance", "ep-capture", "quiet"})
_SQUARES = frozenset(f + r for f in "abcdefgh" for r in "12345678")
_PIECES = frozenset(s + p for s in "wb" for p in "pnbrqk")


class EnPassantError(Exception):
    """A typed en-passant failure: `failure_class` from the contract's
    closed classes and `code` from its failure mapping."""

    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(failure_class):
    raise EnPassantError(failure_class)


def _exact_dict(value, keys):
    """Exact dict whose keys are exact str and exactly KEYS - key types are
    checked before any set build or comparison, so a str-subclass or
    hash-colliding key fails closed without running its methods."""
    return (
        type(value) is dict
        and all(type(k) is str for k in dict.keys(value))
        and set(dict.keys(value)) == keys
    )


def _read_state(state):
    """Validate and detach the state: returns (ep_target, occupied copy,
    side_to_move) built from exact values only."""
    if not _exact_dict(state, _STATE_KEYS):
        _fail("target_malformed")
    target = dict.__getitem__(state, "ep_target")
    occupied = dict.__getitem__(state, "occupied")
    side = dict.__getitem__(state, "side_to_move")
    if type(target) is not str or type(side) is not str or side not in _OTHER:
        _fail("target_malformed")
    if type(occupied) is not dict:
        _fail("target_malformed")
    occ = {}
    for square, token in dict.items(occupied):
        if (
            type(square) is not str
            or square not in _SQUARES
            or type(token) is not str
            or token not in _PIECES
        ):
            _fail("target_malformed")
        occ[square] = token
    for colour in _OTHER:
        if sum(1 for token in occ.values() if token == colour + "k") != 1:
            _fail("target_malformed")
    return target, occ, side


def _read_move(move):
    if not _exact_dict(move, _MOVE_KEYS):
        _fail("target_malformed")
    kind = dict.__getitem__(move, "type")
    frm = dict.__getitem__(move, "from")
    to = dict.__getitem__(move, "to")
    if type(kind) is not str or kind not in _MOVE_TYPES:
        _fail("target_malformed")
    if type(frm) is not str or frm not in _SQUARES or type(to) is not str or to not in _SQUARES:
        _fail("target_malformed")
    return kind, frm, to


def _grammar_ok(target):
    if target == NONE:
        return True
    return len(target) == 2 and target[0] in _FILES and target[1] in _RANKS


def _clear(occ, a, b, step_f, step_r):
    f, r = ord(a[0]) - 97 + step_f, int(a[1]) + step_r
    end = (ord(b[0]) - 97, int(b[1]))
    while (f, r) != end:
        if chr(97 + f) + str(r) in occ:
            return False
        f += step_f
        r += step_r
    return True


def _attacked(occ, square, by_side):
    f0, r0 = ord(square[0]) - 97, int(square[1])
    for sq, token in occ.items():
        if token[0] != by_side:
            continue
        df, dr = ord(sq[0]) - 97 - f0, int(sq[1]) - r0
        piece = token[1]
        if piece == "p":
            if dr == (-1 if by_side == "w" else 1) and abs(df) == 1:
                return True
        elif piece == "n":
            if (abs(df), abs(dr)) in ((1, 2), (2, 1)):
                return True
        elif piece == "k":
            if max(abs(df), abs(dr)) == 1:
                return True
        elif (
            ((piece in "rq" and (df == 0 or dr == 0)) or (piece in "bq" and abs(df) == abs(dr)))
            and (df, dr) != (0, 0)
            and _clear(occ, square, sq, (df > 0) - (df < 0), (dr > 0) - (dr < 0))
        ):
            return True
    return False


def _capture(target, occ, side, frm, to):
    if not _grammar_ok(target):
        _fail("target_malformed")
    if target == NONE:
        _fail("capture_precondition")  # no target: stale or never set
    if target in occ:
        # the target is the square the pawn passed over, so it is empty in
        # every consistent position (fen.yaml target_square: empty)
        _fail("target_inconsistent")
    # the target is available only to the advancing side's opponent
    if target[1] != _SET_ON[_OTHER[side]]["target_rank"]:
        _fail("target_inconsistent")
    mover = _MOVER[side]
    captured = target[0] + mover["captured_rank"]
    if occ.get(captured) != _OTHER[side] + "p":
        _fail("target_inconsistent")
    if (
        to != target
        or frm[1] != mover["mover_rank"]
        or abs(ord(frm[0]) - ord(target[0])) != 1
        or occ.get(frm) != side + "p"
    ):
        _fail("capture_precondition")
    result = {sq: token for sq, token in occ.items() if sq not in (frm, captured)}
    result[to] = side + "p"
    king = next(sq for sq, token in result.items() if token == side + "k")
    if _attacked(result, king, _OTHER[side]):
        _fail("pinned_capture")  # judged on the RESULTING position
    return {"ep_target": NONE, "occupied": result, "side_to_move": _OTHER[side]}


def apply(state, move):
    """Apply one move; returns a new state or raises EnPassantError."""
    target, occ, side = _read_state(state)
    kind, frm, to = _read_move(move)
    if kind == "ep-capture":
        return _capture(target, occ, side, frm, to)
    token = occ.get(frm)
    if token is None:
        _fail("target_malformed")  # no piece to move: not a closed-domain move
    result = {sq: t for sq, t in occ.items() if sq != frm}
    result[to] = token
    new_target = NONE  # lifetime: every move clears the target
    set_on = _SET_ON[side]
    if (
        kind == "pawn-advance"
        and token[1] == "p"
        and frm[0] == to[0]
        and frm[1] == set_on["from_rank"]
        and to[1] == set_on["to_rank"]
    ):
        new_target = to[0] + set_on["target_rank"]
    return {"ep_target": new_target, "occupied": result, "side_to_move": _OTHER[side]}


def identity_value(state):
    """The canonical en_passant identity value: the stored target when at
    least one legal en-passant capture exists against it, else the none
    sentinel (storage vs identity)."""
    target, occ, side = _read_state(state)
    if target == NONE or not _grammar_ok(target):
        return NONE
    file_index = ord(target[0]) - 97
    for df in (-1, 1):
        f = file_index + df
        if 0 <= f <= 7:
            frm = chr(97 + f) + _MOVER[side]["mover_rank"]
            try:
                _capture(target, occ, side, frm, target)
            except EnPassantError:
                continue
            return target
    return NONE


def turn_transition(move_type, side):
    """The turn-machine transition for one move (turn linkage): a pawn move
    or capture resets the halfmove clock; the fullmove number increments
    after black's move."""
    if (
        type(move_type) is not str
        or move_type not in _MOVE_TYPES
        or type(side) is not str
        or side not in _OTHER
    ):
        _fail("target_malformed")
    pawnish = move_type in ("pawn-advance", "ep-capture")
    return {
        "halfmove_clock": "reset"
        if pawnish and _TURN["halfmove_clock"] == "reset"
        else "increment",
        "fullmove_number": "increment"
        if (_TURN["fullmove_number"] == "increment-when-black" and side == "b")
        else "same",
    }
