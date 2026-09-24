"""T0280 deterministic fuzz/fault battery for the production crash resume.

Seeded generators drive the shipped store.crash_resume (T0278). Every
input log is built from an independent model of data/contracts/wal.yaml
and data/contracts/crash_resume.yaml (entry-id derivation, put/delete
fold, quarantine token and resume-id derivation are restated here, never
taken from production), then torn at a known position. The contract
reference engine (tests.test_t0275_crash_resume_contract) is the
differential oracle for every case.

The quarantine sink is store.crash_resume's only except-BaseException
oracle boundary. It forges a ResumeError of EVERY class and a WalError
(the linked module's error) of EVERY class; each must fail closed as a
FRESH divergent_quarantine. Pass-through mutants per class are pinned in
FORGE_TARGETS.
"""

from __future__ import annotations

import ast
import builtins
import copy
import functools
import hashlib
import json
import random
import types
from pathlib import Path

import pytest

from graph import diff
from graph.node import make_record, record_identity
from store import crash_resume, wal
from tests import test_t0275_crash_resume_contract as _contract

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "store" / "crash_resume.py").read_text()

FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
IDENTITIES = tuple(record_identity(record) for record in RECORDS)
GENESIS = "wal0:" + "0" * 64
FIELDS = ("resume_id", "head", "state_id", "resumed_count", "discarded_count",
          "quarantine_token")
MAX_DEPTH = crash_resume.MAX_DEPTH
MAX_DIGITS = crash_resume.MAX_INT_DIGITS
RESUME_CLASSES = tuple(sorted(crash_resume.FAILURE_MAPPING))
WAL_CLASSES = tuple(sorted(wal.FAILURE_MAPPING))
MER, UC, CS, DQ = ("malformed_resume_record", "unknown_checkpoint",
                   "corrupt_source", "divergent_quarantine")


# -- independent model --------------------------------------------------------

def _entry_id(sequence, op, identity, record, prior):
    canonical = (f"{identity}\n{record['variant']}\n{record['digest']}\n"
                 f"{record['snapshot_fen']}")
    return "wal1:" + hashlib.sha256(
        f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


def _model_log(ops):
    log, prior = [], GENESIS
    for sequence, (op, index) in enumerate(ops, 1):
        entry_id = _entry_id(sequence, op, IDENTITIES[index], RECORDS[index], prior)
        log.append({"entry_id": entry_id, "sequence": sequence, "op": op,
                    "payload": {"identity": IDENTITIES[index],
                                "record": dict(RECORDS[index])},
                    "prior_entry_id": prior})
        prior = entry_id
    return log


def _fold(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _token(tail, ensure_ascii=False):
    parts = ["qtn1"]
    for entry in tail:
        text = json.dumps(entry, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=ensure_ascii)
        parts.append(f"{len(text)}:{text}")
    return "qtn1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _receipt(ops, tail):
    log = _model_log(ops)
    head = log[-1]["entry_id"] if log else GENESIS
    sid = diff.state_id(_fold(ops))
    token = _token(tail)
    rid = "rsm1:" + hashlib.sha256(
        f"{head}\n{sid}\n{len(ops)}\n{len(tail)}\n{token}".encode()).hexdigest()
    return {"resume_id": rid, "head": head, "state_id": sid,
            "resumed_count": len(ops), "discarded_count": len(tail),
            "quarantine_token": token}


def _ops(rng, low=0, high=14):
    return tuple((rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
                 for _ in range(rng.randrange(low, high + 1)))


def _flip(value):
    return value[:-1] + ("0" if value[-1] != "0" else "1")


# WAL-invalid but admissible (canonical JSON) tears of the entry at the tear
# position; they are JSON values, so the tail round-trips exactly
TEARS = {
    "sequence-flip": lambda e: {**e, "sequence": e["sequence"] + 1},
    "entry-id-flip": lambda e: {**e, "entry_id": _flip(e["entry_id"])},
    "prior-flip": lambda e: {**e, "prior_entry_id": _flip(e["prior_entry_id"])},
    "unknown-op": lambda e: {**e, "op": "upsert"},
    "missing-key": lambda e: {k: v for k, v in e.items() if k != "op"},
    "extra-key": lambda e: {**e, "zz": [1, 2.5, None, True]},
    "wrong-digest": lambda e: {**e, "payload": {**e["payload"], "record": {
        **e["payload"]["record"], "digest": "pdv1:" + "1" * 64}}},
    "identity-other": lambda e: {**e, "payload": {**e["payload"],
                                                  "identity": "x\u00e9\u4e2d"}},
    "null": lambda e: None,
    "int": lambda e: 7,
    "str": lambda e: "torn \u00e9",
    "list": lambda e: [e["sequence"], "x"],
    "empty-dict": lambda e: {},
}


def _torn(ops, tear, extra):
    """The model log of OPS + EXTRA honest continuations with the entry at
    len(OPS) replaced by TEAR (or no tail when EXTRA == 0)."""
    full = _model_log(ops + extra)
    if not extra:
        return full
    full[len(ops)] = TEARS[tear](full[len(ops)])
    return full


# -- hostile values -----------------------------------------------------------

_ARMED = [False]


class _Str(str):
    pass


class _Int(int):
    pass


class _Float(float):
    pass


class _Dict(dict):
    pass


class _List(list):
    pass


class _Colliding:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if _ARMED[0]:
            raise RuntimeError("hostile __eq__")
        return other is self

    def __deepcopy__(self, memo):
        return self


def _nested(lists):
    """LISTS nested lists around an int."""
    value = 0
    for _ in range(lists):
        value = [value]
    return value


def _cycle():
    value = []
    value.append(value)
    return value


def _alias():
    shared = [1]
    return [shared, shared]


ADMISSIBLE = {
    "depth-max": lambda at: _nested(MAX_DEPTH - at + 1),
    "digits-max": lambda at: int("9" * MAX_DIGITS),
    "digits-max-negative": lambda at: -int("9" * MAX_DIGITS),
    "float": lambda at: 2.5e-300,
    "multibyte": lambda at: "\u00e9\u4e2d\U0001f600",
    "bare-true": lambda at: True,
}
INADMISSIBLE = {
    "depth-over": lambda at: _nested(MAX_DEPTH - at + 2),
    "digits-over": lambda at: int("9" * (MAX_DIGITS + 1)),
    "huge-int": lambda at: 1 << 20000,
    "nan": lambda at: float("nan"),
    "infinity": lambda at: float("inf"),
    "surrogate-value": lambda at: "a\ud800",
    "surrogate-key": lambda at: {"a\ud800": 1},
    "int-key": lambda at: {1: 1},
    "strsub-key": lambda at: {_Str("k"): 1},
    "colliding-key": lambda at: {_Colliding("k"): 1},
    "strsub": lambda at: _Str("s"),
    "intsub": lambda at: _Int(1),
    "floatsub": lambda at: _Float(1.0),
    "dictsub": lambda at: _Dict(a=1),
    "listsub": lambda at: _List([1]),
    "tuple": lambda at: (1, 2),
    "bytes": lambda at: b"x",
    "set": lambda at: {1},
    "object": lambda at: object(),
    "alias": lambda at: _alias(),
    "cycle": lambda at: _cycle(),
}


# -- the sink --------------------------------------------------------------------

def _sink(module, fault, calls, forged, live=None):
    def sink(tail):
        calls.append(copy.deepcopy(tail))
        honest = _token(tail)
        if live is not None:
            live()
        if fault is None:
            return honest
        kind, arg = fault
        if kind == "forge-resume":
            forged[0] = module.ResumeError(arg, crash_resume.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-wal":
            forged[0] = wal.WalError(arg, wal.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "raise":
            if arg == "UnicodeEncodeError":
                raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "hostile sink")
            raise getattr(builtins, arg)("hostile sink")
        if kind == "scribble":
            tail.append({"zz": 1})
            if tail and type(tail[0]) is dict:
                tail[0]["zz"] = 2
            return honest
        return {
            "none": None, "bytes": honest.encode(), "int": 7, "list": [honest],
            "strsub": _Str(honest), "upper": "qtn1:" + honest[5:].upper(),
            "newline": honest + "\n", "leading-space": " " + honest,
            "other-prefix": "qtn2:" + honest[5:], "flip": _flip(honest),
            "empty-tail": _token([]), "shorter": _token(tail[:-1]),
            "longer": _token(tail + [None]), "reversed": _token(tail[::-1]),
            "ascii-escaped": _token(tail, ensure_ascii=True),
        }[kind]
    return sink


# builtins store.crash_resume catches internally; each is forged at the sink
HANDLED_BUILTINS = ("ValueError", "UnicodeEncodeError", "TypeError", "RecursionError")
# builtins store.crash_resume itself RAISES (crash_resume.py: the empty-prefix
# AssertionError); each is forged at the sink too
RAISED_BUILTINS = ("AssertionError",)

SINK_FAULTS = (
    [("forge-resume", cls) for cls in RESUME_CLASSES]
    + [("forge-wal", cls) for cls in WAL_CLASSES]
    + [("raise", exc) for exc in ("ValueError", "KeyboardInterrupt", "SystemExit",
                                  "GeneratorExit", "MemoryError", "RecursionError")]
    # every builtin the module itself catches or re-raises (UnicodeEncodeError
    # in the scalar walk, TypeError in the freeze) is forged at the sink too
    + [("raise", exc) for exc in HANDLED_BUILTINS + RAISED_BUILTINS if exc not in (
        "ValueError", "RecursionError")]
    + [(kind, None) for kind in ("none", "bytes", "int", "list", "strsub", "upper",
                                 "newline", "leading-space", "other-prefix", "flip",
                                 "empty-tail", "shorter", "longer", "reversed",
                                 "ascii-escaped")])

LIVE_EDITS = ("add-log-entry-key", "add-payload-key", "add-record-key", "change-op",
              "drop-field", "append-log", "insert-log", "delete-log", "reverse-log",
              "clear-log", "add-request-key", "change-checkpoint", "clear-request",
              "add-tail-entry-key", "change-tail-entry")
LIVE_ENDINGS = ("honest", "raise", "flip")


def _live_edit(log, request, name):
    def edit():
        entry = log[0] if log and type(log[0]) is dict else None
        if name == "add-log-entry-key" and entry is not None:
            entry["zz"] = 1
        elif name == "add-payload-key" and entry is not None:
            entry["payload"]["zz"] = 1
        elif name == "add-record-key" and entry is not None:
            entry["payload"]["record"]["zz"] = 1
        elif name == "change-op" and entry is not None:
            entry["op"] = "upsert"
        elif name == "drop-field" and entry is not None:
            entry.pop("sequence")
        elif name == "append-log":
            log.append({"x": 1})
        elif name == "insert-log":
            log.insert(0, None)
        elif name == "delete-log" and log:
            del log[-1]
        elif name == "reverse-log":
            log.reverse()
        elif name == "clear-log":
            log.clear()
        elif name == "add-request-key":
            request["zz"] = 1
        elif name == "change-checkpoint":
            request["checkpoint_sequence"] = -5
        elif name == "clear-request":
            request.clear()
        elif name in ("add-tail-entry-key", "change-tail-entry") and log:
            # IN-PLACE mutation of a discarded tail entry (log[-1]) before
            # the sink returns: the token binds to the FROZEN tail, so the
            # honest token must still be accepted and the tail discarded
            last = log[-1]
            if type(last) is dict and name == "add-tail-entry-key":
                last["zz"] = 1
            elif type(last) is dict and last:
                last[next(iter(last))] = "changed-in-place"
            elif type(last) is dict:
                last["zz"] = 1
            elif type(last) is list:
                last.append("zz")
    return edit


def _rekey(mapping, old, new):
    items = [(new if key == old else key, value) for key, value in mapping.items()]
    mapping.clear()
    mapping.update(items)
    return mapping


REQUESTS = {
    "renamed": lambda r: _rekey(r, "checkpoint_sequence", "checkpoint_sequencx"),
    "missing": lambda r: {},
    "extra": lambda r: {**r, "force": True},
    "extra-non-ascii": lambda r: {**r, "\u00e9": 1},
    "strsub-key": lambda r: _rekey(r, "checkpoint_sequence", _Str("checkpoint_sequence")),
    "colliding-key": lambda r: _rekey(r, "checkpoint_sequence",
                                      _Colliding("checkpoint_sequence")),
    "dict-subclass": lambda r: _Dict(r),
    "list": lambda r: [r["checkpoint_sequence"]],
    "none": lambda r: None,
    "checkpoint-bool": lambda r: {"checkpoint_sequence": False},
    "checkpoint-intsub": lambda r: {"checkpoint_sequence": _Int(0)},
    "checkpoint-float": lambda r: {"checkpoint_sequence": 0.0},
    "checkpoint-str": lambda r: {"checkpoint_sequence": "0"},
    "checkpoint-none": lambda r: {"checkpoint_sequence": None},
    "checkpoint-list": lambda r: {"checkpoint_sequence": [0]},
    "negative-with-extra-key": lambda r: {"checkpoint_sequence": -1, "zz": 1},
    "negative-intsub": lambda r: {"checkpoint_sequence": _Int(-1)},
}
NON_LIST_LOGS = {"none": None, "tuple": (), "dict": {}, "str": "log", "int": 0,
                 "list-subclass": "SUB"}

GENERATORS = {
    "happy": 64,
    "tear": len(TEARS) * 3,
    "request": len(REQUESTS) * 2,
    "checkpoint": 24,
    "container": len(NON_LIST_LOGS),
    "admission": (len(ADMISSIBLE) + len(INADMISSIBLE)) * 3,
    "sink": len(SINK_FAULTS) * 2 + 4,
    "live": len(LIVE_EDITS) * len(LIVE_ENDINGS),
}


def _case(gen, seed):
    """A fresh case: label, a builder of fresh (log, request), the
    independently known outcome and the expected sink calls."""
    rng = random.Random(f"t0280:{gen}:{seed}")
    tears = sorted(TEARS)
    if gen in ("happy", "tear"):
        ops = _ops(rng)
        if gen == "happy":
            tear = rng.choice(tears)
            extra = _ops(rng, 0, 3)
        else:
            tear = tears[seed % len(tears)]
            extra = _ops(rng, 1, 3)
        checkpoint = rng.randrange(len(ops) + 1)
        tail = _torn(ops, tear, extra)[len(ops):]
        return {"label": f"{gen}:{tear}:{len(extra)}", "kind": "ok",
                "build": lambda: (_torn(ops, tear, extra),
                                  {"checkpoint_sequence": checkpoint}),
                "expect": _receipt(ops, tail), "tail": tail, "keep": len(ops),
                "calls": 1}
    if gen == "request":
        names = sorted(REQUESTS)
        name = names[seed % len(names)]
        hostile = seed // len(names) == 1
        ops = _ops(rng, 1, 6)

        def build():
            log = _model_log(ops)
            if hostile:
                log.append(object())
            return log, REQUESTS[name]({"checkpoint_sequence": 0})
        return {"label": f"request:{name}", "build": build, "expect": MER, "calls": 0}
    if gen == "checkpoint":
        ops = _ops(rng, 2, 8)
        variant = seed % 4
        if variant == 0:
            label, expect, cp, log = "negative", UC, -1 - seed, lambda: (
                _model_log(ops) + [object()])
        elif variant == 1:
            label, expect, cp, log = "past-log", CS, len(ops) + 1 + seed % 3, lambda: (
                _model_log(ops))
        elif variant == 2:
            tear = tears[seed % len(tears)]
            cp = len(ops) + 1
            label, expect, log = f"tear-at-checkpoint:{tear}", CS, lambda: (
                _torn(ops, tear, (("put", 0), ("put", 1))))
        else:
            tear = tears[seed % len(tears)]
            label, expect, cp, log = f"tear-before-checkpoint:{tear}", CS, len(ops), (
                lambda: _torn(ops[:-1], tear, ops[-1:] + (("put", 2),)))
        return {"label": f"checkpoint:{label}", "expect": expect, "calls": 0,
                "build": lambda: (log(), {"checkpoint_sequence": cp})}
    if gen == "container":
        name = sorted(NON_LIST_LOGS)[seed]
        value = NON_LIST_LOGS[name]
        return {"label": f"container:{name}", "expect": MER, "calls": 0,
                "build": lambda: (_List(_model_log((("put", 0),))) if value == "SUB"
                                  else copy.copy(value), {"checkpoint_sequence": 0})}
    if gen == "admission":
        names = [("ok", n) for n in sorted(ADMISSIBLE)] + \
            [("bad", n) for n in sorted(INADMISSIBLE)]
        verdict, name = names[seed % len(names)]
        where = ("tear", "after-tear", "acknowledged")[seed // len(names)]
        ops = _ops(rng, 2, 6)
        make = (ADMISSIBLE if verdict == "ok" else INADMISSIBLE)[name]

        def build():
            log = _model_log(ops + (("put", 0), ("put", 1)))
            if where == "tear":
                log[len(ops)] = make(1)
                cp = len(ops)
            elif where == "after-tear":
                log[len(ops)] = None
                log[len(ops) + 1]["zz"] = make(2)
                cp = len(ops)
            else:
                log[0]["payload"]["zz"] = make(3)
                cp = len(ops)
            return log, {"checkpoint_sequence": cp}
        if where == "acknowledged":
            return {"label": f"admission:{verdict}:{name}@{where}", "build": build,
                    "expect": CS, "calls": 0}
        if verdict == "bad":
            return {"label": f"admission:bad:{name}@{where}", "build": build,
                    "expect": MER, "calls": 0}
        log, _request = build()
        tail = json.loads(json.dumps(log[len(ops):]))
        return {"label": f"admission:ok:{name}@{where}", "build": build, "kind": "ok",
                "expect": _receipt(ops, tail), "tail": tail, "keep": len(ops),
                "calls": 1}
    if gen == "sink":
        # a two-entry tail with non-ASCII text, so every wrong-token shape
        # (empty, shorter, longer, reversed, ASCII-escaped) differs
        ops = _ops(rng, 0, 3) if seed % 2 == 0 else _ops(rng, 4, 10)
        extra = (("put", 3), ("delete", 3))
        tear = "identity-other"
        tail = _torn(ops, tear, extra)[len(ops):]

        def build():
            return _torn(ops, tear, extra), {"checkpoint_sequence": len(ops)}
        if seed >= len(SINK_FAULTS) * 2:
            return {"label": f"sink:scribble:{seed % 2}", "build": build, "kind": "ok",
                    "fault": ("scribble", None), "expect": _receipt(ops, tail),
                    "tail": tail, "keep": len(ops), "calls": 1}
        fault = SINK_FAULTS[seed // 2]
        return {"label": f"sink:{fault[0]}:{fault[1]}", "build": build, "fault": fault,
                "expect": DQ, "calls": 1, "tail": tail}
    edit = LIVE_EDITS[seed % len(LIVE_EDITS)]
    ending = LIVE_ENDINGS[seed // len(LIVE_EDITS)]
    ops = _ops(rng, 1, 8)
    extra = (("put", 4), ("put", 5))
    tear = tears[seed % len(tears)]
    tail = _torn(ops, tear, extra)[len(ops):]
    fault = {"honest": None, "raise": ("raise", "SystemExit"), "flip": ("flip", None)}
    case = {"label": f"live:{edit}:{ending}", "live": edit, "fault": fault[ending],
            "build": lambda: (_torn(ops, tear, extra), {"checkpoint_sequence": len(ops)}),
            "tail": tail, "calls": 1}
    if ending == "honest":
        case.update(kind="ok", expect=_receipt(ops, tail), keep=len(ops))
    else:
        case["expect"] = DQ
    return case


# -- running a case --------------------------------------------------------------

def _shape(value, seen=None):
    """Value, exact type, key order and identity at every level (cycle safe)."""
    seen = set() if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return ("again", id(value))
        seen.add(id(value))
        if isinstance(value, dict):
            return (type(value), id(value),
                    [(type(k), k if type(k) is str else id(k), _shape(v, seen))
                     for k, v in dict.items(value)])
        return (type(value), id(value), [_shape(v, seen) for v in list.__iter__(value)])
    if isinstance(value, float) and value != value:
        return (type(value), "nan")
    if type(value) in (str, int, float, bool, bytes, type(None), _Str, _Int, _Float):
        return (type(value), value)
    return (type(value), id(value))


def _outcome(module, case):
    calls, forged, why = [], [None], []
    log, request = case["build"]()
    log_before, request_before = _shape(log), _shape(request)
    kept = [id(e) for e in log[:case.get("keep", 0)]] if type(log) is list else []
    live = _live_edit(log, request, case["live"]) if "live" in case else None
    engine = module.ResumeEngine(_sink(module, case.get("fault"), calls, forged, live))
    _ARMED[0] = True
    try:
        result = engine.resume(log, request)
    except module.ResumeError as error:
        result = error
    except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
        return f"raw:{type(error).__name__}", [f"raw {type(error).__name__}"]
    finally:
        _ARMED[0] = False
    if len(calls) != case["calls"]:
        why.append(f"{len(calls)} sink calls")
    if calls and "tail" in case and calls[0] != case["tail"]:
        why.append("sink saw a wrong tail")
    if _shape(request) != request_before:
        why.append("request changed")
    if isinstance(result, BaseException):
        if _shape(log) != log_before:
            why.append("log changed on rejection")
        if type(result) is not module.ResumeError or \
                result.code != crash_resume.FAILURE_MAPPING.get(result.failure_class):
            why.append("untyped failure")
        if forged[0] is not None and result is forged[0]:
            why.append("forged error escaped")
        return result.failure_class, why
    if [id(e) for e in log] != kept or \
            _shape(log)[2] != log_before[2][:case.get("keep", 0)]:
        why.append("surviving prefix not kept in place")
    if type(result) is not dict or list(result) != list(FIELDS):
        why.append("receipt not in contract order")
    return result, why


def _check(module, gen, seed):
    case = _case(gen, seed)
    got, why = _outcome(module, case)
    if got != case["expect"]:
        why.append(f"got {got!r:.120}, expected {case['expect']!r:.120}")
    if got != _oracle(gen, seed):
        why.append("differs from the contract reference")
    return why


@functools.cache
def _oracle(gen, seed):
    return _outcome(_contract, _case(gen, seed))[0]


def _all_cases():
    return [(gen, seed) for gen, count in GENERATORS.items() for seed in range(count)]


# -- tests: fuzz -----------------------------------------------------------------

@pytest.mark.parametrize("gen", sorted(GENERATORS))
def test_fuzz_cases_hold(gen):
    bad = {}
    for seed in range(GENERATORS[gen]):
        why = _check(crash_resume, gen, seed)
        if why:
            bad[_case(gen, seed)["label"]] = why
    assert not bad, bad


def test_determinism_by_value():
    for gen, seed in _all_cases()[::5]:
        a, _ = _outcome(crash_resume, _case(gen, seed))
        b, _ = _outcome(crash_resume, _case(gen, seed))
        assert a == b, (gen, seed)


def test_no_poisoning_after_rejections():
    for gen in ("request", "checkpoint", "admission", "sink", "live"):
        for seed in range(GENERATORS[gen]):
            _outcome(crash_resume, _case(gen, seed))
    ops = (("put", 0), ("put", 1), ("delete", 0))
    log = _torn(ops, "unknown-op", (("put", 2),))
    engine = crash_resume.ResumeEngine(crash_resume.quarantine_tail)
    tail = log[3:]
    assert engine.resume(log, {"checkpoint_sequence": 3}) == _receipt(ops, tail)
    assert engine.resume(log, {"checkpoint_sequence": 3}) == _receipt(ops, [])


def test_corpus_reaches_every_class_and_shape():
    cases = [_case(gen, seed) for gen, seed in _all_cases()]
    labels = {case["label"] for case in cases}
    expects = {case["expect"] for case in cases if type(case["expect"]) is str}
    assert expects == set(RESUME_CLASSES)
    assert any(type(case["expect"]) is dict for case in cases)
    for tear in TEARS:
        assert any(label.startswith(f"tear:{tear}:") for label in labels)
    for name in INADMISSIBLE:
        for where in ("tear", "after-tear", "acknowledged"):
            assert f"admission:bad:{name}@{where}" in labels
    for edit in LIVE_EDITS:
        for ending in LIVE_ENDINGS:
            assert f"live:{edit}:{ending}" in labels


def test_every_boundary_error_class_is_forged():
    assert {a for k, a in SINK_FAULTS if k == "forge-resume"} == \
        set(crash_resume.FAILURE_MAPPING)
    assert {a for k, a in SINK_FAULTS if k == "forge-wal"} == set(wal.FAILURE_MAPPING)
    forged_raises = {a for k, a in SINK_FAULTS if k == "raise"}
    assert set(HANDLED_BUILTINS) | set(RAISED_BUILTINS) <= forged_raises
    # enumerate EVERY raise site and except clause of the module (not only
    # the except clauses): each builtin named is forged, each non-builtin
    # is the module's own ResumeError (forged per class) or the linked
    # WalError (forged per class)
    raised, caught = set(), set()
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            raised.add(ast.unparse(exc))
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            kinds = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            caught |= {ast.unparse(k) for k in kinds}
    assert raised == {"ResumeError", *RAISED_BUILTINS}
    assert caught - {"BaseException"} == {"_wal.WalError", *HANDLED_BUILTINS}
    # no module-private error class exists to forge (checked, not assumed)
    assert not {n for n, v in vars(crash_resume).items() if n.startswith("_")
                and isinstance(v, type) and issubclass(v, BaseException)}
    assert set(FORGE_TARGETS) <= set(MUTANTS)


# -- mutation check --------------------------------------------------------------

def _source_mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0280_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "crash_resume.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.ResumeError = crash_resume.ResumeError

    def _fail(failure_class):
        raise crash_resume.ResumeError(failure_class,
                                       crash_resume.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


_BOUNDARY = ("        except BaseException:\n"
             "            # fail closed against the FULL BaseException surface\n"
             "            _fail(\"divergent_quarantine\")\n")


def _passthrough(error, cls):
    guard = "" if cls is None else (
        f"            if error.failure_class != {cls!r}:\n"
        "                _fail(\"divergent_quarantine\")\n")
    return [(_BOUNDARY, f"        except {error} as error:\n" + guard +
             "            raise\n" + _BOUNDARY)]


MUTANTS = {
    "except-exception": [(_BOUNDARY, _BOUNDARY.replace("BaseException:", "Exception:"))],
    "resume-error-passthrough": _passthrough("ResumeError", None),
    **{f"resume-error-passthrough-{c}": _passthrough("ResumeError", c)
       for c in RESUME_CLASSES},
    "wal-error-passthrough": _passthrough("_wal.WalError", None),
    **{f"wal-error-passthrough-{c}": _passthrough("_wal.WalError", c)
       for c in WAL_CLASSES},
    **{f"builtin-passthrough-{e}": [(_BOUNDARY, f"        except {e}:\n"
                                     "            raise\n" + _BOUNDARY)]
       for e in HANDLED_BUILTINS + RAISED_BUILTINS},
    "token-from-live-tail": [("            if token != quarantine_tail(frozen_tail):\n",
                              "            if token != quarantine_tail(tail):\n")],
    "sink-output-isinstance": [("if type(out) is not str or",
                                "if not isinstance(out, str) or")],
    "no-token-equality": [("            if token != quarantine_tail(frozen_tail):\n",
                           "            if False:\n")],
    "sink-shares-frozen-tail": [("self.sink(copy.deepcopy(frozen_tail))",
                                 "self.sink(frozen_tail)")],
    "no-restore": [("            _restore(acc)\n", "            pass\n")],
    "no-request-restore": [("        _snapshot(request, acc, set())\n", "")],
    "log-isinstance": [("        if type(log) is not list:\n",
                        "        if not isinstance(log, list):\n")],
    "request-isinstance": [("        if type(request) is not dict:\n",
                            "        if not isinstance(request, dict):\n")],
    "no-request-key-guard": [("        if not all(type(key) is str for key in "
                              "dict.keys(request)):\n", "        if False:\n")],
    "request-key-len": [("if set(request.keys()) != set(_REQUEST_FIELDS):",
                         "if len(request) != len(_REQUEST_FIELDS):")],
    "request-key-superset": [("if set(request.keys()) != set(_REQUEST_FIELDS):",
                              "if not set(request.keys()) >= set(_REQUEST_FIELDS):")],
    "checkpoint-isinstance": [("        if type(checkpoint) is not int:\n",
                               "        if not isinstance(checkpoint, int):\n")],
    "no-negative-check": [("        if checkpoint < 0:\n", "        if False:\n")],
    "negative-check-inclusive": [("        if checkpoint < 0:\n",
                                  "        if checkpoint <= 0:\n")],
    "no-prefix-checkpoint-check": [("        if k < checkpoint:\n", "        if False:\n")],
    "no-tail-admission": [("        if not all(_is_canonical_json(entry) for entry in tail):\n",
                           "        if False:\n")],
    "depth-inclusive": [("if depth > MAX_DEPTH or id(node) in seen:",
                         "if depth >= MAX_DEPTH or id(node) in seen:")],
    "no-alias-check": [("if depth > MAX_DEPTH or id(node) in seen:",
                        "if depth > MAX_DEPTH:")],
    "float-any": [("        return math.isfinite(obj)\n", "        return True\n")],
    "no-str-utf8": [('            obj.encode("utf-8")\n        except UnicodeEncodeError:\n'
                     "            return False\n",
                     "            pass\n        except UnicodeEncodeError:\n"
                     "            return False\n")],
    "digits-exclusive": [("return len(str(abs(obj))) <= MAX_INT_DIGITS",
                          "return len(str(abs(obj))) < MAX_INT_DIGITS")],
    "commit-keeps-one": [("        del log[k:]\n", "        del log[k + 1:]\n")],
    "no-commit": [("        del log[k:]\n", "")],
    "resume-id-swapped-counts": [("{resumed}\\n{discarded}", "{discarded}\\n{resumed}")],
    "resumed-count-is-checkpoint": [('            "resumed_count": k,\n',
                                     '            "resumed_count": checkpoint,\n')],
}

FORGE_TARGETS = {
    "except-exception": "sink:raise:KeyboardInterrupt",
    "resume-error-passthrough": f"sink:forge-resume:{RESUME_CLASSES[0]}",
    **{f"resume-error-passthrough-{c}": f"sink:forge-resume:{c}" for c in RESUME_CLASSES},
    "wal-error-passthrough": f"sink:forge-wal:{WAL_CLASSES[0]}",
    **{f"wal-error-passthrough-{c}": f"sink:forge-wal:{c}" for c in WAL_CLASSES},
    **{f"builtin-passthrough-{e}": f"sink:raise:{e}"
       for e in HANDLED_BUILTINS + RAISED_BUILTINS},
}

# one-guard edits no black-box case can separate: the byte-exact token
# equality rejects every string the grammar check would, with the same
# class; a checkpoint past the log is always past the prefix too; an
# isinstance key check is backed by _scalar_ok's exact-type check
EQUIVALENT_EDITS = {
    "key-isinstance": [("if type(key) is not str or not _scalar_ok(key):",
                        "if not isinstance(key, str) or not _scalar_ok(key):")],
    "token-grammar-search": [("_TOKEN_RE.fullmatch(out)", "_TOKEN_RE.search(out)")],
    "no-token-grammar": [(" or _TOKEN_RE.fullmatch(out) is None", "")],
    "no-past-log-check": [("        if checkpoint > len(log):\n", "        if False:\n")],
}


def _probe(module, first_only=False, only=None):
    labels = []
    for gen, seed in _all_cases():
        if only is not None and _case(gen, seed)["label"] not in only:
            continue
        try:
            why = _check(module, gen, seed)
        except BaseException as error:  # noqa: BLE001
            why = [f"check raised {type(error).__name__}"]
        if why:
            labels.append(_case(gen, seed)["label"])
            if first_only:
                break
    return labels


def test_identity_source_mutant_is_green():
    assert _probe(_source_mutant("identity", [])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    module = _source_mutant(name, MUTANTS[name])
    if name in FORGE_TARGETS:
        target = FORGE_TARGETS[name]
        assert set(_probe(module, only={target})) == {target}, name
    else:
        assert _probe(module, first_only=True), name


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edits_stay_green(name):
    assert _probe(_source_mutant(name, EQUIVALENT_EDITS[name])) == []
