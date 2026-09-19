"""T0066: castling instrumentation - the CastlingTracer diagnostic layer.

Mirrors the T0057 turn-instrument contract for the castling domain.
Proves the instrumented runtime is diagnosable AND behavior-neutral:

- happy: a seeded traced session (castles when legal + loss events)
  matches an untraced run bit-identically; every accept record
  carries snapshotted before/after states and the move; the JSONL
  trace is deterministic and SHA-256-pinned.
- malformed/boundary: reject records carry the exact failure_class
  (declared class OR the pinned structural None), code (normative
  mapping / malformed_request), exact-bool retryable and the message;
  the propagated exception is unchanged vs the bare runtime; the
  opposite-side-turn boundary is exercised.
- rollback: a recorded rejection leaves the caller's state untouched,
  and the next traced call starts from that same state.
- integrity: seq strictly increasing from 0, one record per call,
  records view an immutable structural copy.
- crash: any non-CastlingError BaseException records outcome "crash"
  with the error type; the SAME exception object propagates.
- neutrality/adversarial: raising AND successful side-effecting copy
  hooks in state/move/result are never invoked (non-dispatching
  structural snapshots); wrapped call count, exact argument/result/
  exception identity and caller-state bit-identity hold; records
  reads are side-effect-free.
"""

from __future__ import annotations

import copy
import hashlib
import random
from pathlib import Path

import pytest
import yaml

from tools import castling_runtime as rt
from tools.castling_instrument import CastlingTracer

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "castling.yaml").read_text())
C = DOC["contract"]
VALUES = list(C["rights"]["values"])
GRAMMAR = dict(C["rights"]["grammar"])
NONE = GRAMMAR["none_sentinel"]
ORDERING = GRAMMAR["ordering"]
HOME = {k: dict(v) for k, v in C["rights"]["home_squares"].items()}
LOSS_KING = {"w": list(C["loss"]["on_king_move"]),
             "b": list(C["loss"]["on_king_move_black"])}
LOSS_ROOK_FROM = dict(C["loss"]["on_rook_move_from"])
LOSS_CAPTURE_ON = dict(C["loss"]["on_rook_capture_on"])
PATHS = {k: dict(v) for k, v in C["move"]["per_side_paths"].items()}
FAILURE_MAPPING = dict(C["failure_mapping"])

SEED = 20260919
SESSION_EVENTS = 150
EXPECTED_JSONL_SHA256: str | None = (
    "1b7ce7de4809738d8be9c4d7359202c643ac2bfe6ad7315210846307bf89554c")


def _side_of(right: str) -> str:
    return "w" if right.isupper() else "b"


def _canonical(held: set) -> str:
    if not held:
        return NONE
    return "".join(c for c in ORDERING if c in held)


def _home_pieces(right: str) -> dict:
    side = _side_of(right)
    return {HOME[right]["king"]: side + "k", HOME[right]["rook"]: side + "r"}


def _start_state() -> dict:
    occ = {}
    for r in VALUES:
        occ.update(_home_pieces(r))
    return {"rights": _canonical(set(VALUES)), "occupied": occ,
            "attacked": [], "side_to_move": "w"}


def _castle_legal(state: dict, right: str) -> bool:
    if state["side_to_move"] != _side_of(right):
        return False
    if right not in (set() if state["rights"] == NONE
                     else set(state["rights"])):
        return False
    path, home = PATHS[right], HOME[right]
    if any(sq in state["occupied"] for sq in path["empty_required"]):
        return False
    att = set(state["attacked"])
    return not ({home["king"], path["king_to"], *path["king_transit"]} & att)


def _event_stream(rng: random.Random, n: int) -> list:
    state = _start_state()
    moves = []
    for _ in range(n):
        legal = [r for r in VALUES if _castle_legal(state, r)]
        if legal and rng.random() < 0.6:
            move = {"type": "castle",
                    "right": legal[rng.randrange(len(legal))]}
        else:
            kind = rng.choice(["king_move", "rook_move_from",
                               "rook_capture_on"])
            if kind == "king_move":
                move = {"type": "king_move", "side": rng.choice(["w", "b"])}
            elif kind == "rook_move_from":
                move = {"type": "rook_move_from",
                        "square": rng.choice(sorted(LOSS_ROOK_FROM))}
            else:
                move = {"type": "rook_capture_on",
                        "square": rng.choice(sorted(LOSS_CAPTURE_ON))}
        state = rt.apply(state, move)
        if move["type"] == "castle":
            state = dict(state)
            state["side_to_move"] = ("b" if state["side_to_move"] == "w"
                                     else "w")
        moves.append(move)
    return moves


def _run_session(apply_fn, seed: int, n: int):
    rng = random.Random(seed)
    moves = _event_stream(rng, n)
    state = _start_state()
    for move in moves:
        state = apply_fn(state, move)
        if move["type"] == "castle":
            state = dict(state)
            state["side_to_move"] = ("b" if state["side_to_move"] == "w"
                                     else "w")
    return state, moves


def test_happy_traced_matches_untraced_bit_identically():
    tracer = CastlingTracer()
    traced_final, moves = _run_session(tracer.apply, SEED, SESSION_EVENTS)
    untraced_final, _ = _run_session(rt.apply, SEED, SESSION_EVENTS)
    assert traced_final == untraced_final
    recs = tracer.records
    assert len(recs) == SESSION_EVENTS
    state = _start_state()
    for rec, move in zip(recs, moves, strict=True):
        assert rec["outcome"] == "accept"
        assert rec["state_before"] == state
        assert rec["move"] == move
        state = rt.apply(state, move)
        assert rec["state_after"] == state
        if move["type"] == "castle":
            state = dict(state)
            state["side_to_move"] = ("b" if state["side_to_move"] == "w"
                                     else "w")
    assert state == untraced_final


def test_jsonl_deterministic_and_pinned():
    a = CastlingTracer()
    _run_session(a.apply, SEED, SESSION_EVENTS)
    b = CastlingTracer()
    _run_session(b.apply, SEED, SESSION_EVENTS)
    assert a.to_jsonl() == b.to_jsonl(), "trace not deterministic"
    digest = hashlib.sha256(a.to_jsonl().encode()).hexdigest()
    if EXPECTED_JSONL_SHA256 is not None:
        assert digest == EXPECTED_JSONL_SHA256, (
            "trace changed - runtime or tracer edit; update the pin"
            " only with a reviewed change")


def _castling_state(right: str) -> dict:
    return {"rights": _canonical({right}), "occupied": _home_pieces(right),
            "attacked": [], "side_to_move": _side_of(right)}


def _reject_cases():
    st = _castling_state("K")
    blocked = _castling_state("K")
    blocked["occupied"]["f1"] = "wp"
    checked = _castling_state("K")
    checked["attacked"] = ["e1"]
    unheld = _castling_state("K")
    unheld["rights"] = _canonical({"Q"})
    inconsistent = _castling_state("K")
    del inconsistent["occupied"]["h1"]
    grammar = _castling_state("K")
    grammar["rights"] = "QK"
    other_turn = _castling_state("K")
    other_turn["side_to_move"] = "b"
    return [
        ("grammar", grammar, {"type": "castle", "right": "K"},
         "rights_malformed"),
        ("unheld", unheld, {"type": "castle", "right": "K"},
         "king_unmoved_required"),
        ("inconsistent", inconsistent, {"type": "castle", "right": "K"},
         "rights_inconsistent"),
        ("blocked", blocked, {"type": "castle", "right": "K"},
         "path_blocked"),
        ("through_check", checked, {"type": "castle", "right": "K"},
         "through_check"),
        # boundary: castle on the other side's turn -> structural None
        ("other_turn", other_turn, {"type": "castle", "right": "K"}, None),
        ("bad_move_shape", st, {"type": "castle"}, None),
    ]


def test_malformed_and_boundary_records_exact():
    tracer = CastlingTracer()
    for name, state, move, cls in _reject_cases():
        before = copy.deepcopy(state)
        with pytest.raises(rt.CastlingError) as ei:
            tracer.apply(state, move)
        err = ei.value
        rec = tracer.records[-1]
        assert rec["outcome"] == "reject", name
        assert rec["failure_class"] == err.failure_class == cls, name
        expect_code = (FAILURE_MAPPING[cls]["error"] if cls is not None
                       else "malformed_request")
        assert rec["code"] == err.code == expect_code, name
        assert rec["retryable"] is err.retryable, name
        assert rec["message"] == err.args[0] and err.args[0].strip(), name
        assert rec["state_before"] == before, name
        with pytest.raises(rt.CastlingError) as ei2:
            rt.apply(copy.deepcopy(state), move)
        bare = ei2.value
        assert (type(err), err.args, err.code, err.failure_class,
                err.retryable) == (type(bare), bare.args, bare.code,
                                   bare.failure_class, bare.retryable), name
        assert state == before, f"{name}: rejected input mutated"


def test_rollback_then_continue_same_state():
    tracer = CastlingTracer()
    state = _castling_state("K")
    state["attacked"] = ["f1"]
    with pytest.raises(rt.CastlingError):
        tracer.apply(state, {"type": "castle", "right": "K"})
    assert state["attacked"] == ["f1"], "rejected call touched the state"
    out = tracer.apply(state, {"type": "king_move", "side": "w"})
    clean = _castling_state("K")
    clean["attacked"] = ["f1"]
    assert out == rt.apply(clean, {"type": "king_move", "side": "w"})
    recs = tracer.records
    assert [r["outcome"] for r in recs] == ["reject", "accept"]
    assert recs[1]["state_before"] == clean


def test_trace_integrity_append_only():
    tracer = CastlingTracer()
    _run_session(tracer.apply, SEED + 1, 20)
    with pytest.raises(rt.CastlingError):
        tracer.apply(_castling_state("K"), {"type": "castle"})
    recs = tracer.records
    assert [r["seq"] for r in recs] == list(range(21))
    recs[0]["outcome"] = "tampered"
    recs[0]["state_before"]["rights"] = "X"
    fresh = tracer.records
    assert fresh[0]["outcome"] == "accept"
    assert fresh[0]["state_before"] == _start_state()


def test_crash_recorded_and_same_exception_propagates():
    sentinel = ValueError("boom")

    def bad_apply(state, move):
        raise sentinel

    tracer = CastlingTracer(bad_apply)
    with pytest.raises(ValueError) as ei:
        tracer.apply(_castling_state("K"), {"type": "castle", "right": "K"})
    assert ei.value is sentinel, "tracer wrapped or replaced the exception"
    rec = tracer.records[-1]
    assert rec["outcome"] == "crash"
    assert rec["error_type"] == "ValueError"
    assert rec["state_before"] == _castling_state("K")


def test_neutrality_accept_returns_exact_runtime_object():
    sentinel_state = {"rights": NONE, "occupied": {}, "attacked": [],
                      "side_to_move": "b"}

    def spy_apply(state, move):
        return sentinel_state

    tracer = CastlingTracer(spy_apply)
    out = tracer.apply(_castling_state("K"), {"type": "castle", "right": "K"})
    assert out is sentinel_state, "tracer copied or rebuilt the result"
    assert tracer.records[-1]["state_after"] == sentinel_state
    sentinel_state["rights"] = "K"
    assert tracer.records[-1]["state_after"]["rights"] == NONE


class _DeepcopyBomb:
    def __deepcopy__(self, memo):
        raise RuntimeError("copy hook bomb")


class _SideEffectValue:
    """A value whose __deepcopy__ SUCCEEDS while mutating caller
    state: the neutrality break no exception handler can stop. The
    non-dispatching copier must never invoke it."""
    def __init__(self, target):
        self.target = target
    def __deepcopy__(self, memo):
        self.target["rights"] = "TAMPERED"
        return self


def test_snapshot_bomb_in_inputs_and_result_stays_neutral():
    sentinel_out = {"rights": NONE, "occupied": {}, "attacked": [],
                    "side_to_move": "w"}
    for bomb_slot in ("state", "move"):
        caller_state = _castling_state("K")
        calls = []

        def spy(st, mv, _calls=calls, _out=sentinel_out):
            _calls.append((st, mv))
            return _out

        tracer = CastlingTracer(spy)
        args = {"state": caller_state,
                "move": {"type": "castle", "right": "K"}}
        args[bomb_slot] = _DeepcopyBomb()
        out = tracer.apply(args["state"], args["move"])
        assert out is sentinel_out, bomb_slot
        assert len(calls) == 1, bomb_slot
        assert calls[0][0] is args["state"] and calls[0][1] is args["move"]
        assert caller_state == _castling_state("K"), bomb_slot
        recs = tracer.records
        assert len(recs) == 1, bomb_slot
        key = "state_before" if bomb_slot == "state" else "move"
        assert recs[0][key] == {"__opaque__": "_DeepcopyBomb"}, bomb_slot
    # result bomb
    class BombDict(dict):
        def __deepcopy__(self, memo):
            raise RuntimeError("result bomb")

    bomb_out = BombDict(_castling_state("K"))

    def spy2(st, mv):
        return bomb_out

    tracer2 = CastlingTracer(spy2)
    out2 = tracer2.apply(_castling_state("K"), {"type": "castle", "right": "K"})
    assert out2 is bomb_out
    assert len(tracer2.records) == 1
    assert tracer2.records[0]["state_after"] == {"__opaque__": "BombDict"}


def test_successful_side_effect_hook_never_invoked():
    for bomb_slot in ("state", "move"):
        caller_state = _castling_state("K")
        seen = []

        def spy(st, mv, _seen=seen):
            _seen.append((st, mv))
            return {"rights": NONE, "occupied": {}, "attacked": [],
                    "side_to_move": "w"}

        tracer = CastlingTracer(spy)
        bomb = _SideEffectValue(caller_state)
        args = {"state": caller_state,
                "move": {"type": "castle", "right": "K"}}
        args[bomb_slot] = bomb
        tracer.apply(args["state"], args["move"])
        assert caller_state == _castling_state("K"), (
            f"{bomb_slot}: caller state mutated by instrumentation")
        assert seen[0][0] is args["state"], bomb_slot
        assert seen[0][1] is args["move"], bomb_slot
        rec = tracer.records[0]
        key = "state_before" if bomb_slot == "state" else "move"
        assert rec[key] == {"__opaque__": "_SideEffectValue"}, bomb_slot
        _ = tracer.records
        tracer.to_jsonl()
        assert caller_state == _castling_state("K"), bomb_slot


def test_records_read_never_invokes_user_hooks():
    caller_state = _castling_state("K")
    bomb = _SideEffectValue(caller_state)

    def spy(st, mv):
        return {"rights": NONE, "occupied": {}, "attacked": [],
                "side_to_move": "w", "extra": bomb}

    tracer = CastlingTracer(spy)
    tracer.apply(caller_state, {"type": "castle", "right": "K"})
    assert caller_state == _castling_state("K")
    r1 = tracer.records
    r2 = tracer.records
    assert r1 == r2
    assert r1[0]["state_after"]["extra"] == {"__opaque__": "_SideEffectValue"}
    tracer.to_jsonl()
    assert caller_state == _castling_state("K"), "records read ran a user hook"


def test_baseexception_family_recorded_with_exact_identity():
    for exc in (KeyboardInterrupt(), SystemExit(1), GeneratorExit()):
        calls = []

        def spy(st, mv, _exc=exc, _calls=calls):
            _calls.append(1)
            raise _exc

        tracer = CastlingTracer(spy)
        with pytest.raises(type(exc)) as ei:
            tracer.apply(_castling_state("K"), {"type": "castle", "right": "K"})
        assert ei.value is exc
        assert len(calls) == 1
        recs = tracer.records
        assert len(recs) == 1
        assert recs[0]["outcome"] == "crash"
        assert recs[0]["error_type"] == type(exc).__name__


def test_record_field_extraction_failure_degrades_not_replaces():
    class ExplodingError(rt.CastlingError):
        @property
        def failure_class(self):
            raise RuntimeError("field bomb")

        @failure_class.setter
        def failure_class(self, value):
            pass

    err = ExplodingError("illegal_move", "boom", "path_blocked", False)

    def rejecting(st, mv):
        raise err

    tracer = CastlingTracer(rejecting)
    with pytest.raises(ExplodingError) as ei:
        tracer.apply(_castling_state("K"), {"type": "castle", "right": "K"})
    assert ei.value is err, "tracer replaced the exception"
    assert len(tracer.records) == 1
    assert tracer.records[0]["outcome"] == "record_degraded"
    assert tracer.records[0]["record_error"] == "RuntimeError"
