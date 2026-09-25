"""T0270 deterministic unit/property battery for the production corruption scan.

Seeded properties over store.corruption (the shipped T0269 runtime) only: no
tests.* helpers. Source logs are built with the shipped store.wal from
records made by the shipped graph.node runtime, then corrupted at a seeded
position. Receipts are checked against the linked WAL replay of the
verified prefix, graph.diff over an independent put/delete model, an
independent linear longest-prefix search, and independent canonical
encoding, quarantine-token and scan-id derivations.
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
from store import corruption, wal
from tools.corruption_contract_lint import MAX_DEPTH, MAX_INT_BITS

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
FIELDS = (
    "scan_id",
    "verdict",
    "verified_head",
    "verified_count",
    "quarantined_count",
    "quarantine_token",
)
GENESIS = "wal0:" + "0" * 64
SEEDS = range(40)
Q = corruption.quarantine_suffix
CorruptionError = corruption.CorruptionError


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

    def __call__(self, suffix):
        self.calls.append(copy.deepcopy(suffix))
        return Q(suffix)


def _engine(sink=Q):
    return corruption.CorruptionEngine(sink)


def _ops(rng, low, high):
    return [
        (rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
        for _ in range(rng.randrange(low, high))
    ]


def _build(ops):
    log, engine = [], wal.WalEngine(wal.canonical_payload)
    for op, index in ops:
        engine.append(
            log,
            {"op": op, "payload": {"identity": IDENTITIES[index], "record": dict(RECORDS[index])}},
        )
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


# every corruption is WAL-invalid AT its position but inside the scan
# domain, so it and everything after it is a quarantinable suffix
CORRUPTIONS = {
    "flip-id": lambda e: {**e, "entry_id": _flip(e["entry_id"])},
    "flip-prior": lambda e: {**e, "prior_entry_id": _flip(e["prior_entry_id"])},
    "drop-key": lambda e: {k: v for k, v in e.items() if k != "prior_entry_id"},
    "extra-key": lambda e: {**e, "zz": 1},
    "sequence-shift": lambda e: {**e, "sequence": e["sequence"] + 1},
    "sequence-bool": lambda e: (
        {**e, "sequence": True} if e["sequence"] != 1 else {**e, "sequence": False}
    ),
    "unknown-op": lambda e: {**e, "op": "move"},
    "digest-swap": lambda e: {
        **e,
        "payload": {
            "identity": e["payload"]["identity"],
            "record": {**e["payload"]["record"], "digest": _other_digest(e)},
        },
    },
    "partial-write": lambda e: json.dumps(e)[:40],
    "none": lambda e: None,
    "empty-dict": lambda e: {},
    "list": lambda e: [e],
    "non-ascii": lambda e: {**e, "op": "\u00e9\U0001d11e"},
}


def _corrupt(seed):
    """(log, ops_prefix, n): an honest WAL log of n + m entries whose entry
    n (when m > 0) is corrupt; entries after it stay honest-looking."""
    rng = random.Random(f"corrupt-{seed}")
    n = rng.randrange(0, 8)
    m = (0, 1, 2, 3)[seed % 4]
    ops = _ops(rng, n + m, n + m + 1)
    log = _build(ops)
    if m:
        kind = sorted(CORRUPTIONS)[seed // 4 % len(CORRUPTIONS)]
        log[n] = CORRUPTIONS[kind](log[n])
    return log, ops[:n], n


def _enc(value):
    """Independent type-tagged, length-framed canonical encoding from
    data/contracts/corruption.yaml."""
    if value is None:
        return b"n"
    if type(value) is bool:
        return b"t" if value else b"f"
    if type(value) is int:
        text = str(value).encode()
        return b"i" + str(len(text)).encode() + b":" + text
    if type(value) is str:
        raw = value.encode("utf-8")
        return b"s" + str(len(raw)).encode() + b":" + raw
    if type(value) is list:
        return b"l" + str(len(value)).encode() + b":" + b"".join(_enc(v) for v in value)
    assert type(value) is dict
    keys = sorted(value)
    return b"d" + str(len(keys)).encode() + b":" + b"".join(_enc(k) + _enc(value[k]) for k in keys)


def _token(suffix):
    return "qrn1:" + hashlib.sha256(b"qrn1|" + _enc(list(suffix))).hexdigest()


def _scan_id(verdict, head, count, lost, token):
    return (
        "crp1:"
        + hashlib.sha256(
            f"{verdict}\n{head}\n{count}\n{lost}\n{'-' if token is None else token}".encode()
        ).hexdigest()
    )


def _longest(log):
    """Independent LINEAR search: longest prefix the linked WAL replays."""
    engine = wal.WalEngine(wal.canonical_payload)
    for k in range(len(log), -1, -1):
        try:
            engine.replay(copy.deepcopy(log[:k]))
        except wal.WalError:
            continue
        return k
    raise AssertionError("unreachable")


def _shape(value, seen=()):
    """Value, exact type and key order at every level (cycle-safe)."""
    if id(value) in seen:
        return ("cycle",)
    if type(value) in (dict, _Dict):
        inner = (*seen, id(value))
        return (
            type(value).__name__,
            [(type(key).__name__, key, _shape(item, inner)) for key, item in dict.items(value)],
        )
    if type(value) in (list, _List):
        inner = (*seen, id(value))
        return (type(value).__name__, [_shape(item, inner) for item in list.__iter__(value)])
    if type(value) is float and value != value:
        return ("nan",)
    return (type(value).__name__, value)


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


def _deep_ids(value, seen=()):
    if id(value) in seen:
        return [_Pin(value)]
    if isinstance(value, dict):
        inner = (*seen, id(value))
        return [_Pin(value)] + [
            x for k, v in dict.items(value) for x in (_Pin(k), *_deep_ids(v, inner))
        ]
    if isinstance(value, list):
        inner = (*seen, id(value))
        return [_Pin(value)] + [x for v in list.__iter__(value) for x in _deep_ids(v, inner)]
    return [_Pin(value)]


def _snap(value):
    return _shape(value), _deep_ids(value)


def _fails(failure, call, *args):
    with pytest.raises(CorruptionError) as exc:
        try:
            _armed(call, *args)
        except CorruptionError:
            raise
        else:
            raise AssertionError("accepted")
    assert type(exc.value) is CorruptionError
    assert exc.value.failure_class == failure
    assert exc.value.code == corruption.FAILURE_MAPPING[failure]


def _check(out, ops_prefix, original, n):
    """OUT is the exact receipt for scanning ORIGINAL (a deep copy of the
    input) whose verified prefix has N entries built from OPS_PREFIX."""
    suffix = original[n:]
    replay = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(original[:n]))
    assert list(out) == list(FIELDS)
    assert out["verified_head"] == replay["head"] == (original[n - 1]["entry_id"] if n else GENESIS)
    assert replay["state_id"] == diff.state_id(_fold(ops_prefix))
    assert type(out["verified_count"]) is int and out["verified_count"] == n
    assert type(out["quarantined_count"]) is int and out["quarantined_count"] == len(suffix)
    if suffix:
        assert out["verdict"] == "salvaged"
        assert out["quarantine_token"] == _token(suffix) == Q(copy.deepcopy(suffix))
    else:
        assert out["verdict"] == "clean" and out["quarantine_token"] is None
    assert out["scan_id"] == _scan_id(
        out["verdict"], out["verified_head"], n, len(suffix), out["quarantine_token"]
    )
    assert out["scan_id"] == corruption.derive_scan_id(
        out["verdict"], out["verified_head"], n, len(suffix), out["quarantine_token"]
    )


# -- R2/R3: honest scans ----------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r1_scan_matches_model_and_derivations(seed):
    log, ops_prefix, n = _corrupt(seed)
    original = copy.deepcopy(log)
    assert _longest(original) == n
    entries = list(log)
    entry_snaps = [_snap(e) for e in log]
    lost = len(original) - n
    rng = random.Random(f"loss-{seed}")
    request = {"max_loss": lost + rng.randrange(0, 3)}
    req_before = _snap(request)
    sink = _Sink()
    out = _engine(sink).scan(log, request)
    _check(out, ops_prefix, original, n)
    # one sink call on the frozen suffix per salvage, none when clean
    assert sink.calls == ([original[n:]] if lost else [])
    # the prefix survives in place by identity; only the suffix is removed
    assert len(log) == n and all(a is b for a, b in zip(log, entries[:n], strict=True))
    assert [_snap(e) for e in log] == entry_snaps[:n]
    assert _snap(request) == req_before
    # deterministic by value
    assert _engine().scan(copy.deepcopy(original), dict(request)) == out
    # a salvaged log scans clean, with the same head, and no sink call
    again = _engine(sink).scan(log, {"max_loss": 0})
    assert again["verdict"] == "clean" and again["quarantine_token"] is None
    assert again["verified_head"] == out["verified_head"]
    assert again["verified_count"] == n and again["quarantined_count"] == 0
    assert len(sink.calls) == (1 if lost else 0)
    assert len(log) == n and all(a is b for a, b in zip(log, entries[:n], strict=True))


@pytest.mark.parametrize("seed", range(20))
def test_r2_everything_from_the_first_corrupt_entry_is_quarantined(seed):
    """Honest entries after the corrupt one are quarantined too, and the
    verified prefix (binary search in production) equals a linear search."""
    rng = random.Random(f"r2-{seed}")
    ops = _ops(rng, 3, 10)
    log = _build(ops)
    n = rng.randrange(0, len(log) - 1)
    log[n] = CORRUPTIONS[sorted(CORRUPTIONS)[seed % len(CORRUPTIONS)]](log[n])
    # a second corruption later never moves the prefix
    if seed % 2 and n + 2 < len(log):
        log[n + 2] = None
    original = copy.deepcopy(log)
    assert _longest(original) == n
    out = _engine().scan(log, {"max_loss": len(ops)})
    _check(out, ops[:n], original, n)
    assert out["quarantined_count"] == len(ops) - n >= 2
    assert log == original[:n]


def test_r2b_empty_log_all_corrupt_log_and_emptied_state():
    sink = _Sink()
    log = []
    out = _engine(sink).scan(log, {"max_loss": 0})
    assert out["verdict"] == "clean" and out["verified_head"] == GENESIS
    assert (out["verified_count"], out["quarantined_count"]) == (0, 0)
    assert out["quarantine_token"] is None and sink.calls == [] and log == []
    assert out["scan_id"] == _scan_id("clean", GENESIS, 0, 0, None)
    bad = ['{"entry_', None, {"a": [1, True, "\u00e9"]}]
    out2 = _engine(sink).scan(bad, {"max_loss": 3})
    assert bad == [] and out2["verified_head"] == GENESIS and out2["verdict"] == "salvaged"
    assert out2["quarantined_count"] == 3
    assert sink.calls == [['{"entry_', None, {"a": [1, True, "\u00e9"]}]]
    honest = _build([("put", 0), ("delete", 0)])
    out3 = _engine().scan(honest, {"max_loss": 0})
    assert out3["verdict"] == "clean" and out3["verified_head"] == honest[-1]["entry_id"]
    assert out3["scan_id"] != out["scan_id"]


# -- R4: loss bound ------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(24))
def test_r3_loss_bound_is_inclusive(seed):
    log, ops_prefix, n = _corrupt(seed)
    original = copy.deepcopy(log)
    lost = len(original) - n
    for max_loss in (lost, lost + 1, 10**30):
        work = copy.deepcopy(original)
        _check(_engine().scan(work, {"max_loss": max_loss}), ops_prefix, original, n)
    for max_loss in range(0, lost):
        work = copy.deepcopy(original)
        before, sink = _snap(work), _Sink()
        request = {"max_loss": max_loss}
        _fails("excessive_loss", _engine(sink).scan, work, request)
        assert sink.calls == [] and _snap(work) == before
        assert request == {"max_loss": max_loss}


# -- R5: public encoding and derivations --------------------------------------------

ENCODABLE = [
    None,
    True,
    False,
    0,
    -1,
    2**MAX_INT_BITS - 1,
    -(2**MAX_INT_BITS - 1),
    "",
    "\u00e9",
    "\U0001d11e",
    'q"\\\n',
    [],
    {},
    [1, [2, [3]]],
    {"b": 1, "a": [None, "x"], "\u00e9": {"z": False}, "B": 2},
]


def test_r4_canonical_encoding_vectors():
    for value in ENCODABLE:
        before = _snap(value)
        copied, raw = corruption.canonical_encoding(value)
        assert raw == _enc(value)
        assert copied == value and type(copied) is type(value)
        if type(value) is dict:
            assert list(copied) == sorted(value)
        if type(value) in (list, dict):
            assert copied is not value
        assert _snap(value) == before
    assert corruption.canonical_encoding({"b": 1, "a": 2})[1] == b"d2:s1:ai1:2s1:bi1:1"
    assert corruption.canonical_encoding("\u00e9")[1] == b"s2:\xc3\xa9"
    assert corruption.canonical_encoding([True, None, -12])[1] == b"l3:tni3:-12"
    # distinct values never share an encoding
    raws = [
        corruption.canonical_encoding(v)[1]
        for v in (["1", "2"], ["12"], [1, 2], ["1|2"], "1", 1, True, [[]], [[], []])
    ]
    assert len(set(raws)) == len(raws)


def test_r4b_quarantine_suffix_and_scan_id_vectors():
    suffixes = [
        [None],
        ["a"],
        [{"b": 1, "a": [True, "\u00e9"]}],
        ["\U0001d11e", {"\u00fc": 'q"\\\n'}],
        [1, 2],
        [2, 1],
        ["1", "2"],
        ["12"],
    ]
    tokens = set()
    for suffix in suffixes:
        before = _snap(suffix)
        assert Q(suffix) == _token(suffix)
        assert _snap(suffix) == before
        tokens.add(Q(suffix))
    assert len(tokens) == len(suffixes)
    assert Q([None]) == "qrn1:" + hashlib.sha256(b"qrn1|l1:n").hexdigest()
    assert Q(("a",)) == Q(["a"])
    head = "wal1:" + "a" * 64
    for args in (
        ("clean", GENESIS, 0, 0, None),
        ("salvaged", head, 3, 2, Q(["a"])),
        ("salvaged", head, 3, 2, Q(["b"])),
    ):
        assert corruption.derive_scan_id(*args) == _scan_id(*args)
    assert corruption.derive_scan_id("clean", head, 1, 0, None) != corruption.derive_scan_id(
        "clean", head, 1, 0, "-x"
    )


@pytest.mark.parametrize("seed", range(12))
def test_r5_sink_argument_is_detached(seed):
    log, ops_prefix, n = _corrupt(4 * seed + 1 + seed % 3)
    original = copy.deepcopy(log)
    entries = list(log)
    seen = []

    def sink(suffix):
        token = Q(suffix)
        seen.append([id(e) for e in suffix if type(e) in (dict, list)])
        for entry in suffix:
            if type(entry) is dict:
                entry["zz"] = 1
                entry.pop("entry_id", None)
            elif type(entry) is list:
                entry.append(1)
        suffix.append({"foreign": True})
        suffix.reverse()
        return token

    out = _engine(sink).scan(log, {"max_loss": len(original)})
    _check(out, ops_prefix, original, n)
    assert log == original[:n]
    assert all(a is b for a, b in zip(log, entries[:n], strict=True))
    assert not set(seen[0]) & {id(e) for e in entries}


# -- R6: sink boundary -------------------------------------------------------------------


def _raiser(kind):
    def sink(suffix):
        raise kind()

    return sink


def _forged(error):
    def sink(suffix):
        raise error

    return sink


FORGED_ERRORS = {
    **{
        f"forged-corruption-error-{c}": CorruptionError(c, corruption.FAILURE_MAPPING[c])
        for c in sorted(corruption.FAILURE_MAPPING)
        if c != "divergent_quarantine"
    },
    **{
        f"forged-wal-error-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
        for c in sorted(wal.FAILURE_MAPPING)
    },
}

HOSTILE_SINKS = {
    **{name: _forged(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda suffix: None,
    "bytes": lambda suffix: Q(suffix).encode(),
    "int": lambda suffix: 0,
    "list": lambda suffix: [Q(suffix)],
    "str-subclass": lambda suffix: _Str(Q(suffix)),
    "upper-hex": lambda suffix: "qrn1:" + Q(suffix)[5:].upper(),
    "trailing-newline": lambda suffix: Q(suffix) + "\n",
    "leading-space": lambda suffix: " " + Q(suffix),
    "surrogate": lambda suffix: Q(suffix)[:-1] + "\ud800",
    "other-prefix": lambda suffix: "qtn1:" + Q(suffix)[5:],
    "scan-id-prefix": lambda suffix: "crp1:" + Q(suffix)[5:],
    "flipped-token": lambda suffix: _flip(Q(suffix)),
    "shorter-suffix-token": lambda suffix: Q(suffix[:-1]),
    "longer-suffix-token": lambda suffix: Q([*suffix, None]),
    "reversed-suffix-token": lambda suffix: (
        Q(suffix[::-1]) if len(suffix) > 1 else _flip(Q(suffix))
    ),
    "json-token": lambda suffix: (
        "qrn1:" + hashlib.sha256(b"qrn1|" + json.dumps(suffix, sort_keys=True).encode()).hexdigest()
    ),
    "undomained-token": lambda suffix: "qrn1:" + hashlib.sha256(_enc(list(suffix))).hexdigest(),
}


@pytest.mark.parametrize("name", sorted(HOSTILE_SINKS))
@pytest.mark.parametrize("seed", [1, 2, 3, 5, 6, 7])
def test_r6_hostile_sink_fails_closed(name, seed):
    log, _, n = _corrupt(seed)
    assert len(log) > n
    request = {"max_loss": len(log)}
    before, req_before = _snap(log), _snap(request)
    _fails("divergent_quarantine", _engine(HOSTILE_SINKS[name]).scan, log, request)
    assert _snap(log) == before and _snap(request) == req_before


@pytest.mark.parametrize("name", sorted(HOSTILE_SINKS))
def test_r6b_a_clean_scan_never_calls_the_sink(name):
    for seed in (0, 4, 8, 12):
        log, ops_prefix, n = _corrupt(seed)
        original = copy.deepcopy(log)
        before = _snap(log)
        out = _engine(HOSTILE_SINKS[name]).scan(log, {"max_loss": 0})
        _check(out, ops_prefix, original, n)
        assert _snap(log) == before


@pytest.mark.parametrize("seed", [s for s in range(16) if s % 4])
@pytest.mark.parametrize("fail", [False, True])
def test_r7_live_input_mutation_is_undone(seed, fail):
    """A sink closing over the caller's LIVE log and request adds keys at
    every level, changes and removes values and inserts into, reverses and
    deletes from both containers; everything is put back (values, key
    order, identity) on both exits, and a salvage removes exactly the
    suffix."""
    log, ops_prefix, n = _corrupt(seed)
    log.append({"t": [1, {"u": 2}]})
    request = {"max_loss": len(log)}
    original = copy.deepcopy(log)
    entries = list(log)
    entry_snaps = [_snap(e) for e in log]
    before, req_before = _snap(log), _snap(request)

    def sink(suffix):
        token = Q(suffix)
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
        del request["max_loss"]
        request["zz"] = 1
        request["max_loss"] = -5
        return None if fail else token

    if fail:
        _fails("divergent_quarantine", _engine(sink).scan, log, request)
        assert _snap(log) == before
    else:
        out = _engine(sink).scan(log, request)
        _check(out, ops_prefix, original, n)
        assert len(log) == n
        assert all(a is b for a, b in zip(log, entries[:n], strict=True))
        assert [_snap(e) for e in log] == entry_snaps[:n]
        # the quarantined entries themselves were restored too
        assert [_snap(e) for e in entries[n:]] == entry_snaps[n:]
    assert _snap(request) == req_before


# -- R8: malformed requests and logs -------------------------------------------------

REQUEST_TAMPERS = [
    ("renamed-key", {"max_losx": 0}),
    ("missing-key", {}),
    ("extra-key", {"max_loss": 0, "zz": 1}),
    ("extra-non-ascii-key", {"max_loss": 0, "\u00e9": 1}),
    ("str-subclass-key", {_Str("max_loss"): 0}),
    ("colliding-key", {_Colliding("max_loss"): 0}),
    ("colliding-extra-key", {"max_loss": 0, _Colliding("zz"): 0}),
    ("dict-subclass", _Dict({"max_loss": 0})),
    ("list", [("max_loss", 0)]),
    ("none", None),
    ("max-loss-negative", {"max_loss": -1}),
    ("max-loss-huge-negative", {"max_loss": -(10**30)}),
    ("max-loss-bool", {"max_loss": False}),
    ("max-loss-true", {"max_loss": True}),
    ("max-loss-int-subclass", {"max_loss": _Int(5)}),
    ("max-loss-float", {"max_loss": 5.0}),
    ("max-loss-str", {"max_loss": "5"}),
    ("max-loss-none", {"max_loss": None}),
    ("max-loss-list", {"max_loss": [5]}),
]


@pytest.mark.parametrize("name,request_", REQUEST_TAMPERS, ids=[n for n, _ in REQUEST_TAMPERS])
def test_r8_malformed_request_is_rejected_first(name, request_):
    for seed in range(6):
        for bad_log in (False, True):
            log, _, _ = _corrupt(seed)
            if bad_log:
                log.append({"f": 1.5})
            before, req_before, sink = _snap(log), _snap(request_), _Sink()
            _fails("malformed_corruption_record", _engine(sink).scan, log, request_)
            assert sink.calls == [] and _snap(log) == before
            assert _snap(request_) == req_before


@pytest.mark.parametrize("bad", [None, (), {}, "log", 0, _Dict(), _List(), _List([None])])
def test_r8b_non_list_log_is_malformed(bad):
    sink = _Sink()
    _fails("malformed_corruption_record", _engine(sink).scan, bad, {"max_loss": 5})
    assert sink.calls == []


# -- R9: the scan domain ------------------------------------------------------------


def _deep(total):
    """An entry whose deepest node sits at depth TOTAL (the log is depth 1)."""
    node = 0
    for _ in range(total - 3):
        node = [node]
    return {"x": node}


def _deep_key(total):
    node = {"k": 0}
    for _ in range(total - 4):
        node = {"k": node}
    return {"x": node}


def _cycle():
    entry = {"a": []}
    entry["a"].append(entry)
    return entry


def _alias():
    shared = [1]
    return {"a": shared, "b": shared}


IN_DOMAIN = {
    "depth-max": lambda: _deep(MAX_DEPTH),
    "key-depth-max": lambda: _deep_key(MAX_DEPTH),
    "int-max-bits": lambda: {"n": 2**MAX_INT_BITS - 1},
    "negative-int-max-bits": lambda: {"n": -(2**MAX_INT_BITS - 1)},
    "multi-byte": lambda: {"\u00e9": "\U0001d11e"},
    "scalars": lambda: [None, True, False, 0, ""],
    "bare-string": lambda: "partial",
    "bare-int": lambda: -7,
}

OUT_OF_DOMAIN = {
    "depth-over": lambda: _deep(MAX_DEPTH + 1),
    "key-depth-over": lambda: _deep_key(MAX_DEPTH + 1),
    "int-over-bits": lambda: {"n": 2**MAX_INT_BITS},
    "negative-int-over-bits": lambda: {"n": -(2**MAX_INT_BITS)},
    "huge-int": lambda: {"n": 2**20000},
    "float": lambda: {"f": 1.5},
    "integral-float": lambda: {"f": 1.0},
    "nan": lambda: {"f": float("nan")},
    "bare-float": lambda: 0.0,
    "surrogate-value": lambda: {"s": "\ud800"},
    "surrogate-key": lambda: {"\ud800": 1},
    "int-key": lambda: {1: 2},
    "none-key": lambda: {None: 2},
    "str-subclass-key": lambda: {_Str("k"): 1},
    "colliding-key": lambda: {_Colliding("op"): 1},
    "str-subclass-value": lambda: {"s": _Str("v")},
    "int-subclass-value": lambda: {"n": _Int(1)},
    "float-subclass-value": lambda: {"f": _Float(1.5)},
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


@pytest.mark.parametrize("name", sorted(IN_DOMAIN))
def test_r9_in_domain_suffix_entries_are_quarantined(name):
    for seed in range(4):
        log, ops_prefix, n = _corrupt(seed)
        log.append(IN_DOMAIN[name]())
        original = copy.deepcopy(log)
        sink = _Sink()
        out = _engine(sink).scan(log, {"max_loss": len(log)})
        _check(out, ops_prefix, original, n)
        assert sink.calls == [original[n:]] and log == original[:n]


@pytest.mark.parametrize("name", sorted(OUT_OF_DOMAIN))
def test_r9b_out_of_domain_value_anywhere_is_malformed(name):
    """Anywhere in the log - inside a verified entry, as the corrupt entry,
    after it, or at the end - an out-of-domain value fails typed before
    the loss check and the sink, log untouched."""
    for seed in range(8):
        log, _, n = _corrupt(seed)
        where = (0, n, len(log), max(n - 1, 0))[seed % 4]
        log.insert(where, OUT_OF_DOMAIN[name]())
        for max_loss in (0, len(log)):
            before, sink = _snap(log), _Sink()
            _fails("malformed_corruption_record", _engine(sink).scan, log, {"max_loss": max_loss})
            assert sink.calls == [] and _snap(log) == before


def test_r9c_out_of_domain_inside_an_honest_entry_and_across_entries():
    for spot in ("record", "payload", "entry"):
        log = _build([("put", 0), ("put", 1), ("put", 2)])
        target = {
            "record": log[1]["payload"]["record"],
            "payload": log[1]["payload"],
            "entry": log[1],
        }[spot]
        target["zz"] = 1.5
        before = _snap(log)
        _fails("malformed_corruption_record", _engine(_Sink()).scan, log, {"max_loss": 3})
        assert _snap(log) == before
    # an alias ACROSS entries is outside the domain too
    log = _build([("put", 0), ("put", 1)])
    log.append({"a": log[0]["payload"]})
    before = _snap(log)
    _fails("malformed_corruption_record", _engine(_Sink()).scan, log, {"max_loss": 3})
    assert _snap(log) == before
    # a shared record between two verified entries
    log = _build([("put", 0), ("put", 0)])
    log[1]["payload"]["record"] = log[0]["payload"]["record"]
    _fails("malformed_corruption_record", _engine(_Sink()).scan, log, {"max_loss": 3})


def test_r10_property_file_uses_no_test_helpers():
    tree = ast.parse(Path(__file__).read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)
    assert modules <= {
        "__future__",
        "ast",
        "copy",
        "hashlib",
        "json",
        "random",
        "pathlib",
        "pytest",
        "graph",
        "graph.node",
        "store",
        "tools.corruption_contract_lint",
    }


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original leaf and
    the second can land on its freed address, which a bare id() would
    miss. The fingerprint pins the original, so the swap goes red."""
    value = {"k": [1]}
    before = _snap(value)
    value["k"] = [1]
    value["k"] = [1]
    assert _snap(value) != before
