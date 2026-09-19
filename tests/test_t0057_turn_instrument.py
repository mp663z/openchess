"""T0057: turn instrumentation - the TurnTracer diagnostic layer.

Proves the instrumented runtime is diagnosable AND behavior-neutral:

- happy: a seeded traced session matches an untraced run
  bit-identically; every accept record carries deep-copied before/
  after states, the move and the terminated flag; the JSONL trace is
  deterministic and SHA-256-pinned.
- malformed/boundary: reject records carry the exact failure_class,
  code (normative mapping), exact-bool retryable and the message; the
  propagated exception is unchanged in type and fields; a
  terminated-state (boundary) call records the terminated flag.
- rollback: a recorded rejection leaves the caller's state untouched,
  and the next traced call starts from that same state.
- integrity: seq is strictly increasing from 0, one record per call,
  the records view is an immutable deep copy.
- crash: a non-TurnError escape records outcome "crash" with the
  error type and the SAME exception object propagates.
- neutrality: accept returns the EXACT object the wrapped runtime
  returned.
"""

from __future__ import annotations

import copy
import hashlib
import random
from pathlib import Path

import pytest
import yaml

from tools import turn_runtime as rt
from tools.turn_instrument import TurnTracer

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "turn.yaml").read_text())
C = DOC["contract"]
SIDE_VALUES = list(C["state"]["side_values"])
ON_MOVE = dict(C["transition"]["on_move"])
RESET_WHEN = list(ON_MOVE["halfmove_clock"]["reset_when"])
TERMINATION_STATES = list(C["termination"]["states"])
FAILURE_MAPPING = dict(C["failure_mapping"])
LEGAL_MOVES = sorted(set(RESET_WHEN) | {"quiet"})

SEED = 20260919
SESSION_MOVES = 200
EXPECTED_JSONL_SHA256: str | None = (
    "fbd48cd9b56be731c67f6a6a699fe3fc35c1e3106e75f8ca6ed7792cf92d4fcf")
EXPECTED_RECORDS = 203  # 200 accepts + 3 recorded rejections


def _state(side="w", half=7, full=12):
    return {"side_to_move": side, "halfmove_clock": half,
            "fullmove_number": full}


def _run_session(apply_fn, seed: int, n: int):
    rng = random.Random(seed)
    state = _state()
    for _ in range(n):
        state = apply_fn(state, rng.choice(LEGAL_MOVES))
    return state


def test_happy_traced_matches_untraced_bit_identically():
    tracer = TurnTracer()
    traced_final = _run_session(tracer.apply, SEED, SESSION_MOVES)
    untraced_final = _run_session(rt.apply_move, SEED, SESSION_MOVES)
    assert traced_final == untraced_final
    recs = tracer.records
    assert len(recs) == SESSION_MOVES
    # replay the same seed through the records: each record's
    # state_before/state_after chains without gaps
    rng = random.Random(SEED)
    state = _state()
    for rec in recs:
        move = rng.choice(LEGAL_MOVES)
        assert rec["outcome"] == "accept"
        assert rec["state_before"] == state
        assert rec["move"] == move
        assert rec["terminated"] is None
        state = rec["state_after"]
    assert state == untraced_final


def test_jsonl_deterministic_and_pinned():
    a = TurnTracer()
    _run_session(a.apply, SEED, SESSION_MOVES)
    b = TurnTracer()
    _run_session(b.apply, SEED, SESSION_MOVES)
    assert a.to_jsonl() == b.to_jsonl(), "trace not deterministic"
    digest = hashlib.sha256(a.to_jsonl().encode()).hexdigest()
    if EXPECTED_JSONL_SHA256 is not None:
        assert digest == EXPECTED_JSONL_SHA256, (
            "trace changed - runtime or tracer edit; update the pin"
            " only with a reviewed change")


def _reject_cases():
    return [
        ("no_side", _state(side="x"), "quiet", None, "no_side_to_move"),
        ("bad_counter", _state(half=True), "quiet", None, "bad_counter"),
        ("bad_move", _state(), "null", None, "illegal_transition"),
        ("terminated", _state(), "quiet", TERMINATION_STATES[0],
         "illegal_transition"),
        ("unknown_term", _state(), "quiet", "weird", "unknown_termination"),
    ]


def test_malformed_and_boundary_records_exact():
    tracer = TurnTracer()
    for name, state, move, terminated, cls in _reject_cases():
        before = copy.deepcopy(state)
        with pytest.raises(rt.TurnError) as ei:
            tracer.apply(state, move, terminated)
        err = ei.value
        rec = tracer.records[-1]
        assert rec["outcome"] == "reject", name
        assert rec["failure_class"] == err.failure_class == cls, name
        assert rec["code"] == err.code == FAILURE_MAPPING[cls]["error"], name
        assert rec["retryable"] is err.retryable, name
        assert rec["message"] == err.args[0] and err.args[0].strip(), name
        assert rec["state_before"] == before, name
        assert rec["terminated"] == terminated, name
        # the propagated error is unchanged vs the bare runtime
        with pytest.raises(rt.TurnError) as ei2:
            rt.apply_move(copy.deepcopy(state), move, terminated)
        bare = ei2.value
        assert (type(err), err.args, err.code, err.failure_class,
                err.retryable) == (type(bare), bare.args, bare.code,
                                   bare.failure_class, bare.retryable), name
        assert state == before, f"{name}: rejected input mutated"


def test_rollback_then_continue_same_state():
    tracer = TurnTracer()
    state = _state()
    with pytest.raises(rt.TurnError):
        tracer.apply(state, "null")
    assert state == _state(), "rejected call touched the state"
    out = tracer.apply(state, "quiet")  # continues from the SAME state
    assert out == rt.apply_move(_state(), "quiet")
    recs = tracer.records
    assert [r["outcome"] for r in recs] == ["reject", "accept"]
    assert recs[1]["state_before"] == _state()


def test_trace_integrity_append_only():
    tracer = TurnTracer()
    _run_session(tracer.apply, SEED + 1, 20)
    with pytest.raises(rt.TurnError):
        tracer.apply(_state(), "null")
    recs = tracer.records
    assert [r["seq"] for r in recs] == list(range(21))
    # the view is an immutable deep copy
    recs[0]["outcome"] = "tampered"
    recs[0]["state_before"]["side_to_move"] = "x"
    fresh = tracer.records
    assert fresh[0]["outcome"] == "accept"
    assert fresh[0]["state_before"] == _state()


def test_crash_recorded_and_same_exception_propagates():
    sentinel = ValueError("boom")

    def bad_apply(state, move, terminated=None):
        raise sentinel

    tracer = TurnTracer(bad_apply)
    with pytest.raises(ValueError) as ei:
        tracer.apply(_state(), "quiet")
    assert ei.value is sentinel, "tracer wrapped or replaced the exception"
    rec = tracer.records[-1]
    assert rec["outcome"] == "crash"
    assert rec["error_type"] == "ValueError"
    assert rec["state_before"] == _state()


def test_neutrality_accept_returns_exact_runtime_object():
    sentinel_state = _state(side="b", half=0, full=99)

    def spy_apply(state, move, terminated=None):
        return sentinel_state

    tracer = TurnTracer(spy_apply)
    out = tracer.apply(_state(), "quiet")
    assert out is sentinel_state, "tracer copied or rebuilt the result"
    assert tracer.records[-1]["state_after"] == sentinel_state
    # and the record is a snapshot: mutating the returned object later
    # must not rewrite the recorded history
    sentinel_state["halfmove_clock"] = 12345
    assert tracer.records[-1]["state_after"]["halfmove_clock"] == 0


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
        self.target["halfmove_clock"] = 999
        return self


def test_snapshot_bomb_in_each_input_stays_neutral():
    """Adversarial: a __deepcopy__ bomb in state, move, or terminated
    must not change behavior: the wrapped function is called exactly
    once with the ORIGINAL arguments, its exact result is returned,
    exactly one record lands, and the bombed fields degrade to the
    snapshot-failed marker."""
    sentinel_out = _state(side="b", half=1, full=2)
    for bomb_slot in ("state", "move", "terminated"):
        calls = []

        def spy(st, mv, term=None, _calls=calls):
            _calls.append((st, mv, term))
            return sentinel_out

        tracer = TurnTracer(spy)
        args = {"state": _state(), "move": "quiet", "terminated": None}
        args[bomb_slot] = _DeepcopyBomb()
        out = tracer.apply(args["state"], args["move"], args["terminated"])
        assert out is sentinel_out, bomb_slot
        assert len(calls) == 1, bomb_slot
        assert calls[0] == (args["state"], args["move"], args["terminated"])
        recs = tracer.records
        assert len(recs) == 1, bomb_slot
        rec = recs[0]
        key = {"state": "state_before", "move": "move",
               "terminated": "terminated"}[bomb_slot]
        assert rec[key] == {"__opaque__": "_DeepcopyBomb"}, bomb_slot
        assert rec["outcome"] == "accept", bomb_slot


def test_snapshot_bomb_in_result_stays_neutral():
    """Adversarial: the wrapped runtime SUCCEEDS with an unsnapshotable
    result. The tracer must return the exact object and still append
    exactly one record (state_after degraded)."""
    class BombDict(dict):
        def __deepcopy__(self, memo):
            raise RuntimeError("result bomb")

    sentinel_out = BombDict(_state())
    calls = []

    def spy(st, mv, term=None):
        calls.append(1)
        return sentinel_out

    tracer = TurnTracer(spy)
    out = tracer.apply(_state(), "quiet")
    assert out is sentinel_out
    assert len(calls) == 1
    recs = tracer.records
    assert len(recs) == 1
    assert recs[0]["outcome"] == "accept"
    assert recs[0]["state_after"] == {"__opaque__": "BombDict"}


def test_snapshot_bomb_during_reject_and_crash_recording():
    """Adversarial: snapshot failures while recording reject/crash
    fields degrade the record, never the propagated exception."""
    # reject: bombed state input + a genuine TurnError
    err_out = None

    def rejecting(st, mv, term=None):
        nonlocal err_out
        try:
            return rt.apply_move(_state(), "null")
        except rt.TurnError as err:
            err_out = err
            raise

    tracer = TurnTracer(rejecting)
    with pytest.raises(rt.TurnError) as ei:
        tracer.apply(_DeepcopyBomb(), "quiet")
    assert ei.value is err_out, "tracer replaced the rejection"
    assert len(tracer.records) == 1
    rec = tracer.records[0]
    assert rec["outcome"] == "reject"
    assert rec["failure_class"] == "illegal_transition"
    assert rec["state_before"] == {"__opaque__": "_DeepcopyBomb"}
    # crash: bombed state input + BaseException escape
    bomb_exit = SystemExit(3)

    def exiting(st, mv, term=None):
        raise bomb_exit

    tracer2 = TurnTracer(exiting)
    with pytest.raises(SystemExit) as ei2:
        tracer2.apply(_DeepcopyBomb(), "quiet")
    assert ei2.value is bomb_exit
    assert len(tracer2.records) == 1
    assert tracer2.records[0]["outcome"] == "crash"
    assert tracer2.records[0]["error_type"] == "SystemExit"
    assert tracer2.records[0]["state_before"] == {"__opaque__": "_DeepcopyBomb"}


def test_baseexception_family_recorded_with_exact_identity():
    """The contract's 'any non-TurnError escape' covers BaseException:
    KeyboardInterrupt and SystemExit propagate as the SAME object and
    each produces exactly one crash record."""
    for exc in (KeyboardInterrupt(), SystemExit(1), GeneratorExit()):
        calls = []

        def spy(st, mv, term=None, _exc=exc, _calls=calls):
            _calls.append(1)
            raise _exc

        tracer = TurnTracer(spy)
        with pytest.raises(type(exc)) as ei:
            tracer.apply(_state(), "quiet")
        assert ei.value is exc
        assert len(calls) == 1
        recs = tracer.records
        assert len(recs) == 1
        assert recs[0]["outcome"] == "crash"
        assert recs[0]["error_type"] == type(exc).__name__


def test_record_field_extraction_failure_degrades_not_replaces():
    """Adversarial: if building the reject FIELDS itself raises, the
    original exception still propagates and exactly one degraded
    record lands."""

    class ExplodingError(rt.TurnError):
        @property
        def failure_class(self):
            raise RuntimeError("field bomb")

        @failure_class.setter
        def failure_class(self, value):
            pass

    err = ExplodingError("illegal_transition", "boom",
                         "illegal_transition", False)

    def rejecting(st, mv, term=None):
        raise err

    tracer = TurnTracer(rejecting)
    with pytest.raises(ExplodingError) as ei:
        tracer.apply(_state(), "quiet")
    assert ei.value is err, "tracer replaced the exception"
    assert len(tracer.records) == 1
    assert tracer.records[0]["outcome"] == "record_degraded"
    assert tracer.records[0]["record_error"] == "RuntimeError"


def test_successful_side_effect_hook_never_invoked():
    """The decisive sibling: a __deepcopy__ that SUCCEEDS while
    mutating caller state. In state, move, or terminated, the wrapped
    function must observe the ORIGINAL objects, caller state must stay
    bit-identical, and the record must carry the opaque marker - the
    hook must never run at all."""
    for bomb_slot in ("state", "move", "terminated"):
        caller_state = _state(half=0)
        seen = []

        def spy(st, mv, term=None, _seen=seen):
            _seen.append((st, mv, term))
            return dict(_state())

        tracer = TurnTracer(spy)
        bomb = _SideEffectValue(caller_state)
        args = {"state": caller_state, "move": "quiet", "terminated": None}
        args[bomb_slot] = bomb
        tracer.apply(args["state"], args["move"], args["terminated"])
        assert caller_state == _state(half=0), (
            f"{bomb_slot}: caller state mutated by instrumentation")
        # the wrapped function observed the EXACT original arguments
        assert seen[0][0] is args["state"], bomb_slot
        assert seen[0][1] is args["move"], bomb_slot
        assert seen[0][2] is args["terminated"], bomb_slot
        rec = tracer.records[0]
        key = {"state": "state_before", "move": "move",
               "terminated": "terminated"}[bomb_slot]
        assert rec[key] == {"__opaque__": "_SideEffectValue"}, bomb_slot
        # history reads are side-effect-free too
        _ = tracer.records
        tracer.to_jsonl()
        assert caller_state == _state(half=0), bomb_slot


def test_records_read_never_invokes_user_hooks():
    """A stored opaque value must stay inert across records reads:
    no hook execution, no caller-state change, stable marker."""
    caller_state = _state(half=0)
    bomb = _SideEffectValue(caller_state)

    def spy(st, mv, term=None):
        return {"rights": bomb}  # out-of-domain value inside the result

    tracer = TurnTracer(spy)
    tracer.apply(caller_state, "quiet")
    assert caller_state == _state(half=0)
    r1 = tracer.records
    r2 = tracer.records
    assert r1 == r2
    assert r1[0]["state_after"] == {"rights": {"__opaque__": "_SideEffectValue"}}
    tracer.to_jsonl()
    assert caller_state == _state(half=0), "records read ran a user hook"
