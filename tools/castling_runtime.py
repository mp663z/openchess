"""T0062: castling runtime - the reference implementation of the T0059
chess castling contract (offline tooling per ADR-0001; the product
runtime target is the Rust core, which this module mirrors and the
T0060 fixture pins).

API:
- apply(state, move) -> new state dict (exactly the four declared
  fixture state fields: rights, occupied, attacked, side_to_move);
  NEVER mutates its input. A rejected castle is a true rollback.
  Semantics derive from the linted contract: rights grammar (none
  sentinel / subset / duplicate-free / canonical ordering), king and
  rook loss events (exact per-entry mapping), castle preconditions
  (right held -> home squares occupied -> empty_required clear ->
  from/transit/to unattacked) and the exact post-castle relocation
  and rights loss (a castling move is a king move: BOTH of the side's
  rights go).
- identity(state) -> the identity projection: exactly {"rights": ...}
  while rights_participate holds in the contract, else {}.
- turn_effect(state, move) -> {"halfmove_clock": str,
  "fullmove_number": str} per the contract's turn_linkage: exactly one
  transition, halfmove increment never reset, fullmove number
  increments exactly when black castles.
- CastlingError: .code (closed error enum), .failure_class (declared
  castling failure class or None), .retryable (exact bool), nonempty
  str message. The constructor enforces the contract's exact error
  shape - it cannot be built out of shape.

PUBLIC API CHOICES PINNED (the contract is silent here):
- structural defects the contract's five failure classes do not name
  (non-mapping state or move, missing/extra state fields, wrong field
  types, undeclared move type, castle right outside values, castle
  attempted on the other side's turn) are malformed_request with
  failure_class None - never a raw KeyError/TypeError/traceback.
- turn_effect requires a castle move; anything else is
  malformed_request with failure_class None.

Provenance: the contract document is LINTED at import time before any
runtime state is derived from it; a malformed contract fails the
import closed instead of silently reshaping runtime behavior.
"""

from __future__ import annotations

import yaml

from tools.castling_contract_lint import (
    CONTRACT,
    ERROR_ENUM,
    FAILURE_CLASSES,
    FAILURE_MAPPING,
)
from tools.castling_contract_lint import (
    lint as _lint,
)

_DOC = yaml.safe_load(CONTRACT.read_text())
_lint(_DOC)  # provenance gate: malformed contract -> import fails closed
_C = _DOC["contract"]
_VALUES = list(_C["rights"]["values"])
_GRAMMAR = dict(_C["rights"]["grammar"])
_NONE = _GRAMMAR["none_sentinel"]
_ORDERING = _GRAMMAR["ordering"]
_HOME = {k: dict(v) for k, v in _C["rights"]["home_squares"].items()}
_LOSS_KING = {"w": list(_C["loss"]["on_king_move"]),
              "b": list(_C["loss"]["on_king_move_black"])}
_LOSS_ROOK_FROM = dict(_C["loss"]["on_rook_move_from"])
_LOSS_CAPTURE_ON = dict(_C["loss"]["on_rook_capture_on"])
_PATHS = {k: dict(v) for k, v in _C["move"]["per_side_paths"].items()}
_TURN = dict(_C["turn_linkage"])
_RIGHTS_PARTICIPATE = _C["identity"]["rights_participate"]
_SIDES = {"w", "b"}
_STATE_FIELDS = {"rights", "occupied", "attacked", "side_to_move"}
_MOVE_KEY_SETS = {
    "castle": {"type", "right"},
    "king_move": {"type", "side"},
    "rook_move_from": {"type", "square"},
    "rook_capture_on": {"type", "square"},
}
_LOSS_TYPES = {"king_move", "rook_move_from", "rook_capture_on"}

_VALID_SQUARES = set()
for _pair in _HOME.values():
    _VALID_SQUARES.update(_pair.values())
for _p in _PATHS.values():
    _VALID_SQUARES.add(_p["king_from"])
    _VALID_SQUARES.add(_p["king_to"])
    _VALID_SQUARES.add(_p["rook_from"])
    _VALID_SQUARES.add(_p["rook_to"])
    _VALID_SQUARES.update(_p["king_transit"])
    _VALID_SQUARES.update(_p["empty_required"])
_VALID_SQUARES.update(_LOSS_ROOK_FROM)
_VALID_SQUARES.update(_LOSS_CAPTURE_ON)


class CastlingError(Exception):
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
            if type(failure_class) is not str or failure_class not in FAILURE_CLASSES:
                raise ValueError(f"undeclared failure class {failure_class!r}")
            if code != FAILURE_MAPPING[failure_class]["error"]:
                raise ValueError(
                    f"class {failure_class} must emit code"
                    f" {FAILURE_MAPPING[failure_class]['error']}, got {code!r}"
                )
        if type(retryable) is not bool:
            raise ValueError("retryable must be an exact bool")
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class
        self.retryable = retryable


def _fail(failure_class: str, detail: str) -> None:
    raise CastlingError(
        code=FAILURE_MAPPING[failure_class]["error"],
        message=detail,
        failure_class=failure_class,
    )


def _malformed(detail: str) -> None:
    raise CastlingError(code="malformed_request", message=detail)


def _grammar_ok(rights: object) -> bool:
    """Canonical-rights predicate derived ONLY from the contract grammar
    fields."""
    if type(rights) is not str:
        return False
    if rights == _NONE:
        return True
    if rights == "":
        return False  # empty: forbidden
    members = list(rights)
    if any(m not in _VALUES for m in members):
        return False  # membership: subset-of-values
    if len(set(members)) != len(members):
        return False  # duplicates: forbidden
    return [c for c in _ORDERING if c in set(members)] == members


def _canonical(held: set) -> str:
    if not held:
        return _NONE
    return "".join(c for c in _ORDERING if c in held)


def _side_of(right: str) -> str:
    return "w" if right.isupper() else "b"


def _validate_state(state: object) -> None:
    if type(state) is not dict or set(state) != _STATE_FIELDS:
        _malformed(f"state must be exactly {sorted(_STATE_FIELDS)}")
    if type(state["rights"]) is not str:
        _malformed("rights must be a string")
    occ = state["occupied"]
    if type(occ) is not dict:
        _malformed("occupied must be a mapping")
    for sq, tok in occ.items():
        if type(sq) is not str or sq not in _VALID_SQUARES:
            _malformed(f"occupied key {sq!r} is not a contract-named square")
        if not (type(tok) is str and len(tok) == 2
                and tok[0] in "wb" and tok[1] in "kqrbnp"):
            _malformed(f"bad piece token {tok!r}")
    att = state["attacked"]
    if type(att) is not list:
        _malformed("attacked must be a list")
    for sq in att:  # type-check BEFORE hashing: unhashable entries are
        if type(sq) is not str or sq not in _VALID_SQUARES:
            _malformed(f"attacked square {sq!r} is not a contract-named square")
    if len(att) != len(set(att)):
        _malformed("attacked must be duplicate-free")
    stm = state["side_to_move"]
    if type(stm) is not str or stm not in _SIDES:
        _malformed(f"side_to_move must be one of {sorted(_SIDES)}")


def _validate_move(move: object) -> str:
    if type(move) is not dict:
        _malformed("move must be a mapping")
    t = move.get("type")
    if type(t) is not str or t not in _MOVE_KEY_SETS:
        _malformed(f"undeclared move type {t!r}")
    if set(move) != _MOVE_KEY_SETS[t]:
        _malformed(f"{t} move carries exactly {sorted(_MOVE_KEY_SETS[t])}")
    if t == "castle":
        r = move["right"]
        if type(r) is not str or r not in _VALUES:
            _malformed(f"castle right must be one of {_VALUES}")
    elif t == "king_move":
        s = move["side"]
        if type(s) is not str or s not in _SIDES:
            _malformed(f"king_move side must be one of {sorted(_SIDES)}")
    elif t == "rook_move_from":
        sq = move["square"]
        if type(sq) is not str or sq not in _LOSS_ROOK_FROM:
            _malformed(f"rook_move_from square must be one of {sorted(_LOSS_ROOK_FROM)}")
    else:
        sq = move["square"]
        if type(sq) is not str or sq not in _LOSS_CAPTURE_ON:
            _malformed(f"rook_capture_on square must be one of {sorted(_LOSS_CAPTURE_ON)}")
    return t


def apply(state: object, move: object) -> dict:
    """Validate, then apply exactly one castling-domain event per the
    contract. Returns a NEW dict; the input is never mutated, so a
    rejected castle is a true rollback."""
    _validate_state(state)
    t = _validate_move(move)
    rights = state["rights"]
    if not _grammar_ok(rights):
        _fail("rights_malformed", f"rights field {rights!r} is not canonical")
    held = set() if rights == _NONE else set(rights)
    if t in _LOSS_TYPES:
        if t == "king_move":
            loss = _LOSS_KING[move["side"]]
        elif t == "rook_move_from":
            loss = [_LOSS_ROOK_FROM[move["square"]]]
        else:
            loss = [_LOSS_CAPTURE_ON[move["square"]]]
        out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
               for k, v in state.items()}
        out["rights"] = _canonical(held - set(loss))
        return out
    # castle
    right = move["right"]
    side = _side_of(right)
    if side != state["side_to_move"]:
        _malformed(f"castle {right} is not {state['side_to_move']}'s move")
    if right not in held:
        _fail("king_unmoved_required",
              f"right {right} not held: king or rook already moved")
    home, path = _HOME[right], _PATHS[right]
    occ = state["occupied"]
    if occ.get(home["king"]) != side + "k" or occ.get(home["rook"]) != side + "r":
        _fail("rights_inconsistent",
              f"right {right} held but home squares {home} not occupied")
    if any(sq in occ for sq in path["empty_required"]):
        _fail("path_blocked", f"an empty_required square of {right} is occupied")
    att = set(state["attacked"])
    if (home["king"] in att or path["king_to"] in att
            or any(sq in att for sq in path["king_transit"])):
        _fail("through_check", f"king path of {right} is attacked")
    new_occ = {sq: tok for sq, tok in occ.items()
               if sq not in (home["king"], home["rook"])}
    new_occ[path["king_to"]] = side + "k"
    new_occ[path["rook_to"]] = side + "r"
    return {
        "rights": _canonical(held - set(_LOSS_KING[side])),
        "occupied": new_occ,
        "attacked": list(state["attacked"]),
        "side_to_move": state["side_to_move"],
    }


def identity(state: object) -> dict:
    """The identity projection: exactly the contract-declared
    participating fields."""
    _validate_state(state)
    if not _grammar_ok(state["rights"]):
        _fail("rights_malformed", f"rights field {state['rights']!r} is not canonical")
    if _RIGHTS_PARTICIPATE:
        return {"rights": state["rights"]}
    return {}


def turn_effect(state: object, move: object) -> dict:
    """The turn linkage of a castling move, derived from the contract:
    exactly one transition, halfmove increments (never resets), the
    fullmove number increments exactly when black castles."""
    _validate_state(state)
    t = _validate_move(move)
    if t != "castle":
        _malformed("turn_effect is defined for castle moves only")
    if not _grammar_ok(state["rights"]):
        _fail("rights_malformed", f"rights field {state['rights']!r} is not canonical")
    side = _side_of(move["right"])
    return {
        "halfmove_clock": _TURN["halfmove_clock"],
        "fullmove_number": (
            "increment" if (_TURN["fullmove_number"] == "increment-when-black"
                            and side == "b") else "same"),
    }
