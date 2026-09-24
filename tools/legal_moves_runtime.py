"""T0080: legal-moves runtime - the reference implementation of the
T0077 chess legal-moves contract (offline tooling per ADR-0001; the
product runtime target is the Rust core, which this module mirrors and
the T0078 conformance fixture pins). Implemented SEPARATELY from the
T0078 fixture interpreter: all semantics derive from the linted
contract document, never copied from the fixture's own reference code.

API:
- legal_moves(state) -> list of move dicts, each EXACTLY the
  move_model shape (from_square, to_square, optional promotion, no
  other members): pseudo-legal movement per the contract movement
  section, promotion-rank pawn moves expanded to exactly one move per
  declared promotion value (quiet AND capture; no unpromoted move to
  the promotion rank exists), filtered by the king-safety legality
  rule. Castling and en-passant moves are NOT produced here: the
  linkage section assigns them to their own contracts, and this
  runtime's state carries no rights or ep target.
- is_attacked(state, square, attacker) -> exact bool: the contract
  attack relation (occupancy-irrelevant, attacker king safety
  ignored, pawns attack exactly their diagonal forward squares,
  kings attack adjacent squares).
- terminal_status(state) -> the move-set terminal classification:
  "checkmate" (check + zero legal moves), "stalemate" (no check +
  zero legal moves), "check" (king attacked, moves exist), else
  "none" (a game with at least one legal move is never
  move-set-terminal). Every non-move-set outcome is owned by the
  chess-turn contract, never reported here.
- apply(state, move) -> NEW state dict; NEVER mutates its input, so
  a rejected move is a true rollback. Validation order is pinned:
  shape -> no_piece -> not_players_piece -> unreachable_target ->
  promotion rules -> leaves_king_attacked. Turn advancement
  (side_to_move flip, clocks) is owned by the chess-turn contract:
  apply performs the position transition only.
- LegalMovesError: .failure_class (declared class or None), .code
  (closed error enum, normative class-to-code mapping), .retryable
  (exact bool), nonempty str message. The constructor enforces the
  exact error shape - a mismatched class/code pair cannot be built.

PUBLIC API CHOICES PINNED (the contract is silent here):
- structural defects the declared failure classes do not name
  (non-mapping state or move containers, missing/extra state fields,
  wrong field types, bad piece tokens, wrong king counts, bad square
  or attacker arguments to is_attacked) are malformed_request with
  failure_class None - never a raw KeyError/TypeError/StopIteration.
- a non-mapping MOVE fails the move_model shape schema, so it is the
  declared malformed_move class, not the structural choice.
- wrong_variant is unreachable within this runtime: variant identity
  is owned by the chess-variant contract and every state here is
  standard chess (same scoping as the T0078 fixture).

Provenance: the contract document is LINTED at import time before any
runtime table is derived from it; a malformed contract fails the
import closed instead of silently reshaping runtime behavior.
"""

from __future__ import annotations

import yaml

from tools.legal_moves_contract_lint import (
    CONTRACT,
    ERROR_ENUM,
    FAILURE_CLASSES,
    FAILURE_MAPPING,
)
from tools.legal_moves_contract_lint import (
    lint as _lint,
)

_DOC = yaml.safe_load(CONTRACT.read_text())
_lint(_DOC)  # provenance gate: malformed contract -> import fails closed
_C = _DOC["contract"]

_SHAPE = dict(_C["move_model"]["shape"])
_REQUIRED = list(_SHAPE["required"])
_OPTIONAL = list(_SHAPE["optional"])
_CLOSED_KEYS = _SHAPE["closed_keys"] is True
_FROM_TO_DISTINCT = _SHAPE["from_to_distinct"] is True
_PROMO_ENUM = list(_SHAPE["types"]["promotion"]["enum"])
_GRAMMAR = dict(_C["move_model"]["square_grammar"])
_FILES = list(_GRAMMAR["files"])
_RANKS = list(_GRAMMAR["ranks"])
_EXPANSION = dict(_C["move_model"]["promotion_expansion"])
_PROMO_VALUES = list(_EXPANSION["values"])
_MOVEMENT = dict(_C["movement"])
_OCCUPANCY = dict(_MOVEMENT["occupancy"])
_ENEMY_KING_NEVER_TARGET = (
    _OCCUPANCY["enemy_king_square"]
    == "never-a-capture-target-check-and-checkmate-terminate-first")
_PAWN = dict(_MOVEMENT["pawn"])
_KNIGHT_DELTAS = [tuple(d) for d in _MOVEMENT["knight"]["deltas"]]
_KING_DELTAS = [tuple(d) for d in _MOVEMENT["king"]["deltas"]]
_ROOK_DIRS = [tuple(d) for d in _MOVEMENT["rook"]["directions"]]
_BISHOP_DIRS = [tuple(d) for d in _MOVEMENT["bishop"]["directions"]]
_QUEEN_DIRS = [tuple(d) for d in _MOVEMENT["queen"]["directions"]]
_SIDE_NAME = {"w": "white", "b": "black"}
_OTHER = {"w": "b", "b": "w"}
_FORWARD = {
    s: (_PAWN["forward"][_SIDE_NAME[s]]["file_delta"],
        _PAWN["forward"][_SIDE_NAME[s]]["rank_delta"]) for s in "wb"}
_CAPTURE_DELTAS = {
    s: [tuple(d) for d in _PAWN["capture_deltas"][_SIDE_NAME[s]]]
    for s in "wb"}
_DOUBLE_RANKS = dict(_PAWN["double"]["ranks"])
_PROMO_RANKS = dict(_PAWN["promotion_ranks"])
_STATE_KEYS = {"occupied", "side_to_move"}
_SIDES = ("w", "b")
_PIECE_LETTERS = "pnbrqk"


class LegalMovesError(Exception):
    def __init__(
        self,
        code: object,
        message: object,
        failure_class: object = None,
        retryable: object = False,
    ) -> None:
        if type(code) is not str or code not in ERROR_ENUM:
            raise ValueError(f"undeclared error code {code!r}")
        if type(message) is not str or not message.strip():
            raise ValueError("error message must be a nonempty string")
        if failure_class is not None:
            if (type(failure_class) is not str
                    or failure_class not in FAILURE_CLASSES):
                raise ValueError(
                    f"undeclared failure class {failure_class!r}")
            if code != FAILURE_MAPPING[failure_class]["error"]:
                raise ValueError(
                    f"class {failure_class} must emit code"
                    f" {FAILURE_MAPPING[failure_class]['error']},"
                    f" got {code!r}")
        if type(retryable) is not bool:
            raise ValueError("retryable must be an exact bool")
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class
        self.retryable = retryable


def _fail(failure_class: str, detail: str) -> None:
    raise LegalMovesError(
        code=FAILURE_MAPPING[failure_class]["error"],
        message=detail,
        failure_class=failure_class,
    )


def _malformed(detail: str) -> None:
    raise LegalMovesError(code="malformed_request", message=detail)


def _xy(square: str) -> tuple[int, int]:
    return _FILES.index(square[0]), _RANKS.index(square[1])


def _sq(x: int, y: int) -> str:
    return _FILES[x] + _RANKS[y]


def _on_board(x: int, y: int) -> bool:
    return 0 <= x < len(_FILES) and 0 <= y < len(_RANKS)


def _grammar_ok(square: object) -> bool:
    """Square-grammar predicate derived ONLY from the contract: exactly
    one declared file char then one declared rank char."""
    return (type(square) is str and len(square) == 2
            and square[0] in _FILES and square[1] in _RANKS)


def _validate_state(state: object) -> None:
    """Structural state validation (the pinned public choice): every
    defect is malformed_request with failure_class None, never a raw
    exception. Exactly the two declared fields; occupied maps contract
    squares to side+piece tokens; exactly one king per side."""
    if type(state) is not dict:
        _malformed(f"state must be exactly {sorted(_STATE_KEYS)}")
    # exact-str keys BEFORE the key-set comparison: a str subclass key
    # must not stand in for a declared field, and a key with a user
    # __eq__/__hash__ must never run during the comparison
    if any(type(key) is not str for key in state) or set(state) != _STATE_KEYS:
        _malformed(f"state must be exactly {sorted(_STATE_KEYS)}")
    occ = state["occupied"]
    if type(occ) is not dict:
        _malformed("occupied must be a mapping")
    for sq, tok in occ.items():
        if not _grammar_ok(sq):
            _malformed(f"occupied key {sq!r} is not a contract square")
        if not (type(tok) is str and len(tok) == 2
                and tok[0] in _SIDES and tok[1] in _PIECE_LETTERS):
            _malformed(f"bad piece token {tok!r}")
    stm = state["side_to_move"]
    if type(stm) is not str or stm not in _SIDES:
        _malformed(f"side_to_move must be one of {list(_SIDES)}")
    for side in _SIDES:
        kings = sum(1 for tok in occ.values() if tok == side + "k")
        if kings != 1:
            _malformed(f"exactly one {side} king required, found {kings}")


def _pseudo_targets(occ: dict, sq: str,
                  _exclude_enemy_king: bool = _ENEMY_KING_NEVER_TARGET
                  ) -> set:
    """Pseudo-legal destinations for the piece on sq, derived from the
    contract movement and occupancy sections (no king-safety filter).
    Own-piece squares unreachable, enemy squares capture-only, a
    slider's ray ends at the first piece (included when enemy). The
    opposing king's square is NEVER a destination (contract occupancy
    enemy_king_square / legality opponent_king_capture): check and
    checkmate terminate the game before any king can be captured. The
    ATTACK relation is unaffected - king squares stay attacked.
    _exclude_enemy_king exists for the behavior suite's mutation
    replay; production callers never pass it."""
    tok = occ[sq]
    side, piece = tok[0], tok[1]
    x, y = _xy(sq)
    targets: set = set()
    leaper = {"n": _KNIGHT_DELTAS, "k": _KING_DELTAS}.get(piece)
    slider = {"r": _ROOK_DIRS, "b": _BISHOP_DIRS,
              "q": _QUEEN_DIRS}.get(piece)
    if leaper is not None:
        for dx, dy in leaper:
            nx, ny = x + dx, y + dy
            if _on_board(nx, ny):
                hit = occ.get(_sq(nx, ny))
                if hit is None or hit[0] != side:
                    targets.add(_sq(nx, ny))
    elif slider is not None:
        for dx, dy in slider:
            nx, ny = x + dx, y + dy
            while _on_board(nx, ny):
                hit = occ.get(_sq(nx, ny))
                if hit is None:
                    targets.add(_sq(nx, ny))
                else:
                    if hit[0] != side:
                        targets.add(_sq(nx, ny))
                    break  # any piece ends the ray before the next square
                nx, ny = nx + dx, ny + dy
    elif piece == "p":
        fx, fy = _FORWARD[side]
        one = (x + fx, y + fy)
        if _on_board(*one) and _sq(*one) not in occ:
            targets.add(_sq(*one))
            two = (x + 2 * fx, y + 2 * fy)
            if (_RANKS[y] == _DOUBLE_RANKS[_SIDE_NAME[side]]
                    and _on_board(*two) and _sq(*two) not in occ):
                targets.add(_sq(*two))
        for dx, dy in _CAPTURE_DELTAS[side]:
            nx, ny = x + dx, y + dy
            if _on_board(nx, ny):
                hit = occ.get(_sq(nx, ny))
                if hit is not None and hit[0] != side:
                    targets.add(_sq(nx, ny))
    if _exclude_enemy_king:
        enemy_king = _OTHER[side] + "k"
        targets = {t for t in targets if occ.get(t) != enemy_king}
    return targets


def _attacked_squares(occ: dict, side: str) -> set:
    """The contract attack relation: pseudo-legal capture targets,
    occupancy-irrelevant (empty, enemy AND own squares all attacked),
    attacker king safety ignored; pawns attack exactly their diagonal
    forward squares; kings attack their adjacent squares."""
    attacked: set = set()
    for sq, tok in occ.items():
        if tok[0] != side:
            continue
        piece = tok[1]
        x, y = _xy(sq)
        leaper = {"n": _KNIGHT_DELTAS, "k": _KING_DELTAS}.get(piece)
        slider = {"r": _ROOK_DIRS, "b": _BISHOP_DIRS,
                  "q": _QUEEN_DIRS}.get(piece)
        if leaper is not None:
            for dx, dy in leaper:
                nx, ny = x + dx, y + dy
                if _on_board(nx, ny):
                    attacked.add(_sq(nx, ny))
        elif slider is not None:
            for dx, dy in slider:
                nx, ny = x + dx, y + dy
                while _on_board(nx, ny):
                    attacked.add(_sq(nx, ny))
                    if _sq(nx, ny) in occ:
                        break  # the first piece's square is attacked
                    nx, ny = nx + dx, ny + dy
        elif piece == "p":
            for dx, dy in _CAPTURE_DELTAS[side]:
                nx, ny = x + dx, y + dy
                if _on_board(nx, ny):
                    attacked.add(_sq(nx, ny))
    return attacked


def _king_square(occ: dict, side: str) -> str:
    return next(sq for sq, tok in occ.items() if tok == side + "k")


def _apply_occupancy(occ: dict, move: dict) -> dict:
    """The position transition on the occupancy mapping: the moving
    piece relocates, a captured piece is removed, a promotion replaces
    the piece letter. Returns a NEW dict."""
    new = dict(occ)
    tok = new.pop(move["from_square"])
    new.pop(move["to_square"], None)  # capture removes the target piece
    if "promotion" in move:
        tok = tok[0] + move["promotion"]
    new[move["to_square"]] = tok
    return new


def _leaves_king_safe(occ: dict, move: dict, side: str) -> bool:
    new = _apply_occupancy(occ, move)
    return _king_square(new, side) not in _attacked_squares(new, _OTHER[side])


def legal_moves(state: object) -> list:
    """The exact legal move set: pseudo-legal moves, promotion-rank
    pawn moves expanded to exactly one move per declared promotion
    value (no unpromoted move to the promotion rank exists), filtered
    to moves whose resulting position leaves the mover's own king
    unattacked. Deterministically ordered by (from, to, promotion)."""
    _validate_state(state)
    occ = state["occupied"]
    side = state["side_to_move"]
    promo_rank = _PROMO_RANKS[_SIDE_NAME[side]]
    moves: list = []
    for sq, tok in sorted(occ.items()):
        if tok[0] != side:
            continue
        for t in sorted(_pseudo_targets(occ, sq)):
            promoting = tok[1] == "p" and t[1] == promo_rank
            for pr in (_PROMO_VALUES if promoting else [None]):
                mv = {"from_square": sq, "to_square": t}
                if pr is not None:
                    mv["promotion"] = pr
                if _leaves_king_safe(occ, mv, side):
                    moves.append(mv)
    return moves


def is_attacked(state: object, square: object, attacker: object) -> bool:
    """The contract attack relation on one square. Structural argument
    defects are the pinned malformed_request/None choice; the verdict
    itself is an exact bool."""
    _validate_state(state)
    if not _grammar_ok(square):
        _malformed(f"square {square!r} is not a contract square")
    if type(attacker) is not str or attacker not in _SIDES:
        _malformed(f"attacker must be one of {list(_SIDES)}")
    return square in _attacked_squares(state["occupied"], attacker)


def terminal_status(state: object) -> str:
    """Move-set terminal classification, derived ONLY from the legal
    move set and the check state per the contract; "none" when the
    side to move has at least one legal move and is not in check."""
    _validate_state(state)
    occ = state["occupied"]
    side = state["side_to_move"]
    check = _king_square(occ, side) in _attacked_squares(occ, _OTHER[side])
    zero = len(legal_moves(state)) == 0
    if check and zero:
        return "checkmate"
    if zero:
        return "stalemate"
    if check:
        return "check"
    return "none"


def _validate_move_shape(move: object) -> None:
    """The move_model shape schema: mapping, closed keys, required
    members present, distinct squares, square grammar, promotion a
    string when present. Any violation is malformed_move."""
    if type(move) is not dict:
        _fail("malformed_move", "move must be a mapping")
    # exact-str member names before any key-set comparison (same reason
    # as the state keys); a non-str name is a closed-keys violation
    if any(type(key) is not str for key in move):
        _fail("malformed_move", "move member names must be exact strings")
    if _CLOSED_KEYS:
        allowed = set(_REQUIRED) | set(_OPTIONAL)
        if not set(move) <= allowed:
            _fail("malformed_move",
                  f"move keys must be within {sorted(allowed)}")
    if not set(_REQUIRED) <= set(move):
        _fail("malformed_move",
              f"move requires {sorted(_REQUIRED)}")
    fr, to = move["from_square"], move["to_square"]
    if not _grammar_ok(fr) or not _grammar_ok(to):
        _fail("malformed_move", "squares must match the square grammar")
    if _FROM_TO_DISTINCT and fr == to:
        _fail("malformed_move", "from_square and to_square must differ")
    if "promotion" in move and type(move["promotion"]) is not str:
        _fail("malformed_move", "promotion must be a string")


def apply(state: object, move: object) -> dict:
    """The validated transition. Pinned validation order: shape ->
    no_piece -> not_players_piece -> unreachable_target -> promotion
    rules -> leaves_king_attacked. Returns a NEW state dict; the input
    is never mutated, so a rejected move is a true rollback. Turn
    advancement is owned by the chess-turn contract, so side_to_move
    is carried through unchanged."""
    _validate_state(state)
    _validate_move_shape(move)
    occ = state["occupied"]
    side = state["side_to_move"]
    fr, to = move["from_square"], move["to_square"]
    if fr not in occ:
        _fail("no_piece", f"from_square {fr} is empty")
    if occ[fr][0] != side:
        _fail("not_players_piece", f"from_square {fr} holds an enemy piece")
    if to not in _pseudo_targets(occ, fr):
        _fail("unreachable_target",
              f"{to} is not pseudo-legal for the piece on {fr}")
    # promotion rules: an out-of-enum string promotion is
    # promotion_forbidden per the contract trigger; promotion is
    # required exactly when a pawn reaches its promotion rank and
    # forbidden on every other move
    if "promotion" in move and move["promotion"] not in _PROMO_ENUM:
        _fail("promotion_forbidden",
              f"promotion {move['promotion']!r} is outside the enum")
    promoting = (occ[fr][1] == "p"
                 and to[1] == _PROMO_RANKS[_SIDE_NAME[side]])
    if promoting and "promotion" not in move:
        _fail("promotion_missing",
              "a pawn reaching the promotion rank requires promotion")
    if not promoting and "promotion" in move:
        _fail("promotion_forbidden",
              "promotion is forbidden on a non-promotion move")
    if not _leaves_king_safe(occ, move, side):
        _fail("leaves_king_attacked",
              "the resulting position leaves the king attacked")
    return {
        "occupied": _apply_occupancy(occ, move),
        "side_to_move": state["side_to_move"],
    }
