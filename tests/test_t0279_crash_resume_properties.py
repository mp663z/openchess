"""T0279 deterministic unit/property battery for the production crash resume.

Seeded properties over store.crash_resume (the shipped T0278 runtime) only:
no tests.* helpers. Source logs are built with the shipped store.wal from
records made by the shipped graph.node runtime, then torn at a seeded
position. Receipts are checked against the linked WAL replay of the
surviving prefix, graph.diff over an independent put/delete model, an
independent longest-prefix search, and independent quarantine-token and
resume-id derivations.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import random
from pathlib import Path

import pytest

from graph import diff
from graph.node import make_record, record_identity
from store import crash_resume, wal
from tools.crash_resume_contract_lint import MAX_DEPTH, MAX_INT_DIGITS

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
FIELDS = ("resume_id", "head", "state_id", "resumed_count", "discarded_count",
          "quarantine_token")
GENESIS = "wal0:" + "0" * 64
SEEDS = range(40)
Q = crash_resume.quarantine_tail
ResumeError = crash_resume.ResumeError


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
    """Hashes like a real name; while armed (only around the engine call)
    comparing it raises, so any comparison before an exact-str key guard
    escapes raw."""

    armed = False

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __deepcopy__(self, memo):
        return self

    def __eq__(self, other):
        if _Colliding.armed:
            raise RuntimeError("hostile __eq__")
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)


def _armed(call, *args):
    _Colliding.armed = True
    try:
        return call(*args)
    finally:
        _Colliding.armed = False


class _Sink:
    """Honest sink that records every call (deep copy of its argument)."""

    def __init__(self):
        self.calls = []

    def __call__(self, tail):
        self.calls.append(copy.deepcopy(tail))
        return Q(tail)


def _engine(sink=Q):
    return crash_resume.ResumeEngine(sink)


def _ops(rng, low, high):
    return [(rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
            for _ in range(rng.randrange(low, high))]


def _build(ops):
    log, engine = [], wal.WalEngine(wal.canonical_payload)
    for op, index in ops:
        engine.append(log, {"op": op, "payload": {
            "identity": IDENTITIES[index], "record": dict(RECORDS[index])}})
    return log


def _fold(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


def _other_digest(entry):
    index = IDENTITIES.index(entry["payload"]["identity"])
    return RECORDS[(index + 1) % len(RECORDS)]["digest"]


# every tear is WAL-invalid AT its position but canonical JSON, so it and
# everything after it is a quarantinable torn tail
TEARS = {
    "flip-id": lambda e: {**e, "entry_id": _flip(e["entry_id"])},
    "flip-prior": lambda e: {**e, "prior_entry_id": _flip(e["prior_entry_id"])},
    "drop-key": lambda e: {k: v for k, v in e.items() if k != "prior_entry_id"},
    "extra-key": lambda e: {**e, "zz": 1},
    "sequence-shift": lambda e: {**e, "sequence": e["sequence"] + 1},
    "sequence-bool": lambda e: {**e, "sequence": True} if e["sequence"] != 1
    else {**e, "sequence": False},
    "unknown-op": lambda e: {**e, "op": "move"},
    "digest-swap": lambda e: {**e, "payload": {
        "identity": e["payload"]["identity"],
        "record": {**e["payload"]["record"], "digest": _other_digest(e)}}},
    "partial-write": lambda e: json.dumps(e)[:40],
    "none": lambda e: None,
    "empty-dict": lambda e: {},
    "list": lambda e: [e],
    "non-ascii": lambda e: {**e, "op": "\u00e9\U0001d11e"},
}


def _torn(seed):
    """(log, ops_prefix, n): an honest WAL log of n + m entries whose entry
    n (when m > 0) is torn; entries after it stay honest-looking."""
    rng = random.Random(f"torn-{seed}")
    n = rng.randrange(0, 8)
    m = (0, 1, 2, 3)[seed % 4]
    ops = _ops(rng, n + m, n + m + 1)
    log = _build(ops)
    if m:
        kind = sorted(TEARS)[seed // 4 % len(TEARS)]
        log[n] = TEARS[kind](log[n])
    return log, ops[:n], n


def _canon(entry):
    return json.dumps(entry, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _token(tail):
    """Independent quarantine token from data/contracts/crash_resume.yaml."""
    framed = "|".join(["qtn1"] + [f"{len(_canon(e))}:{_canon(e)}" for e in tail])
    return "qtn1:" + hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _resume_id(head, sid, resumed, discarded, token):
    return "rsm1:" + hashlib.sha256(
        f"{head}\n{sid}\n{resumed}\n{discarded}\n{token}".encode()).hexdigest()


def _longest(log):
    """Independent linear search: longest prefix the linked WAL replays."""
    engine = wal.WalEngine(wal.canonical_payload)
    for k in range(len(log), -1, -1):
        try:
            engine.replay(copy.deepcopy(log[:k]))
        except wal.WalError:
            continue
        return k
    raise AssertionError("unreachable")


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


def _shape(value, seen=()):
    """Value, exact type and key order at every level (cycle-safe, NaN-safe)."""
    if id(value) in seen:
        return ("cycle",)
    if type(value) in (dict, _Dict):
        inner = (*seen, id(value))
        return (type(value).__name__, [(type(key).__name__, key, _shape(item, inner))
                                       for key, item in dict.items(value)])
    if type(value) in (list, _List):
        inner = (*seen, id(value))
        return (type(value).__name__, [_shape(item, inner) for item in list.__iter__(value)])
    if type(value) is float and value != value:
        return ("nan",)
    return (type(value).__name__, value)


def _deep_ids(value, seen=()):
    if id(value) in seen:
        return [_Pin(value)]
    if isinstance(value, dict):
        inner = (*seen, id(value))
        return [_Pin(value)] + [x for k, v in dict.items(value)
                              for x in (_Pin(k), *_deep_ids(v, inner))]
    if isinstance(value, list):
        inner = (*seen, id(value))
        return [_Pin(value)] + [x for v in list.__iter__(value) for x in _deep_ids(v, inner)]
    return [_Pin(value)]


def _snap(value):
    return _shape(value), _deep_ids(value)


def _fails(failure, call, *args):
    with pytest.raises(ResumeError) as exc:
        try:
            _armed(call, *args)
        except ResumeError:
            raise
        else:
            raise AssertionError("accepted")
    assert type(exc.value) is ResumeError
    assert exc.value.failure_class == failure
    assert exc.value.code == crash_resume.FAILURE_MAPPING[failure]


def _check(out, ops_prefix, original, n):
    """OUT is the exact receipt for resuming ORIGINAL (a deep copy of the
    input) whose surviving prefix has N entries built from OPS_PREFIX."""
    tail = original[n:]
    replay = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(original[:n]))
    assert list(out) == list(FIELDS)
    assert out["head"] == replay["head"] == (original[n - 1]["entry_id"] if n else GENESIS)
    assert out["state_id"] == replay["state_id"] == diff.state_id(_fold(ops_prefix))
    assert type(out["resumed_count"]) is int and out["resumed_count"] == n
    assert type(out["discarded_count"]) is int and out["discarded_count"] == len(tail)
    assert out["quarantine_token"] == _token(tail) == Q(copy.deepcopy(tail))
    assert out["resume_id"] == _resume_id(out["head"], out["state_id"], n,
                                          len(tail), out["quarantine_token"])


# -- R1: honest resumes ---------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_resume_matches_model_and_derivations(seed):
    log, ops_prefix, n = _torn(seed)
    original = copy.deepcopy(log)
    assert _longest(original) == n
    entries = list(log)
    entry_snaps = [_snap(e) for e in log]
    rng = random.Random(f"ckpt-{seed}")
    request = {"checkpoint_sequence": rng.randrange(0, n + 1)}
    req_before = _snap(request)
    sink = _Sink()
    out = _engine(sink).resume(log, request)
    _check(out, ops_prefix, original, n)
    # exactly one sink call, even for an empty tail, on the canonical tail
    assert sink.calls == [original[n:]]
    # the prefix survives in place by identity; only the tail is removed
    assert len(log) == n and all(a is b for a, b in zip(log, entries[:n], strict=True))
    assert [_snap(e) for e in log] == entry_snaps[:n]
    assert _snap(request) == req_before
    # deterministic by value
    assert _engine().resume(copy.deepcopy(original), dict(request)) == out
    # idempotent: resuming the resumed log discards nothing
    again = _engine(sink).resume(log, {"checkpoint_sequence": n})
    assert again["head"] == out["head"] and again["state_id"] == out["state_id"]
    assert again["resumed_count"] == n and again["discarded_count"] == 0
    assert again["quarantine_token"] == _token([]) and sink.calls[-1] == []
    assert len(log) == n and all(a is b for a, b in zip(log, entries[:n], strict=True))


@pytest.mark.parametrize("seed", range(16))
def test_r2_every_checkpoint_up_to_the_prefix_resumes(seed):
    log, ops_prefix, n = _torn(seed)
    original = copy.deepcopy(log)
    ids = set()
    for checkpoint in range(n + 1):
        work = copy.deepcopy(original)
        out = _engine().resume(work, {"checkpoint_sequence": checkpoint})
        _check(out, ops_prefix, original, n)
        assert work == original[:n]
        ids.add(out["resume_id"])
    # the checkpoint is not part of the receipt
    assert len(ids) == 1
    # past the surviving prefix (acknowledged data lost) or past the log
    for checkpoint in (*range(n + 1, len(original) + 3), 10 ** 30):
        work = copy.deepcopy(original)
        sink = _Sink()
        before = _snap(work)
        _fails("corrupt_source", _engine(sink).resume, work,
               {"checkpoint_sequence": checkpoint})
        assert sink.calls == [] and _snap(work) == before


def test_r2b_empty_log_and_all_torn_log():
    sink = _Sink()
    log = []
    out = _engine(sink).resume(log, {"checkpoint_sequence": 0})
    assert out["head"] == GENESIS and out["state_id"] == diff.state_id({})
    assert (out["resumed_count"], out["discarded_count"]) == (0, 0)
    assert out["quarantine_token"] == _token([]) and sink.calls == [[]]
    assert log == []
    # every entry torn: all quarantined, genesis receipt, different token
    torn = ["{\"entry_", None, {"a": [1, 2.5, "\u00e9"]}]
    out2 = _engine(sink).resume(torn, {"checkpoint_sequence": 0})
    assert torn == [] and out2["head"] == GENESIS
    assert out2["discarded_count"] == 3 and sink.calls[-1] == ["{\"entry_", None,
                                                               {"a": [1, 2.5, "\u00e9"]}]
    assert out2["quarantine_token"] != out["quarantine_token"]
    assert out2["resume_id"] != out["resume_id"]
    # put then delete: empty state under a non-genesis head
    honest = _build([("put", 0), ("delete", 0)])
    out3 = _engine().resume(honest, {"checkpoint_sequence": 2})
    assert out3["state_id"] == diff.state_id({}) and out3["head"] != GENESIS


@pytest.mark.parametrize("seed", range(20))
def test_r3_everything_from_the_first_tear_is_discarded(seed):
    """Honest entries after the tear are discarded too, and the surviving
    prefix is exactly the independent longest-prefix search."""
    rng = random.Random(f"r3-{seed}")
    ops = _ops(rng, 3, 9)
    log = _build(ops)
    n = rng.randrange(0, len(log) - 1)
    log[n] = TEARS[sorted(TEARS)[seed % len(TEARS)]](log[n])
    original = copy.deepcopy(log)
    assert _longest(original) == n
    out = _engine().resume(log, {"checkpoint_sequence": n})
    _check(out, ops[:n], original, n)
    assert out["discarded_count"] == len(ops) - n >= 2
    assert log == original[:n]


def test_r4_quarantine_tail_vectors():
    """Direct vectors on the public token derivation: domain separated,
    length framed (character length), sorted keys, compact separators,
    UTF-8 bytes; order and framing matter."""
    tails = [[], [None], ["a"], [{"b": 1, "a": [True, 1.5, "\u00e9"]}],
             ["\U0001d11e", {"\u00fc": "q\"\\\n"}], [1, 2], [2, 1], ["1|2"], ["1", "2"]]
    tokens = set()
    for tail in tails:
        before = _snap(tail)
        assert Q(tail) == _token(tail)
        assert _snap(tail) == before
        tokens.add(Q(tail))
    assert len(tokens) == len(tails)
    assert Q([]) == "qtn1:" + hashlib.sha256(b"qtn1").hexdigest()
    assert Q(["\u00e9"]) == "qtn1:" + hashlib.sha256(
        "qtn1|3:\"\u00e9\"".encode()).hexdigest()
    assert Q([{"b": 1, "a": 2}]) == Q([{"a": 2, "b": 1}])


@pytest.mark.parametrize("seed", range(12))
def test_r5_sink_argument_is_detached(seed):
    log, ops_prefix, n = _torn(4 * seed + 1 + seed % 3)
    original = copy.deepcopy(log)
    entries = list(log)
    seen = []

    def sink(tail):
        token = Q(tail)
        seen.append([id(e) for e in tail if type(e) in (dict, list)])
        for entry in tail:
            if type(entry) is dict:
                entry["zz"] = 1
                entry.pop("entry_id", None)
            elif type(entry) is list:
                entry.append(1)
        tail.append({"foreign": True})
        tail.reverse()
        return token

    out = _engine(sink).resume(log, {"checkpoint_sequence": n})
    _check(out, ops_prefix, original, n)
    assert log == original[:n] and all(a is b for a, b in zip(log, entries[:n], strict=True))
    assert not set(seen[0]) & {id(e) for e in entries}


# -- R6: sink boundary -------------------------------------------------------------------

def _raiser(kind):
    def sink(tail):
        raise kind()
    return sink


def _forged(error):
    def sink(tail):
        raise error
    return sink


FORGED_ERRORS = {
    **{f"forged-resume-error-{c}": ResumeError(c, crash_resume.FAILURE_MAPPING[c])
       for c in sorted(crash_resume.FAILURE_MAPPING) if c != "divergent_quarantine"},
    "forged-wal-error-corrupt-chain": wal.WalError(
        "corrupt_chain", wal.FAILURE_MAPPING["corrupt_chain"]),
    "forged-wal-error-malformed-entry": wal.WalError(
        "malformed_wal_entry", wal.FAILURE_MAPPING["malformed_wal_entry"]),
}

HOSTILE_SINKS = {
    **{name: _forged(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda tail: None,
    "bytes": lambda tail: Q(tail).encode(),
    "int": lambda tail: 0,
    "list": lambda tail: [Q(tail)],
    "str-subclass": lambda tail: _Str(Q(tail)),
    "upper-hex": lambda tail: Q(tail).upper().replace("QTN1", "qtn1"),
    "trailing-newline": lambda tail: Q(tail) + "\n",
    "leading-space": lambda tail: " " + Q(tail),
    "other-prefix": lambda tail: "qtn2:" + Q(tail)[5:],
    "rsm-token": lambda tail: "rsm1:" + Q(tail)[5:],
    "flipped-token": lambda tail: _flip(Q(tail)),
    "empty-tail-token": lambda tail: Q([]) if tail else Q([None]),
    "shorter-tail-token": lambda tail: Q(tail[:-1]) if tail else Q([None]),
    "longer-tail-token": lambda tail: Q([*tail, None]),
    "reversed-tail-token": lambda tail: Q(tail[::-1]) if len(tail) > 1 else _flip(Q(tail)),
    "ascii-escaped-token": lambda tail: "qtn1:" + hashlib.sha256("|".join(
        ["qtn1"] + [f"{len(json.dumps(e, sort_keys=True))}:{json.dumps(e, sort_keys=True)}"
                    for e in tail]).encode()).hexdigest() if tail else Q([None]),
}


@pytest.mark.parametrize("name", sorted(HOSTILE_SINKS))
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 6, 7])
def test_r6_hostile_sink_fails_closed(name, seed):
    log, _, n = _torn(seed)
    if name == "ascii-escaped-token":
        log.append({"\u00e9": "\U0001d11e"})
    request = {"checkpoint_sequence": n}
    before, req_before = _snap(log), _snap(request)
    _fails("divergent_quarantine", _engine(HOSTILE_SINKS[name]).resume, log, request)
    assert _snap(log) == before and _snap(request) == req_before


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("fail", [False, True])
def test_r7_live_input_mutation_is_undone(seed, fail):
    """A sink closing over the caller's LIVE log and request adds keys at
    every level, changes and removes values and inserts into and deletes
    from both containers; everything is put back (values, key order,
    identity) on both exits, and a success removes exactly the tail."""
    log, ops_prefix, n = _torn(seed)
    log.append({"t": [1, {"u": 2}]})
    request = {"checkpoint_sequence": n}
    original = copy.deepcopy(log)
    entries = list(log)
    entry_snaps = [_snap(e) for e in log]
    before, req_before = _snap(log), _snap(request)

    def sink(tail):
        token = Q(tail)
        for entry in list(log):
            if type(entry) is dict:
                entry["zz"] = 1
                if "payload" in entry and type(entry["payload"]) is dict:
                    entry["payload"]["zz"] = 1
                    record = entry["payload"].get("record")
                    if type(record) is dict:
                        record["zz"] = 1
                        record.pop("digest", None)
                if "t" in entry:
                    entry["t"].append(3)
                    entry["t"][1]["v"] = 4
                entry.pop("sequence", None)
        log.insert(0, {"foreign": True})
        log.reverse()
        del log[-1]
        del request["checkpoint_sequence"]
        request["zz"] = 1
        request["checkpoint_sequence"] = -5
        return None if fail else token

    if fail:
        _fails("divergent_quarantine", _engine(sink).resume, log, request)
        assert _snap(log) == before
    else:
        out = _engine(sink).resume(log, request)
        _check(out, ops_prefix, original, n)
        assert len(log) == n and all(a is b for a, b in zip(log, entries[:n], strict=True))
        assert [_snap(e) for e in log] == entry_snaps[:n]
        # the discarded entries themselves were restored too
        assert [_snap(e) for e in entries[n:]] == entry_snaps[n:]
    assert _snap(request) == req_before


# -- R8: malformed requests and logs -------------------------------------------------

REQUEST_TAMPERS = [
    ("renamed-key", {"checkpoint_sequencx": 0}),
    ("missing-key", {}),
    ("extra-key", {"checkpoint_sequence": 0, "zz": 1}),
    ("extra-non-ascii-key", {"checkpoint_sequence": 0, "\u00e9": 1}),
    ("str-subclass-key", {_Str("checkpoint_sequence"): 0}),
    ("colliding-key", {_Colliding("checkpoint_sequence"): 0}),
    ("colliding-extra-key", {"checkpoint_sequence": 0, _Colliding("zz"): 0}),
    ("dict-subclass", _Dict({"checkpoint_sequence": 0})),
    ("list", [("checkpoint_sequence", 0)]),
    ("none", None),
    ("checkpoint-bool", {"checkpoint_sequence": False}),
    ("checkpoint-true", {"checkpoint_sequence": True}),
    ("checkpoint-int-subclass", {"checkpoint_sequence": _Int(0)}),
    ("checkpoint-float", {"checkpoint_sequence": 0.0}),
    ("checkpoint-str", {"checkpoint_sequence": "0"}),
    ("checkpoint-none", {"checkpoint_sequence": None}),
    ("checkpoint-list", {"checkpoint_sequence": [0]}),
    ("negative-with-extra-key", {"checkpoint_sequence": -1, "zz": 1}),
    ("negative-int-subclass", {"checkpoint_sequence": _Int(-1)}),
]


@pytest.mark.parametrize("name,request_", REQUEST_TAMPERS, ids=[n for n, _ in REQUEST_TAMPERS])
def test_r8_malformed_request_is_rejected_first(name, request_):
    for seed in range(6):
        for corrupt in (False, True):
            log, _, _ = _torn(seed)
            if corrupt:
                log.insert(0, {"bad": float("nan")})
            before, req_before, sink = _snap(log), _snap(request_), _Sink()
            _fails("malformed_resume_record", _engine(sink).resume, log, request_)
            assert sink.calls == [] and _snap(log) == before
            assert _snap(request_) == req_before


@pytest.mark.parametrize("bad", [None, (), {}, "log", 0, _Dict(), _List(), _List([None])])
def test_r8b_non_list_log_is_malformed(bad):
    sink = _Sink()
    _fails("malformed_resume_record", _engine(sink).resume, bad, {"checkpoint_sequence": 0})
    assert sink.calls == []


@pytest.mark.parametrize("checkpoint", [-1, -2, -(10 ** 30)])
def test_r9_negative_checkpoint_is_unknown_before_the_source(checkpoint):
    for seed in range(8):
        for corrupt in (False, True):
            log, _, _ = _torn(seed)
            if corrupt:
                log.insert(0, {"bad": float("nan")})
            request = {"checkpoint_sequence": checkpoint}
            before, sink = _snap(log), _Sink()
            _fails("unknown_checkpoint", _engine(sink).resume, log, request)
            assert sink.calls == [] and _snap(log) == before


# -- R10: admission of the torn tail -------------------------------------------------

def _nested(depth):
    """An entry whose container nesting depth is exactly DEPTH."""
    node = []
    for _ in range(depth - 2):
        node = [node]
    return {"x": node}


def _cycle():
    entry = {"a": []}
    entry["a"].append(entry)
    return entry


def _alias():
    shared = [1]
    return {"a": shared, "b": shared}


ADMISSIBLE = {
    "depth-max": lambda: _nested(MAX_DEPTH),
    "int-max-digits": lambda: {"n": 10 ** (MAX_INT_DIGITS - 1)},
    "negative-int-max-digits": lambda: {"n": -(10 ** MAX_INT_DIGITS - 1)},
    "finite-floats": lambda: {"f": [1.5, -0.0, 1e308]},
    "multi-byte": lambda: {"\u00e9": "\U0001d11e"},
    "scalars": lambda: [None, True, False, 0, ""],
    "bare-string": lambda: "partial",
}

INADMISSIBLE = {
    "depth-over": lambda: _nested(MAX_DEPTH + 1),
    "int-over-digits": lambda: {"n": 10 ** MAX_INT_DIGITS},
    "negative-int-over-digits": lambda: {"n": -(10 ** MAX_INT_DIGITS)},
    "huge-int": lambda: {"n": 2 ** 20000},
    "nan": lambda: {"f": float("nan")},
    "inf": lambda: {"f": float("-inf")},
    "surrogate-value": lambda: {"s": "\ud800"},
    "surrogate-key": lambda: {"\ud800": 1},
    "int-key": lambda: {1: 2},
    "str-subclass-key": lambda: {_Str("k"): 1},
    "colliding-key": lambda: {_Colliding("op"): 1},
    "str-subclass-value": lambda: {"s": _Str("v")},
    "int-subclass-value": lambda: {"n": _Int(1)},
    "float-subclass-value": lambda: {"f": _Float(1.5)},
    "bare-float-subclass": lambda: _Float(1.5),
    "dict-subclass": lambda: _Dict({"a": 1}),
    "list-subclass": lambda: {"a": _List([1])},
    "tuple": lambda: {"t": (1,)},
    "bytes": lambda: {"b": b"x"},
    "set": lambda: {"s": {1}},
    "object": lambda: {"o": object()},
    "alias": _alias,
    "cycle": _cycle,
    "bare-bytes": lambda: b"torn",
}


@pytest.mark.parametrize("name", sorted(ADMISSIBLE))
def test_r10_admissible_tail_entries_are_quarantined(name):
    for seed in range(4):
        log, ops_prefix, n = _torn(seed)
        log.append(ADMISSIBLE[name]())
        original = copy.deepcopy(log)
        sink = _Sink()
        out = _engine(sink).resume(log, {"checkpoint_sequence": n})
        _check(out, ops_prefix, original, n)
        assert sink.calls == [original[n:]] and log == original[:n]


@pytest.mark.parametrize("name", sorted(INADMISSIBLE))
def test_r10b_inadmissible_tail_entry_is_malformed(name):
    """Anywhere in the torn tail - as the tear itself or after it - an
    inadmissible entry fails typed before the sink, log untouched."""
    for seed in range(8):
        log, _, n = _torn(seed)
        where = n if seed % 2 else len(log)
        log.insert(where, INADMISSIBLE[name]())
        before, sink = _snap(log), _Sink()
        _fails("malformed_resume_record", _engine(sink).resume, log,
               {"checkpoint_sequence": min(n, where)})
        assert sink.calls == [] and _snap(log) == before


def test_r10c_admission_inside_an_honest_entry_caps_the_prefix():
    """A honest-looking entry carrying an inadmissible value never reaches
    the WAL: the prefix stops before it, it is corrupt when acknowledged
    and malformed when torn."""
    log = _build([("put", 0), ("put", 1), ("put", 2)])
    log[1]["payload"]["record"]["zz"] = float("nan")
    for checkpoint, failure in ((2, "corrupt_source"), (3, "corrupt_source"),
                                (1, "malformed_resume_record"),
                                (0, "malformed_resume_record")):
        work = copy.deepcopy(log)
        before, sink = _snap(work), _Sink()
        _fails(failure, _engine(sink).resume, work, {"checkpoint_sequence": checkpoint})
        assert sink.calls == [] and _snap(work) == before


@pytest.mark.parametrize("name", sorted(INADMISSIBLE))
def test_r10d_the_linked_wal_rejects_every_inadmissible_value(name):
    """Why the admission cap never changes the prefix: embedded anywhere in
    an honest entry, every inadmissible value is also rejected by the
    linked WAL itself, typed and never raw."""
    spots = (
        lambda e, v: e.__setitem__("zz", v),
        lambda e, v: e["payload"].__setitem__("zz", v),
        lambda e, v: e["payload"]["record"].__setitem__("digest", v),
        lambda e, v: e["payload"].__setitem__("record", v),
        lambda e, v: e.__setitem__("prior_entry_id", v),
    )
    for spot in spots:
        log = _build([("put", 0), ("put", 1)])
        spot(log[1], INADMISSIBLE[name]())
        with pytest.raises(wal.WalError):
            _armed(wal.WalEngine(wal.canonical_payload).replay, log)
        work = copy.deepcopy(log)
        before = _snap(work)
        _fails("corrupt_source", _engine(_Sink()).resume, work, {"checkpoint_sequence": 2})
        assert _snap(work) == before


# -- R11: corrupt source -----------------------------------------------------------------

@pytest.mark.parametrize("tear", sorted(TEARS))
def test_r11_a_tear_at_or_before_the_checkpoint_is_corrupt(tear):
    for seed in range(8):
        rng = random.Random(f"r11-{seed}")
        log = _build(_ops(rng, 1, 8))
        position = rng.randrange(len(log))
        log[position] = TEARS[tear](log[position])
        for checkpoint in range(position + 1, len(log) + 1):
            work = copy.deepcopy(log)
            before, sink = _snap(work), _Sink()
            _fails("corrupt_source", _engine(sink).resume, work,
                   {"checkpoint_sequence": checkpoint})
            assert sink.calls == [] and _snap(work) == before


def test_r12_property_file_uses_no_test_helpers():
    tree = ast.parse(Path(__file__).read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)
    assert modules <= {"__future__", "ast", "copy", "hashlib", "json", "random",
                       "pathlib", "pytest", "graph", "graph.node", "store",
                       "tools.crash_resume_contract_lint"}




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _snap(value)
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _snap(value) != before
