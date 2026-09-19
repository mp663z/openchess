"""T0053: turn runtime - the reference implementation of the T0050
chess turn contract (offline tooling per ADR-0001; the product runtime
target is the Rust core, which this module mirrors and the T0051
fixture pins).

API:
- apply_move(state, move, terminated=None) -> new state dict (exactly
  the three declared fields); NEVER mutates its input. Validation and
  transition semantics are derived from the linted contract document:
  side flip, fullmove +1 exactly when black moves, halfmove reset on
  pawn_move/capture else +1, null move rejected (illegal_transition),
  terminated states closed (illegal_transition), unknown termination
  states rejected (unknown_termination).
- identity(state) -> the identity projection: side_to_move only
  (counters never participate). Validates first.
- termination_for(state) -> None | {"state": str, "automatic": bool}:
  the fifty-move claim (threshold 100, non-automatic) and
  seventyfive-move automatic (threshold 150) statuses from the
  contract's structured fields. Validates first.
- TurnError: .code (closed error enum), .failure_class (declared turn
  failure class or None), .retryable (exact bool), nonempty str
  message. The constructor enforces the contract's exact error shape -
  it cannot be built out of shape.

Provenance: the contract document is LINTED at import time before any
runtime state is derived from it; a malformed contract fails the
import closed instead of silently reshaping runtime behavior.
"""

from __future__ import annotations

import yaml

from tools.turn_contract_lint import (
    CONTRACT,
    FAILURE_CLASSES,
)
from tools.turn_contract_lint import (
    lint as _lint,
)

_DOC = yaml.safe_load(CONTRACT.read_text())
_lint(_DOC)  # provenance gate: malformed contract -> import fails closed
_C = _DOC["contract"]
_STATE_FIELDS = list(_C["state"]["fields"])
_SIDE_VALUES = list(_C["state"]["side_values"])
_BOUNDS = dict(_C["state"]["bounds"])
_ON_MOVE = dict(_C["transition"]["on_move"])
_RESET_WHEN = list(_ON_MOVE["halfmove_clock"]["reset_when"])
_RESET_TO = _ON_MOVE["halfmove_clock"]["reset_to"]
_FMW = dict(_ON_MOVE["fullmove_number"])
_HM_OTHERWISE = dict(_ON_MOVE["halfmove_clock"]["otherwise"])
_TERMINATION_STATES = list(_C["termination"]["states"])
_FIFTY = dict(_C["termination"]["fifty_move"])
_IDENTITY = dict(_C["transition"]["identity"])
_FAILURE_MAPPING = dict(_C["failure_mapping"])
_NULL_POLICY = dict(_C["transition"]["null_move"])
_AFTER_TERM = dict(_C["termination"]["on_transition_after_termination"])
_UNKNOWN_STATE = dict(_C["termination"]["unknown_state"])
_ERROR_ENUM = set(_C["errors"]["closed_enum"])
_MOVE_KINDS = set(_RESET_WHEN) | {"quiet"}


class TurnError(Exception):
    def __init__(
        self,
        code: object,
        message: object,
        failure_class: object = None,
        retryable: object = False,
    ) -> None:
        if type(code) is not str or code not in _ERROR_ENUM:
            raise ValueError(f"undeclared error code {code!r}")
        if type(message) is not str or not message.strip():
            raise ValueError("error message must be a nonempty string")
        if failure_class is not None and (
            type(failure_class) is not str or failure_class not in FAILURE_CLASSES
        ):
            raise ValueError(f"undeclared failure class {failure_class!r}")
        if type(retryable) is not bool:
            raise ValueError("retryable must be an exact bool")
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class
        self.retryable = retryable


def _fail(failure_class: str, detail: str) -> None:
    raise TurnError(
        code=_FAILURE_MAPPING[failure_class]["error"],
        message=detail,
        failure_class=failure_class,
    )


def _is_int(v: object) -> bool:
    return type(v) is int  # bool is NOT int here: 0/False conflation is a bypass


def _validate_state(state: object) -> None:
    if type(state) is not dict:
        _fail("bad_counter", "turn state must be a mapping")
    side = state.get("side_to_move")
    if type(side) is not str or side not in _SIDE_VALUES:
        _fail("no_side_to_move", f"side_to_move must be one of {_SIDE_VALUES}")
    for f, bound in _BOUNDS.items():
        v = state.get(f)
        if not _is_int(v) or v < bound["min"]:
            _fail("bad_counter", f"{f} must be an integer >= {bound['min']}")
    if set(state) != set(_STATE_FIELDS):
        _fail("bad_counter", f"turn state is exactly {_STATE_FIELDS}")


def apply_move(state: object, move: object, terminated: object = None) -> dict:
    """Validate the state, then apply exactly one transition per the
    contract's structured fields. Returns a NEW dict; the input is
    never mutated, so a rejected transition is a true rollback."""
    _validate_state(state)
    if terminated is not None:
        if type(terminated) is not str or terminated not in _TERMINATION_STATES:
            _fail(_UNKNOWN_STATE["failure"], f"undeclared termination state {terminated!r}")
        _fail(_AFTER_TERM["failure"], f"state terminated ({terminated}): machine closed")
    if type(move) is not str:
        _fail(_NULL_POLICY["failure"],
              f"move must be a string kind, got {type(move).__name__}")
    if move == "null":
        _fail(_NULL_POLICY["failure"], "null move rejected per contract policy")
    if move not in _MOVE_KINDS:
        _fail(_NULL_POLICY["failure"], f"undeclared move kind {move!r}")
    out = dict(state)
    mover = state["side_to_move"]
    out["side_to_move"] = next(s for s in _SIDE_VALUES if s != mover)  # flip
    if (_FMW["when"] == "black") == (mover == "b"):
        out["fullmove_number"] = state["fullmove_number"] + _FMW["amount"]
    if move in _RESET_WHEN:
        out["halfmove_clock"] = _RESET_TO
    else:
        out["halfmove_clock"] = state["halfmove_clock"] + _HM_OTHERWISE["amount"]
    return out


def identity(state: object) -> dict:
    """The identity projection per the contract's participation
    booleans (side_to_move always; counters never). Validates first."""
    _validate_state(state)
    out = {}
    if _IDENTITY["side_to_move_participates"]:
        out["side_to_move"] = state["side_to_move"]
    if _IDENTITY["counters_participate"]:
        out["halfmove_clock"] = state["halfmove_clock"]
        out["fullmove_number"] = state["fullmove_number"]
    return out


def termination_for(state: object) -> dict | None:
    """The fifty/seventyfive-move status of a validated state, from the
    contract's structured thresholds; None when no threshold is met."""
    _validate_state(state)
    claim = _FIFTY["claim"]
    auto = _FIFTY["automatic"]
    hm = state["halfmove_clock"]
    if hm >= auto["threshold"]:
        return {"state": auto["state"], "automatic": auto["automatic"]}
    if hm >= claim["threshold"]:
        return {"state": claim["state"], "automatic": claim["automatic"]}
    return None
