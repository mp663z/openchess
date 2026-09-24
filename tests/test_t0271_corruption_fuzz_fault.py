"""T0271 deterministic fuzz/fault battery for the production corruption scan.

Seeded generators drive the shipped store.corruption (T0269). Every input
log is built from an independent model of data/contracts/wal.yaml and
data/contracts/corruption.yaml (entry-id derivation, canonical encoding,
quarantine token and scan-id derivation are restated here, never taken
from production), then corrupted at a known position. The contract
reference engine (tests.test_t0266_corruption_contract) is the
differential oracle for every case.

The quarantine sink is store.corruption's only except-BaseException
oracle boundary. It forges a CorruptionError of EVERY class and a
WalError (the linked module's error) of EVERY class, plus the module's
PRIVATE error class _OutOfDomain; each must fail closed as a FRESH
divergent_quarantine. Pass-through mutants per class are pinned in
FORGE_TARGETS.
"""

from __future__ import annotations

import ast
import builtins
import copy
import functools
import hashlib
import random
import types
from pathlib import Path

import pytest

from graph.node import make_record, record_identity
from store import corruption, wal
from tests import test_t0266_corruption_contract as _contract

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "store" / "corruption.py").read_text()

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
FIELDS = ("scan_id", "verdict", "verified_head", "verified_count",
          "quarantined_count", "quarantine_token")
MAX_DEPTH = corruption.MAX_DEPTH
MAX_BITS = corruption.MAX_INT_BITS
CLASSES = tuple(sorted(corruption.FAILURE_MAPPING))
WAL_CLASSES = tuple(sorted(wal.FAILURE_MAPPING))
MER, EL, DQ = "malformed_corruption_record", "excessive_loss", "divergent_quarantine"


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


def _enc(value):
    """Independent type-tagged, length-framed canonical encoding."""
    if value is None:
        return b"n"
    if type(value) is bool:
        return b"t" if value else b"f"
    if type(value) is int:
        text = str(value).encode()
        return b"i%d:" % len(text) + text
    if type(value) is str:
        raw = value.encode("utf-8")
        return b"s%d:" % len(raw) + raw
    if type(value) is list:
        return b"l%d:" % len(value) + b"".join(_enc(v) for v in value)
    return b"d%d:" % len(value) + b"".join(_enc(k) + _enc(value[k])
                                            for k in sorted(value))


def _token(suffix):
    return "qrn1:" + hashlib.sha256(b"qrn1|" + _enc(list(suffix))).hexdigest()


def _receipt(ops, suffix):
    log = _model_log(ops)
    head = log[-1]["entry_id"] if log else GENESIS
    token = _token(suffix) if suffix else None
    verdict = "salvaged" if suffix else "clean"
    sid = "crp1:" + hashlib.sha256(
        f"{verdict}\n{head}\n{len(ops)}\n{len(suffix)}\n"
        f"{token if token is not None else '-'}".encode()).hexdigest()
    return {"scan_id": sid, "verdict": verdict, "verified_head": head,
            "verified_count": len(ops), "quarantined_count": len(suffix),
            "quarantine_token": token}


def _ops(rng, low=0, high=12):
    return tuple((rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
                 for _ in range(rng.randrange(low, high + 1)))


def _flip(value):
    return value[:-1] + ("0" if value[-1] != "0" else "1")


# WAL-invalid but in-domain corruptions of the entry at the corruption point
TEARS = {
    "sequence-flip": lambda e: {**e, "sequence": e["sequence"] + 1},
    "entry-id-flip": lambda e: {**e, "entry_id": _flip(e["entry_id"])},
    "prior-flip": lambda e: {**e, "prior_entry_id": _flip(e["prior_entry_id"])},
    "unknown-op": lambda e: {**e, "op": "upsert"},
    "missing-key": lambda e: {k: v for k, v in e.items() if k != "op"},
    "extra-key": lambda e: {**e, "zz": [1, None, True]},
    "wrong-digest": lambda e: {**e, "payload": {**e["payload"], "record": {
        **e["payload"]["record"], "digest": "pdv1:" + "1" * 64}}},
    "identity-other": lambda e: {**e, "payload": {**e["payload"],
                                                  "identity": "x\u00e9\u4e2d"}},
    "sequence-bool": lambda e: {**e, "sequence": True},
    "null": lambda e: None,
    "int": lambda e: 7,
    "str": lambda e: "torn \u00e9",
    "list": lambda e: [e["sequence"], "x"],
    "empty-dict": lambda e: {},
}


def _torn(ops, tear, extra):
    full = _model_log(ops + extra)
    if extra:
        full[len(ops)] = TEARS[tear](full[len(ops)])
    return full


# -- hostile values -----------------------------------------------------------

_ARMED = [False]


class _Str(str):
    pass


class _Int(int):
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


# makers take the depth AT which the value sits (the log is depth 1)
IN_DOMAIN = {
    "depth-max": lambda at: _nested(MAX_DEPTH - at),
    "bits-max": lambda at: (1 << MAX_BITS) - 1,
    "bits-max-negative": lambda at: -((1 << MAX_BITS) - 1),
    "multibyte": lambda at: "\u00e9\u4e2d\U0001f600",
    "false": lambda at: False,
    "empty-containers": lambda at: [{}, []],
}
OUT_OF_DOMAIN = {
    "depth-over": lambda at: _nested(MAX_DEPTH - at + 1),
    "bits-over": lambda at: 1 << MAX_BITS,
    "huge-int": lambda at: 1 << 20000,
    "float": lambda at: 1.5,
    "nan": lambda at: float("nan"),
    "surrogate-value": lambda at: "a\ud800",
    "surrogate-key": lambda at: {"a\ud800": 1},
    "int-key": lambda at: {1: 1},
    "strsub-key": lambda at: {_Str("k"): 1},
    "colliding-key": lambda at: {_Colliding("k"): 1},
    "strsub": lambda at: _Str("s"),
    "intsub": lambda at: _Int(1),
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
    def sink(suffix):
        calls.append(copy.deepcopy(suffix))
        honest = _token(suffix)
        if live is not None:
            live()
        if fault is None:
            return honest
        kind, arg = fault
        if kind == "forge-corruption":
            forged[0] = module.CorruptionError(arg, corruption.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-wal":
            forged[0] = wal.WalError(arg, wal.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-private":
            # the module's OWN private error class (its admission walk's
            # _OutOfDomain), forged by the untrusted sink
            private = getattr(module, "_OutOfDomain", corruption._OutOfDomain)
            forged[0] = private(arg)
            raise forged[0]
        if kind == "raise":
            if arg == "UnicodeEncodeError":
                raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "hostile sink")
            raise getattr(builtins, arg)("hostile sink")
        if kind == "scribble":
            suffix.append({"zz": 1})
            if type(suffix[0]) is dict:
                suffix[0]["zz"] = 2
            return honest
        return {
            "none": None, "bytes": honest.encode(), "int": 7, "list": [honest],
            "strsub": _Str(honest), "upper": "qrn1:" + honest[5:].upper(),
            "newline": honest + "\n", "leading-space": " " + honest,
            "other-prefix": "qtn1:" + honest[5:], "flip": _flip(honest),
            "surrogate": honest + "\ud800", "empty-suffix": _token([]),
            "shorter": _token(suffix[:-1]), "longer": _token(suffix + [None]),
            "reversed": _token(suffix[::-1]),
        }[kind]
    return sink


# module-private error classes store.corruption can raise or re-raise;
# every one is forged at the sink boundary (with any reason string)
PRIVATE_ERRORS = ("_OutOfDomain",)
PRIVATE_REASONS = ("depth", "forged-by-sink")
# builtins store.corruption catches (UnicodeEncodeError, in the scalar walk
# and the sink-output check) or raises (none today); each is forged too
HANDLED_BUILTINS = ("UnicodeEncodeError",)
RAISED_BUILTINS = ()

SINK_FAULTS = (
    [("forge-corruption", cls) for cls in CLASSES]
    + [("forge-wal", cls) for cls in WAL_CLASSES]
    + [("forge-private", reason) for reason in PRIVATE_REASONS]
    + [("raise", exc) for exc in HANDLED_BUILTINS + RAISED_BUILTINS]
    + [("raise", exc) for exc in ("ValueError", "KeyboardInterrupt", "SystemExit",
                                  "GeneratorExit", "MemoryError", "RecursionError")]
    + [(kind, None) for kind in ("none", "bytes", "int", "list", "strsub", "upper",
                                 "newline", "leading-space", "other-prefix", "flip",
                                 "surrogate", "empty-suffix", "shorter", "longer",
                                 "reversed")])

LIVE_EDITS = ("add-entry-key", "add-payload-key", "add-record-key", "change-op",
              "drop-field", "append-log", "insert-log", "delete-log", "reverse-log",
              "clear-log", "add-request-key", "change-max-loss", "clear-request")
LIVE_ENDINGS = ("honest", "raise", "flip")


def _live_edit(log, request, name):
    def edit():
        entry = log[0] if log and type(log[0]) is dict else None
        if name == "add-entry-key" and entry is not None:
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
        elif name == "change-max-loss":
            request["max_loss"] = -5
        elif name == "clear-request":
            request.clear()
    return edit


def _rekey(mapping, old, new):
    items = [(new if key == old else key, value) for key, value in mapping.items()]
    mapping.clear()
    mapping.update(items)
    return mapping


REQUESTS = {
    "renamed": lambda r: _rekey(r, "max_loss", "max_losx"),
    "missing": lambda r: {},
    "extra": lambda r: {**r, "force": True},
    "extra-non-ascii": lambda r: {**r, "\u00e9": 1},
    "strsub-key": lambda r: _rekey(r, "max_loss", _Str("max_loss")),
    "colliding-key": lambda r: _rekey(r, "max_loss", _Colliding("max_loss")),
    "dict-subclass": lambda r: _Dict(r),
    "list": lambda r: [r["max_loss"]],
    "none": lambda r: None,
    "max-loss-bool": lambda r: {"max_loss": True},
    "max-loss-intsub": lambda r: {"max_loss": _Int(5)},
    "max-loss-float": lambda r: {"max_loss": 5.0},
    "max-loss-str": lambda r: {"max_loss": "5"},
    "max-loss-none": lambda r: {"max_loss": None},
    "max-loss-list": lambda r: {"max_loss": [5]},
    "max-loss-negative": lambda r: {"max_loss": -1},
}
NON_LIST_LOGS = {"none": None, "tuple": (), "dict": {}, "str": "log", "int": 0,
                 "list-subclass": "SUB"}

GENERATORS = {
    "happy": 64,
    "tear": len(TEARS) * 3,
    "loss": 24,
    "request": len(REQUESTS) * 2,
    "container": len(NON_LIST_LOGS),
    "domain": len(IN_DOMAIN) * 2 + len(OUT_OF_DOMAIN) * 3,
    "sink": len(SINK_FAULTS) * 2 + 4,
    "live": len(LIVE_EDITS) * len(LIVE_ENDINGS),
}


def _ok_case(label, ops, log_builder, suffix, max_loss, **extra):
    case = {"label": label, "kind": "ok", "expect": _receipt(ops, suffix),
            "suffix": suffix, "keep": len(ops), "calls": 1 if suffix else 0,
            "build": lambda: (log_builder(), {"max_loss": max_loss})}
    case.update(extra)
    return case


def _case(gen, seed):
    rng = random.Random(f"t0271:{gen}:{seed}")
    tears = sorted(TEARS)
    if gen in ("happy", "tear"):
        ops = _ops(rng)
        if gen == "happy":
            tear, extra = rng.choice(tears), _ops(rng, 0, 3)
        else:
            tear, extra = tears[seed % len(tears)], _ops(rng, 1, 3)
        suffix = _torn(ops, tear, extra)[len(ops):]
        return _ok_case(f"{gen}:{tear}:{len(extra)}", ops,
                        lambda: _torn(ops, tear, extra), suffix,
                        len(suffix) + rng.randrange(3))
    if gen == "loss":
        ops = _ops(rng, 1, 8)
        extra = _ops(rng, 1, 3)
        tear = tears[seed % len(tears)]
        suffix = _torn(ops, tear, extra)[len(ops):]
        variant = seed % 3
        if variant == 0:
            return {"label": "loss:over-bound", "expect": EL, "calls": 0,
                    "build": lambda: (_torn(ops, tear, extra),
                                      {"max_loss": len(suffix) - 1})}
        if variant == 1:
            return _ok_case("loss:at-bound", ops, lambda: _torn(ops, tear, extra),
                            suffix, len(suffix))
        return _ok_case("loss:clean-zero-bound", ops, lambda: _model_log(ops), [], 0)
    if gen == "request":
        names = sorted(REQUESTS)
        name = names[seed % len(names)]
        hostile = seed // len(names) == 1
        ops = _ops(rng, 1, 6)

        def build():
            log = _torn(ops, "unknown-op", (("put", 0),))
            if hostile:
                log.append(object())
            return log, REQUESTS[name]({"max_loss": 5})
        return {"label": f"request:{name}", "build": build, "expect": MER, "calls": 0}
    if gen == "container":
        name = sorted(NON_LIST_LOGS)[seed]
        value = NON_LIST_LOGS[name]
        return {"label": f"container:{name}", "expect": MER, "calls": 0,
                "build": lambda: (_List(_model_log((("put", 0),))) if value == "SUB"
                                  else copy.copy(value), {"max_loss": 5})}
    if gen == "domain":
        names = [("in", n, w) for w in ("tear", "after-tear") for n in sorted(IN_DOMAIN)]
        names += [("out", n, w) for w in ("tear", "after-tear", "verified")
                  for n in sorted(OUT_OF_DOMAIN)]
        verdict, name, where = names[seed]
        ops = _ops(rng, 2, 6)
        make = (IN_DOMAIN if verdict == "in" else OUT_OF_DOMAIN)[name]

        def build():
            log = _model_log(ops + (("put", 0), ("put", 1)))
            if where == "tear":
                log[len(ops)] = make(2)
            elif where == "after-tear":
                log[len(ops)] = None
                log[len(ops) + 1]["zz"] = make(3)
            else:
                log[0]["payload"]["zz"] = make(4)
            return log, {"max_loss": 5}
        label = f"domain:{verdict}:{name}@{where}"
        if verdict == "out":
            return {"label": label, "build": build, "expect": MER, "calls": 0}
        log, _request = build()
        return _ok_case(label, ops, lambda: build()[0], copy.deepcopy(log[len(ops):]), 5)
    if gen == "sink":
        # a two-entry suffix, so every wrong-token shape differs
        ops = _ops(rng, 0, 3) if seed % 2 == 0 else _ops(rng, 4, 10)
        extra = (("put", 3), ("delete", 3))
        suffix = _torn(ops, "identity-other", extra)[len(ops):]

        def build():
            return _torn(ops, "identity-other", extra), {"max_loss": 2}
        if seed >= len(SINK_FAULTS) * 2:
            return _ok_case(f"sink:scribble:{seed % 2}", ops,
                            lambda: build()[0], suffix, 2, fault=("scribble", None))
        fault = SINK_FAULTS[seed // 2]
        return {"label": f"sink:{fault[0]}:{fault[1]}", "build": build, "fault": fault,
                "expect": DQ, "calls": 1, "suffix": suffix}
    edit = LIVE_EDITS[seed % len(LIVE_EDITS)]
    ending = LIVE_ENDINGS[seed // len(LIVE_EDITS)]
    ops = _ops(rng, 1, 8)
    extra = (("put", 4), ("put", 5))
    tear = tears[seed % len(tears)]
    suffix = _torn(ops, tear, extra)[len(ops):]
    fault = {"honest": None, "raise": ("raise", "SystemExit"), "flip": ("flip", None)}
    if ending == "honest":
        return _ok_case(f"live:{edit}:{ending}", ops, lambda: _torn(ops, tear, extra),
                        suffix, 2, live=edit)
    return {"label": f"live:{edit}:{ending}", "live": edit, "fault": fault[ending],
            "build": lambda: (_torn(ops, tear, extra), {"max_loss": 2}),
            "suffix": suffix, "calls": 1, "expect": DQ}


# -- running a case --------------------------------------------------------------

class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _pins(items):
    """Keep-alive identity tokens of ITEMS, in order: the one helper every
    reference-preservation site and its replace-twice probe share."""
    return [_Pin(item) for item in items]


def _shape(value, seen=None):
    seen = set() if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return ("again", _Pin(value))
        seen.add(id(value))
        if isinstance(value, dict):
            return (type(value), _Pin(value),
                    [(type(k), k if type(k) is str else _Pin(k), _shape(v, seen))
                     for k, v in dict.items(value)])
        return (type(value), _Pin(value), [_shape(v, seen) for v in list.__iter__(value)])
    if isinstance(value, float) and value != value:
        return (type(value), "nan")
    if type(value) in (str, int, float, bool, bytes, type(None), _Str, _Int):
        return (type(value), value)
    return (type(value), _Pin(value))


def _outcome(module, case):
    calls, forged, why = [], [None], []
    log, request = case["build"]()
    log_before, request_before = _shape(log), _shape(request)
    keep = case.get("keep", 0)
    kept = _pins(log[:keep]) if type(log) is list else []
    live = _live_edit(log, request, case["live"]) if "live" in case else None
    engine = module.CorruptionEngine(_sink(module, case.get("fault"), calls, forged, live))
    _ARMED[0] = True
    try:
        result = engine.scan(log, request)
    except module.CorruptionError as error:
        result = error
    except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
        return f"raw:{type(error).__name__}", [f"raw {type(error).__name__}"]
    finally:
        _ARMED[0] = False
    if len(calls) != case["calls"]:
        why.append(f"{len(calls)} sink calls")
    if calls and "suffix" in case and calls[0] != case["suffix"]:
        why.append("sink saw a wrong suffix")
    if _shape(request) != request_before:
        why.append("request changed")
    if isinstance(result, BaseException):
        if _shape(log) != log_before:
            why.append("log changed on rejection")
        if type(result) is not module.CorruptionError or \
                result.code != corruption.FAILURE_MAPPING.get(result.failure_class):
            why.append("untyped failure")
        if forged[0] is not None and result is forged[0]:
            why.append("forged error escaped")
        return result.failure_class, why
    if _pins(log) != kept or _shape(log)[2] != log_before[2][:keep]:
        why.append("verified prefix not kept in place")
    if type(result) is not dict or list(result) != list(FIELDS):
        why.append("receipt not in contract order")
    return result, why


@functools.cache
def _oracle(gen, seed):
    return _outcome(_contract, _case(gen, seed))[0]


def _check(module, gen, seed):
    case = _case(gen, seed)
    got, why = _outcome(module, case)
    if got != case["expect"]:
        why.append(f"got {got!r:.120}, expected {case['expect']!r:.120}")
    if got != _oracle(gen, seed):
        why.append("differs from the contract reference")
    return why


def _all_cases():
    return [(gen, seed) for gen, count in GENERATORS.items() for seed in range(count)]


# -- tests: fuzz -----------------------------------------------------------------

@pytest.mark.parametrize("gen", sorted(GENERATORS))
def test_fuzz_cases_hold(gen):
    bad = {}
    for seed in range(GENERATORS[gen]):
        why = _check(corruption, gen, seed)
        if why:
            bad[_case(gen, seed)["label"]] = why
    assert not bad, bad


def test_determinism_by_value():
    for gen, seed in _all_cases()[::5]:
        a, _ = _outcome(corruption, _case(gen, seed))
        b, _ = _outcome(corruption, _case(gen, seed))
        assert a == b, (gen, seed)


def test_no_poisoning_after_rejections():
    for gen in ("request", "loss", "domain", "sink", "live"):
        for seed in range(GENERATORS[gen]):
            _outcome(corruption, _case(gen, seed))
    ops = (("put", 0), ("put", 1), ("delete", 0))
    log = _torn(ops, "unknown-op", (("put", 2),))
    engine = corruption.CorruptionEngine(corruption.quarantine_suffix)
    suffix = log[3:]
    assert engine.scan(log, {"max_loss": 1}) == _receipt(ops, suffix)
    assert engine.scan(log, {"max_loss": 0}) == _receipt(ops, [])


def test_corpus_reaches_every_class_and_shape():
    cases = [_case(gen, seed) for gen, seed in _all_cases()]
    labels = {case["label"] for case in cases}
    assert {case["expect"] for case in cases if type(case["expect"]) is str} == set(CLASSES)
    assert {case["expect"]["verdict"] for case in cases
            if type(case["expect"]) is dict} == {"clean", "salvaged"}
    for tear in TEARS:
        assert any(label.startswith(f"tear:{tear}:") for label in labels)
    for name in OUT_OF_DOMAIN:
        for where in ("tear", "after-tear", "verified"):
            assert f"domain:out:{name}@{where}" in labels
    for edit in LIVE_EDITS:
        for ending in LIVE_ENDINGS:
            assert f"live:{edit}:{ending}" in labels


def test_every_boundary_error_class_is_forged():
    assert {a for k, a in SINK_FAULTS if k == "forge-corruption"} == \
        set(corruption.FAILURE_MAPPING)
    assert {a for k, a in SINK_FAULTS if k == "forge-wal"} == set(wal.FAILURE_MAPPING)
    # every module-private Exception subclass is forged too
    private = {n for n, v in vars(corruption).items() if n.startswith("_")
               and isinstance(v, type) and issubclass(v, BaseException)}
    assert private == set(PRIVATE_ERRORS)
    assert {a for k, a in SINK_FAULTS if k == "forge-private"} == set(PRIVATE_REASONS)
    forged_raises = {a for k, a in SINK_FAULTS if k == "raise"}
    assert set(HANDLED_BUILTINS) | set(RAISED_BUILTINS) <= forged_raises
    # enumerate EVERY raise site and except clause of the module: each
    # name is forged (own/private/linked classes above, builtins here)
    raised, caught = set(), set()
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            raised.add(ast.unparse(exc))
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            kinds = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            caught |= {ast.unparse(k) for k in kinds}
    assert raised == {"CorruptionError", *PRIVATE_ERRORS, *RAISED_BUILTINS}
    assert caught - {"BaseException"} == {
        "_wal.WalError", *PRIVATE_ERRORS, *HANDLED_BUILTINS}
    assert set(FORGE_TARGETS) <= set(MUTANTS)


# -- mutation check --------------------------------------------------------------

def _source_mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0271_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "corruption.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.CorruptionError = corruption.CorruptionError

    def _fail(failure_class):
        raise corruption.CorruptionError(failure_class,
                                         corruption.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


_BOUNDARY = ("        except BaseException:\n"
             "            _fail(\"divergent_quarantine\")\n"
             "        if type(out) is not str or")


def _passthrough(error, cls):
    guard = "" if cls is None else (
        f"            if error.failure_class != {cls!r}:\n"
        "                _fail(\"divergent_quarantine\")\n")
    return [(_BOUNDARY, f"        except {error} as error:\n" + guard +
             "            raise\n" + _BOUNDARY)]


MUTANTS = {
    "except-exception": [(_BOUNDARY, _BOUNDARY.replace("BaseException:", "Exception:"))],
    "corruption-error-passthrough": _passthrough("CorruptionError", None),
    **{f"corruption-error-passthrough-{c}": _passthrough("CorruptionError", c)
       for c in CLASSES},
    **{f"builtin-passthrough-{e}": [(_BOUNDARY, f"        except {e}:\n"
                                     "            raise\n" + _BOUNDARY)]
       for e in HANDLED_BUILTINS + RAISED_BUILTINS},
    "out-of-domain-passthrough": [(_BOUNDARY, "        except _OutOfDomain:\n"
                                   "            raise\n" + _BOUNDARY)],
    "wal-error-passthrough": _passthrough("_wal.WalError", None),
    **{f"wal-error-passthrough-{c}": _passthrough("_wal.WalError", c)
       for c in WAL_CLASSES},
    "sink-output-isinstance": [("        if type(out) is not str or",
                                "        if not isinstance(out, str) or")],
    "no-token-equality": [("            if token != quarantine_suffix(frozen_suffix):\n",
                           "            if False:\n")],
    "sink-shares-frozen-suffix": [("self.sink(copy.deepcopy(frozen_suffix))",
                                   "self.sink(frozen_suffix)")],
    "no-restore": [("            _restore(saved)\n", "            pass\n")],
    "no-request-restore": [("            request.clear()\n"
                            "            request.update(saved_request)\n", "")],
    "request-isinstance": [("if type(request) is not dict or len(request) != 1",
                            "if not isinstance(request, dict) or len(request) != 1")],
    "no-request-len-check": [(" or len(request) != 1 or", " or")],
    "no-request-key-guard": [("any(type(key) is not str for key in dict.keys(request))",
                              "False")],
    "max-loss-isinstance": [("if type(max_loss) is not int or max_loss < 0:",
                             "if not isinstance(max_loss, int) or max_loss < 0:")],
    "no-max-loss-min": [("if type(max_loss) is not int or max_loss < 0:",
                         "if type(max_loss) is not int:")],
    "loss-bound-inclusive": [("        if lost > max_loss:\n", "        if lost >= max_loss:\n")],
    "no-loss-bound": [("        if lost > max_loss:\n", "        if False:\n")],
    "depth-inclusive": [("    if depth > MAX_DEPTH:\n", "    if depth >= MAX_DEPTH:\n")],
    "bits-inclusive": [("if value.bit_length() > MAX_INT_BITS:",
                        "if value.bit_length() >= MAX_INT_BITS:")],
    "no-alias-check": [("    if id(value) in seen:\n", "    if False:\n")],
    "no-surrogate-check": [('            raw = value.encode("utf-8")\n',
                            '            raw = value.encode("utf-8", "surrogatepass")\n')],
    "bisect-keeps-bad": [("            if self._prefix_head(frozen, mid) is None:\n"
                          "                bad = mid\n",
                          "            if self._prefix_head(frozen, mid) is None:\n"
                          "                good = mid\n")],
    "commit-keeps-one": [("        del log[count:]\n", "        del log[count + 1:]\n")],
    "no-commit": [("        del log[count:]\n", "")],
    "scan-id-swapped-counts": [('f"{verdict}\\n{head}\\n{count}\\n{lost}\\n"',
                                'f"{verdict}\\n{head}\\n{lost}\\n{count}\\n"')],
    "clean-calls-sink": [('            return self._receipt("clean", head, count, 0, None)\n',
                          '            self._quarantine([])\n'
                          '            return self._receipt("clean", head, count, 0, None)\n')],
}

FORGE_TARGETS = {
    "except-exception": "sink:raise:KeyboardInterrupt",
    "corruption-error-passthrough": f"sink:forge-corruption:{CLASSES[0]}",
    **{f"corruption-error-passthrough-{c}": f"sink:forge-corruption:{c}"
       for c in CLASSES},
    "out-of-domain-passthrough": f"sink:forge-private:{PRIVATE_REASONS[0]}",
    **{f"builtin-passthrough-{e}": f"sink:raise:{e}"
       for e in HANDLED_BUILTINS + RAISED_BUILTINS},
    "wal-error-passthrough": f"sink:forge-wal:{WAL_CLASSES[0]}",
    **{f"wal-error-passthrough-{c}": f"sink:forge-wal:{c}" for c in WAL_CLASSES},
}

# one-guard edits no black-box case can separate: byte-exact token equality
# rejects every string the grammar would, with the same class, and the
# grammar rejects every string the output UTF-8 check would; a str-subclass
# key and a list-subclass log still fail the exact-type dispatch in _encode
EQUIVALENT_EDITS = {
    "log-isinstance": [("        if type(log) is not list:\n",
                        "        if not isinstance(log, list):\n")],
    "token-grammar-search": [("_TOKEN_RE.fullmatch(out)", "_TOKEN_RE.search(out)")],
    "no-token-grammar": [(" or _TOKEN_RE.fullmatch(out) is None", "")],
    "no-output-utf8-check": [('            out.encode("utf-8")\n',
                              '            out.encode("utf-8", "surrogatepass")\n')],
    "key-isinstance": [("        if type(key) is not str:\n",
                        "        if not isinstance(key, str):\n")],
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




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _shape(value)
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _shape(value) != before


def test_pins_helper_keeps_replaced_entries():
    """The reference-preservation sites share _pins: a replace-twice engine
    swaps an entry for an equal one, possibly on the freed address, and the
    pinned list still sees the swap."""
    log = [{"a": 1}]
    before = _pins(log)
    log[0] = {"a": 1}
    log[0] = {"a": 1}
    assert _pins(log) != before
