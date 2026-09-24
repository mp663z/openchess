"""T0261 deterministic unit/property battery for the production idempotency.

Seeded properties over store.idempotency (the shipped T0260 runtime)
only: no tests.* helpers. Logs and entries come from the shipped
store.wal over records made by the shipped graph.node runtime; outcomes,
receipts, logs and ledgers are checked against an independent key-then-
fingerprint model, an independent fingerprint derivation and an
independent receipt-id derivation.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import random
from pathlib import Path

import pytest

from graph.node import make_record, record_identity
from store import idempotency as idem
from store import wal

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
RECEIPT_FIELDS = ("receipt_id", "idempotency_key", "request_fingerprint",
                  "entry_id", "sequence")
KEYS = ("k1", "order:42", "A.b-c_d", "z" * 128, "9")
SEEDS = range(40)


class _Str(str):
    pass


class _Int(int):
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


def _engine(fingerprinter=idem.request_fingerprint):
    return idem.IdempotencyEngine(fingerprinter)


def _payload(index):
    return {"identity": IDENTITIES[index], "record": dict(RECORDS[index])}


def _request(key, op, index):
    return {"idempotency_key": key, "op": op, "payload": _payload(index)}


def _fp(op, payload):
    """Independent request fingerprint from data/contracts/idempotency.yaml."""
    r = payload["record"]
    parts = ["idf1"] + [f"{len(str(v))}:{v}" for v in (
        op, payload["identity"], r["variant"], r["digest"], r["snapshot_fen"])]
    return "idf1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _rid(key, fp, entry_id, seq):
    return "idr1:" + hashlib.sha256(f"{key}\n{fp}\n{entry_id}\n{seq}".encode()).hexdigest()


def _plan(seed, length=None):
    """Seeded (key, op, index) requests over a small key pool, so keys
    repeat with the same and with different requests."""
    rng = random.Random(seed)
    n = rng.randrange(1, 12) if length is None else length
    out = []
    for _ in range(n):
        if out and rng.random() < 0.35:
            out.append(rng.choice(out))
        else:
            out.append((rng.choice(KEYS), rng.choice(("put", "put", "delete")),
                        rng.randrange(len(FENS))))
    return out


def _shape(value):
    if type(value) is dict:
        return [(key, _shape(item)) for key, item in value.items()]
    if type(value) is list:
        return [_shape(item) for item in value]
    return value


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


def _deep_ids(value):
    if isinstance(value, dict):
        return [_Pin(value)] + [x for k, v in value.items()
                              for x in (_Pin(k), *_deep_ids(v))]
    if isinstance(value, list):
        return [_Pin(value)] + [x for v in value for x in _deep_ids(v)]
    return [_Pin(value)]


def _snap(*values):
    return [(_shape(v), _deep_ids(v)) for v in values]


def _fails(failure, call, *args):
    with pytest.raises(idem.IdempotencyError) as exc:
        try:
            _armed(call, *args)
        except idem.IdempotencyError:
            raise
        else:
            raise AssertionError("accepted")
    assert exc.value.failure_class == failure
    assert exc.value.code == idem.FAILURE_MAPPING[failure]


def _state(seed, length=None):
    """A real (log, ledger) pair from applying a seeded plan (conflicts
    skipped), plus the applied model entries."""
    log, ledger = [], []
    for key, op, index in _plan(seed, length):
        try:
            _engine().apply(log, ledger, _request(key, op, index))
        except idem.IdempotencyError as err:
            assert err.failure_class == "key_conflict"
    return log, ledger


def _positive(seed):
    for s in range(seed, seed + 300):
        log, ledger = _state(s, 3 + s % 6)
        if len(ledger) >= 2:
            return log, ledger
    raise AssertionError("no seed")


# -- R1: honest sequences against the model --------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_sequences_match_the_key_then_fingerprint_model(seed):
    log, ledger = [], []
    model_ops, model_ledger = [], {}
    for key, op, index in _plan(seed):
        request = _request(key, op, index)
        fp = _fp(op, request["payload"])
        req_before = _snap(request)
        old_entry_ids = [_Pin(e) for e in log]
        old_receipt_ids = [_Pin(r) for r in ledger]
        log_before, ledger_before = copy.deepcopy(log), copy.deepcopy(ledger)
        calls = []

        def fingerprinter(o, p, calls=calls):
            calls.append((o, copy.deepcopy(p)))
            return idem.request_fingerprint(o, p)

        if key in model_ledger and model_ledger[key]["request_fingerprint"] != fp:
            _fails("key_conflict", _engine(fingerprinter).apply, log, ledger, request)
            assert log == log_before and ledger == ledger_before
            assert [_Pin(e) for e in log] == old_entry_ids
            assert [_Pin(r) for r in ledger] == old_receipt_ids
        else:
            out = _engine(fingerprinter).apply(log, ledger, request)
            assert list(out) == ["outcome", "receipt"]
            receipt = out["receipt"]
            assert list(receipt) == list(RECEIPT_FIELDS)
            if key in model_ledger:
                assert out["outcome"] == "replayed"
                assert receipt == model_ledger[key]
                assert log == log_before and ledger == ledger_before
            else:
                assert out["outcome"] == "applied"
                model_ops.append((op, index))
                expected_log = copy.deepcopy(log_before)
                wal.WalEngine(wal.canonical_payload).append(
                    expected_log, {"op": op, "payload": _payload(index)})
                assert log == expected_log
                entry = log[-1]
                assert receipt == {
                    "receipt_id": _rid(key, fp, entry["entry_id"], len(log)),
                    "idempotency_key": key, "request_fingerprint": fp,
                    "entry_id": entry["entry_id"], "sequence": len(log)}
                assert type(receipt["sequence"]) is int
                assert receipt["receipt_id"] == idem.derive_receipt_id(
                    key, fp, entry["entry_id"], len(log))
                assert ledger == ledger_before + [receipt]
                assert ledger[-1] is not receipt
                model_ledger[key] = dict(receipt)
            assert [_Pin(e) for e in log[:len(old_entry_ids)]] == old_entry_ids
            assert [_Pin(r) for r in ledger[:len(old_receipt_ids)]] == old_receipt_ids
            receipt["sequence"] = -1
            assert all(r["sequence"] > 0 for r in ledger)
        assert calls == [(op, request["payload"])]
        assert type(calls[0][1]) is dict and type(calls[0][1]["record"]) is dict
        assert _snap(request) == req_before
    replay = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    assert replay["applied"] == len(model_ops) == len(ledger)


@pytest.mark.parametrize("seed", range(12))
def test_r2_replay_is_stable_and_conflict_never_overwrites(seed):
    log, ledger = _positive(seed)
    for receipt in list(ledger):
        entry = log[receipt["sequence"] - 1]
        request = {"idempotency_key": receipt["idempotency_key"], "op": entry["op"],
                   "payload": copy.deepcopy(entry["payload"])}
        before = _snap(log, ledger)
        for _ in range(2):
            out = _engine().apply(log, ledger, copy.deepcopy(request))
            assert out == {"outcome": "replayed", "receipt": receipt}
            assert out["receipt"] is not receipt
            assert _snap(log, ledger) == before
        other = next(i for i in range(len(FENS))
                     if _fp(entry["op"], _payload(i)) != receipt["request_fingerprint"])
        for op, index in ((entry["op"], other),
                          ("delete" if entry["op"] == "put" else "put",
                           IDENTITIES.index(entry["payload"]["identity"]))):
            _fails("key_conflict", _engine().apply, log, ledger,
                   _request(receipt["idempotency_key"], op, index))
            assert _snap(log, ledger) == before


def test_r2b_same_request_under_a_new_key_applies_again():
    log, ledger = [], []
    first = _engine().apply(log, ledger, _request("a", "put", 0))["receipt"]
    second = _engine().apply(log, ledger, _request("b", "put", 0))["receipt"]
    assert first["request_fingerprint"] == second["request_fingerprint"]
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert len(log) == 2 and len(ledger) == 2


def test_r3_fingerprint_and_receipt_id_derivations():
    for op in ("put", "delete"):
        for i in range(len(FENS)):
            payload = _payload(i)
            before = _snap(payload)
            assert idem.request_fingerprint(op, payload) == _fp(op, payload)
            assert _snap(payload) == before
    fps = {idem.request_fingerprint(op, _payload(i))
           for op in ("put", "delete") for i in range(len(FENS))}
    assert len(fps) == 2 * len(FENS)
    for args in (("k", "idf1:" + "a" * 64, "wal1:" + "b" * 64, 1),
                 ("z" * 128, "idf1:" + "0" * 64, "wal1:" + "f" * 64, 10 ** 6)):
        assert idem.derive_receipt_id(*args) == _rid(*args)


# -- R4: fingerprinter boundary ------------------------------------------------------------

def _raiser(kind):
    def fingerprinter(op, payload):
        raise kind()
    return fingerprinter


def _forged_oracle(error):
    def fingerprinter(op, payload):
        raise error
    return fingerprinter


FORGED_ERRORS = {
    **{f"forged-idempotency-error-{c}": idem.IdempotencyError(c, idem.FAILURE_MAPPING[c])
       for c in sorted(idem.FAILURE_MAPPING) if c != "divergent_fingerprint"},
    **{f"forged-wal-error-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
}


def _flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


HOSTILE_FINGERPRINTERS = {
    **{name: _forged_oracle(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda op, p: None,
    "bytes": lambda op, p: idem.request_fingerprint(op, p).encode(),
    "str-subclass": lambda op, p: _Str(idem.request_fingerprint(op, p)),
    "trailing-newline": lambda op, p: idem.request_fingerprint(op, p) + "\n",
    "upper-case": lambda op, p: idem.request_fingerprint(op, p).upper(),
    "surrogate": lambda op, p: idem.request_fingerprint(op, p)[:-1] + "\ud800",
    "grammar-valid-flip": lambda op, p: _flip(idem.request_fingerprint(op, p)),
    "other-op": lambda op, p: idem.request_fingerprint(
        "delete" if op == "put" else "put", p),
}


@pytest.mark.parametrize("name", sorted(HOSTILE_FINGERPRINTERS))
@pytest.mark.parametrize("seen", [False, True])
def test_r4_hostile_fingerprinter_fails_closed(name, seen):
    """Fails as divergent_fingerprint on both the unseen-key path and the
    seen-key path (fingerprinting precedes deduplication)."""
    for seed in range(3):
        log, ledger = _positive(seed)
        if seen:
            receipt = ledger[0]
            entry = log[receipt["sequence"] - 1]
            request = {"idempotency_key": receipt["idempotency_key"], "op": entry["op"],
                       "payload": copy.deepcopy(entry["payload"])}
        else:
            request = _request("fresh-key", "put", seed)
        before = _snap(log, ledger, request)
        _fails("divergent_fingerprint", _engine(HOSTILE_FINGERPRINTERS[name]).apply,
               log, ledger, request)
        assert _snap(log, ledger, request) == before


@pytest.mark.parametrize("seed", range(8))
def test_r5_fingerprinter_argument_is_detached(seed):
    log, ledger = _positive(seed)
    request = _request("fresh-key", "put", seed % len(FENS))
    expected_log, expected_ledger = copy.deepcopy(log), copy.deepcopy(ledger)
    expected = _engine().apply(expected_log, expected_ledger, copy.deepcopy(request))

    def fingerprinter(op, payload):
        token = idem.request_fingerprint(op, payload)
        payload["identity"] = "x"
        payload["record"]["digest"] = "x"
        payload["record"]["zz"] = "1"
        payload["zz"] = 1
        return token

    out = _engine(fingerprinter).apply(log, ledger, request)
    assert out == expected and log == expected_log and ledger == expected_ledger


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("path", ["applied", "replayed", "failed"])
def test_r6_live_input_mutation_is_undone(seed, path):
    """A fingerprinter closing over the caller's LIVE log, ledger and
    request changes, removes and adds keys at every level and inserts into
    and deletes from every container; all of it is undone (values, key
    order, identity) on every exit, and the result is the unmutated one."""
    log, ledger = _positive(seed)
    if path == "replayed":
        receipt = ledger[-1]
        entry = log[receipt["sequence"] - 1]
        request = {"idempotency_key": receipt["idempotency_key"], "op": entry["op"],
                   "payload": copy.deepcopy(entry["payload"])}
    else:
        request = _request("fresh-key", "put", seed % len(FENS))
    expected_log, expected_ledger = copy.deepcopy(log), copy.deepcopy(ledger)
    expected = _engine().apply(expected_log, expected_ledger, copy.deepcopy(request))
    before = _snap(log, ledger, request)
    old_entries, old_receipts = list(log), list(ledger)
    before_key = request["idempotency_key"]

    def fingerprinter(op, payload):
        token = idem.request_fingerprint(op, payload)
        for entry in list(log):
            entry["zz"] = 1
            entry["sequence"] = 99
            entry["payload"]["record"].pop("digest")
            entry["payload"]["zz"] = 1
        log.insert(0, {"foreign": True})
        del log[-1]
        for receipt in list(ledger):
            receipt["sequence"] = 0
            del receipt["receipt_id"]
            receipt["zz"] = 1
        ledger.append({"foreign": True})
        del ledger[0]
        del request["payload"]["record"]["digest"]
        request["payload"]["identity"] = "x"
        request["payload"]["zz"] = 1
        del request["op"]
        request["op"] = "delete"
        del request["idempotency_key"]
        request["idempotency_key"] = "other-key"
        request[_Str("zz")] = 1
        return None if path == "failed" else token

    if path == "failed":
        _fails("divergent_fingerprint", _engine(fingerprinter).apply, log, ledger, request)
        assert _snap(log, ledger, request) == before
    else:
        out = _engine(fingerprinter).apply(log, ledger, request)
        assert out == expected and log == expected_log and ledger == expected_ledger
        assert out["receipt"]["idempotency_key"] == before_key
        assert ledger[-1]["idempotency_key"] == before_key
        assert log[:len(old_entries)] == old_entries
        assert all(a is b for a, b in zip(log, old_entries, strict=False))
        assert all(a is b for a, b in zip(ledger, old_receipts, strict=False))
        assert _snap(request) == before[2:]


# -- R7: malformed requests ------------------------------------------------------------------

def _request_tampers():
    good = _request("k-new", "put", 0)
    p = good["payload"]
    rec = p["record"]

    def with_payload(payload):
        return {**good, "payload": payload}

    other = RECORDS[1]
    return [
        ("renamed-key", {("opx" if k == "op" else k): v for k, v in good.items()}),
        ("missing-key", {k: v for k, v in good.items() if k != "payload"}),
        ("extra-key", {**good, "zz": 1}),
        ("str-subclass-key", {**{k: v for k, v in good.items() if k != "op"},
                              _Str("op"): "put"}),
        ("colliding-key", {**{k: v for k, v in good.items() if k != "op"},
                           _Colliding("op"): "put"}),
        ("dict-subclass", _Dict(good)),
        ("list", list(good.items())),
        ("none", None),
        ("key-empty", {**good, "idempotency_key": ""}),
        ("key-too-long", {**good, "idempotency_key": "a" * 129}),
        ("key-leading-dash", {**good, "idempotency_key": "-a"}),
        ("key-space", {**good, "idempotency_key": "a b"}),
        ("key-newline", {**good, "idempotency_key": "a\n"}),
        ("key-str-subclass", {**good, "idempotency_key": _Str("k-new")}),
        ("key-int", {**good, "idempotency_key": 1}),
        ("op-unknown", {**good, "op": "move"}),
        ("op-str-subclass", {**good, "op": _Str("put")}),
        ("op-none", {**good, "op": None}),
        ("payload-none", with_payload(None)),
        ("payload-dict-subclass", with_payload(_Dict(p))),
        ("payload-str-subclass-key", with_payload(
            {"identity": p["identity"], _Str("record"): dict(rec)})),
        ("payload-colliding-key", with_payload(
            {"identity": p["identity"], _Colliding("record"): dict(rec)})),
        ("payload-extra-key", with_payload({**p, "zz": 1})),
        ("record-str-subclass-key", with_payload(
            {"identity": p["identity"],
             "record": {**{k: v for k, v in rec.items() if k != "digest"},
                        _Str("digest"): rec["digest"]}})),
        ("record-colliding-key", with_payload(
            {"identity": p["identity"],
             "record": {**{k: v for k, v in rec.items() if k != "digest"},
                        _Colliding("digest"): rec["digest"]}})),
        ("record-dict-subclass", with_payload({"identity": p["identity"],
                                               "record": _Dict(rec)})),
        ("record-wrong-digest", with_payload({"identity": p["identity"],
                                              "record": {**rec, "digest": other["digest"]}})),
        ("identity-mismatch", with_payload({"identity": IDENTITIES[1], "record": dict(rec)})),
        ("identity-str-subclass", with_payload({"identity": _Str(p["identity"]),
                                                "record": dict(rec)})),
    ]


@pytest.mark.parametrize("seed", range(3))
def test_r7_malformed_request_is_rejected_first(seed):
    for name, request in _request_tampers():
        for corrupt in ("none", "log", "log-entry-key", "log-payload-key",
                        "log-record-key", "log-colliding-key", "ledger"):
            log, ledger = _positive(seed)
            if corrupt == "log":
                log[0]["sequence"] += 1
            elif corrupt == "log-entry-key":
                log[0][_Str("op")] = log[0].pop("op")
            elif corrupt == "log-payload-key":
                log[0]["payload"][_Str("identity")] = log[0]["payload"].pop("identity")
            elif corrupt == "log-record-key":
                record = log[0]["payload"]["record"]
                record[_Str("digest")] = record.pop("digest")
            elif corrupt == "log-colliding-key":
                log[0][_Colliding("op")] = 1
            elif corrupt == "ledger":
                ledger[0]["sequence"] = 0
            before, calls = _snap(log, ledger, request), []
            try:
                _fails("malformed_idempotency_request",
                       _engine(lambda o, p, calls=calls: calls.append(o)).apply,
                       log, ledger, request)
            except AssertionError as err:
                raise AssertionError(f"{name}/{corrupt}: {err}") from None
            assert calls == [] and _snap(log, ledger, request) == before, name


def test_r7b_key_grammar_accepts_its_edges():
    log, ledger = [], []
    for i, key in enumerate(("a", "Z" * 128, "0._:-", "a" + "-" * 127)):
        out = _engine().apply(log, ledger, _request(key, "put", i))
        assert out["outcome"] == "applied" and out["receipt"]["idempotency_key"] == key


# -- R8: corrupt source ----------------------------------------------------------------------

SOURCE_TAMPERS = {
    "sequence-shift": lambda e: e.__setitem__("sequence", e["sequence"] + 1),
    "sequence-bool": lambda e: e.__setitem__("sequence", True),
    "unknown-op": lambda e: e.__setitem__("op", "move"),
    "op-str-subclass": lambda e: e.__setitem__("op", _Str(e["op"])),
    "entry-id-flip": lambda e: e.__setitem__("entry_id", _flip(e["entry_id"])),
    "prior-flip": lambda e: e.__setitem__("prior_entry_id", _flip(e["prior_entry_id"])),
    "extra-key": lambda e: e.__setitem__("zz", 1),
    "str-subclass-key": lambda e: e.__setitem__(_Str("op"), e.pop("op")),
    "colliding-key": lambda e: e.__setitem__(_Colliding("op"), 1),
    "payload-str-subclass-key": lambda e: e["payload"].__setitem__(
        _Str("identity"), e["payload"].pop("identity")),
    "payload-colliding-key": lambda e: e["payload"].__setitem__(_Colliding("identity"), 1),
    "record-str-subclass-key": lambda e: e["payload"]["record"].__setitem__(
        _Str("digest"), e["payload"]["record"].pop("digest")),
    "record-colliding-key": lambda e: e["payload"]["record"].__setitem__(
        _Colliding("digest"), 1),
    "digest-swap": lambda e: e["payload"]["record"].__setitem__(
        "digest", RECORDS[(IDENTITIES.index(e["payload"]["identity"]) + 1)
                          % len(RECORDS)]["digest"]),
    "identity-str-subclass": lambda e: e["payload"].__setitem__(
        "identity", _Str(e["payload"]["identity"])),
    "record-dict-subclass": lambda e: e["payload"].__setitem__(
        "record", _Dict(e["payload"]["record"])),
    "entry-dict-subclass": lambda e: _Dict(e),
    "entry-not-dict": lambda e: [e],
}


@pytest.mark.parametrize("name", sorted(SOURCE_TAMPERS))
def test_r8_corrupt_source_fails_before_ledger_and_fingerprinter(name):
    for seed in range(6):
        log, ledger = _positive(seed)
        position = random.Random(seed + 500).randrange(len(log))
        replacement = SOURCE_TAMPERS[name](log[position])
        if replacement is not None:
            log[position] = replacement
        for corrupt_ledger in (False, True):
            work_ledger = [{**r, "sequence": 0} for r in ledger] if corrupt_ledger else ledger
            request = _request("fresh-key", "put", 0)
            before, calls = _snap(log, work_ledger, request), []
            try:
                _fails("corrupt_source",
                       _engine(lambda o, p, calls=calls: calls.append(o)).apply,
                       log, work_ledger, request)
            except AssertionError as err:
                raise AssertionError(f"{name}/{seed}: {err}") from None
            assert calls == [] and _snap(log, work_ledger, request) == before


@pytest.mark.parametrize("bad", [None, (), {}, "log", 0, _Dict(), _List()])
def test_r8b_non_list_log_is_corrupt_source(bad):
    _fails("corrupt_source", _engine().apply, bad, [], _request("k", "put", 0))


# -- R9: corrupt ledger ----------------------------------------------------------------------

def _reforged(receipt, **changes):
    out = {**receipt, **changes}
    out["receipt_id"] = _rid(out["idempotency_key"], out["request_fingerprint"],
                             out["entry_id"], out["sequence"])
    return out


def _ledger_tampers(log, ledger):
    r0, r1 = ledger[0], ledger[1]

    def at(i, value):
        out = list(ledger)
        out[i] = value
        return out

    wrong_entry = next(e["entry_id"] for e in log if e["entry_id"] != r0["entry_id"])
    other_fp = next(fp for fp in (_fp(o, _payload(i)) for o in ("put", "delete")
                                  for i in range(len(FENS)))
                    if fp != r0["request_fingerprint"])
    return [
        ("not-list-tuple", tuple(ledger)),
        ("not-list-none", None),
        ("list-subclass", _List(ledger)),
        ("receipt-dict-subclass", at(0, _Dict(r0))),
        ("receipt-not-dict", at(0, list(r0.items()))),
        *[(f"renamed-key-{f}", at(0, {(f + "x" if k == f else k): v for k, v in r0.items()}))
          for f in RECEIPT_FIELDS],
        *[(f"missing-key-{f}", at(0, {k: v for k, v in r0.items() if k != f}))
          for f in RECEIPT_FIELDS],
        ("extra-key", at(0, {**r0, "zz": 1})),
        ("str-subclass-key", at(0, {**{k: v for k, v in r0.items() if k != "entry_id"},
                                    _Str("entry_id"): r0["entry_id"]})),
        ("colliding-key", at(0, {**{k: v for k, v in r0.items() if k != "entry_id"},
                                 _Colliding("entry_id"): r0["entry_id"]})),
        ("key-bad-grammar", at(0, _reforged(r0, idempotency_key="-bad"))),
        ("key-str-subclass", at(0, {**r0, "idempotency_key": _Str(r0["idempotency_key"])})),
        *[(f"{f}-str-subclass", at(0, {**r0, f: _Str(r0[f])}))
          for f in ("request_fingerprint", "entry_id", "receipt_id")],
        *[(f"{f}-upper", at(0, {**r0, f: r0[f].upper()}))
          for f in ("request_fingerprint", "entry_id", "receipt_id")],
        *[(f"{f}-trailing-newline", at(0, {**r0, f: r0[f] + "\n"}))
          for f in ("request_fingerprint", "entry_id", "receipt_id")],
        *[(f"{f}-non-str", at(0, {**r0, f: None}))
          for f in ("request_fingerprint", "entry_id", "receipt_id")],
        ("seq-bool", at(0, {**r0, "sequence": True} if r0["sequence"] == 1
                        else {**r0, "sequence": float(r0["sequence"])})),
        ("seq-int-subclass", at(0, {**r0, "sequence": _Int(r0["sequence"])})),
        ("seq-float", at(0, {**r0, "sequence": float(r0["sequence"])})),
        ("seq-zero", at(0, _reforged(r0, sequence=0))),
        ("seq-negative", at(0, _reforged(r0, sequence=-1))),
        ("seq-over-log", at(1, _reforged(r1, sequence=len(log) + 1))),
        ("seq-duplicate", at(1, _reforged(r1, sequence=r0["sequence"]))),
        ("seq-duplicate-fully-bound",
         [r0, _reforged(r0, idempotency_key="dup-seq")] + list(ledger[1:])),
        ("seq-descending", [r1, r0] + list(ledger[2:])),
        ("key-duplicate", at(1, _reforged(r1, idempotency_key=r0["idempotency_key"]))),
        ("receipt-id-flip", at(0, {**r0, "receipt_id": _flip(r0["receipt_id"])})),
        ("entry-id-wrong", at(0, _reforged(r0, entry_id=wrong_entry))),
        ("fingerprint-wrong", at(0, _reforged(r0, request_fingerprint=other_fp))),
    ]


@pytest.mark.parametrize("seed", range(6))
def test_r9_corrupt_ledger_fails_before_the_fingerprinter(seed):
    log, ledger = _positive(seed)
    for name, bad in _ledger_tampers(log, ledger):
        request = _request("fresh-key", "put", 0)
        before, calls = _snap(log, bad, request), []
        try:
            _fails("corrupt_ledger",
                   _engine(lambda o, p, calls=calls: calls.append(o)).apply,
                   log, bad, request)
        except AssertionError as err:
            raise AssertionError(f"{name}: {err}") from None
        assert calls == [] and _snap(log, bad, request) == before, name


def test_r9b_ledger_bounds_from_both_sides():
    log, ledger = _positive(0)
    last = ledger[-1]
    # a receipt at the log's last sequence is accepted...
    log2 = copy.deepcopy(log)
    ledger2 = copy.deepcopy(ledger)
    if last["sequence"] != len(log2):
        entry = log2[-1]
        ledger2.append(_reforged({"idempotency_key": "edge", "request_fingerprint":
                                  _fp(entry["op"], entry["payload"]), "entry_id":
                                  entry["entry_id"], "sequence": len(log2),
                                  "receipt_id": ""}))
    assert _engine().apply(log2, ledger2, _request("fresh", "put", 0))["outcome"] == "applied"
    # ...and one past it is rejected
    bad = copy.deepcopy(ledger) + [_reforged({**last, "idempotency_key": "past",
                                              "sequence": len(log) + 1})]
    _fails("corrupt_ledger", _engine().apply, log, bad, _request("fresh", "put", 0))
    # an empty ledger over a non-empty log is valid (receipts are optional)
    out = _engine().apply(copy.deepcopy(log), [], _request(last["idempotency_key"], "put", 0))
    assert out["outcome"] == "applied" and out["receipt"]["sequence"] == len(log) + 1


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
    assert modules <= {"__future__", "ast", "copy", "hashlib", "random",
                       "pathlib", "pytest", "graph.node", "store"}




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _snap(value)
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _snap(value) != before
    log = [{"a": 1}]
    ids = [_Pin(e) for e in log]
    log[0] = {"a": 1}
    log[0] = {"a": 1}
    assert [_Pin(e) for e in log] != ids
