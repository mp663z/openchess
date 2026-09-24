"""T0216 deterministic unit/property battery for the production WAL.

Seeded properties over store.wal (the shipped T0215 runtime) only: no
tests.* helpers. Op sequences are generated from records built by the
shipped graph.node runtime and checked against an independent model
fold, the graph.diff state id and an independent entry-id derivation.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import random
from pathlib import Path

import pytest
import yaml

from graph import diff
from graph.node import make_record, record_identity
from graph.position_digest import DigestError
from store import wal
from tools.variant_runtime import VariantError

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
SEEDS = range(48)


class _Str(str):
    pass


class _Dict(dict):
    pass


class _Colliding:
    """Hashes like a real field name; while armed (only around the engine
    call) comparing it raises, so any engine comparison before the
    exact-str key guard escapes raw."""

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


def _armed(call, *args):
    _Colliding.armed = True
    try:
        return call(*args)
    finally:
        _Colliding.armed = False


def _engine(canonicalizer=wal.canonical_payload):
    return wal.WalEngine(canonicalizer)


def _request(op, index):
    return {"op": op, "payload": {"identity": IDENTITIES[index],
                                  "record": dict(RECORDS[index])}}


def _ops(seed, length=None):
    rng = random.Random(seed)
    n = rng.randrange(0, 13) if length is None else length
    return [(rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
            for _ in range(n)]


def _fold(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _entry_id(sequence, op, identity, record, prior):
    """Independent derivation from data/contracts/wal.yaml."""
    canonical = (f"{identity}\n{record['variant']}\n{record['digest']}\n"
                 f"{record['snapshot_fen']}")
    return "wal1:" + hashlib.sha256(
        f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


def _build(ops):
    log = []
    engine = _engine()
    for op, index in ops:
        engine.append(log, _request(op, index))
    return log


def _shape(value):
    """Value plus key order at every level."""
    if type(value) is dict:
        return [(key, _shape(item)) for key, item in value.items()]
    if type(value) is list:
        return [_shape(item) for item in value]
    return value


def _snap(log):
    return (_shape(log), [id(entry) for entry in log],
            [id(entry["payload"]) for entry in log
             if type(entry) is dict and type(entry.get("payload")) is dict])


# -- R1: append builds the pinned chain -----------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_append_builds_the_chain_entry_by_entry(seed):
    log, engine, prior = [], _engine(), wal.GENESIS
    for position, (op, index) in enumerate(_ops(seed), start=1):
        request = _request(op, index)
        before_request = copy.deepcopy(request)
        kept = list(log)
        entry = engine.append(log, request)
        assert len(log) == position
        assert all(a is b for a, b in zip(log, kept, strict=False))
        assert entry == log[-1] and entry is not log[-1]
        assert entry["payload"] is not log[-1]["payload"]
        assert entry["sequence"] == position
        assert entry["op"] == op
        assert entry["prior_entry_id"] == prior
        assert entry["entry_id"] == _entry_id(
            position, op, IDENTITIES[index], RECORDS[index], prior)
        assert entry["payload"] == before_request["payload"]
        assert entry["payload"] is not request["payload"]
        assert log[-1]["payload"] is not request["payload"]
        assert log[-1]["payload"]["record"] is not request["payload"]["record"]
        assert entry["payload"]["record"] is not request["payload"]["record"]
        assert entry["payload"]["record"] is not log[-1]["payload"]["record"]
        assert request == before_request
        prior = entry["entry_id"]


# -- R2: replay equals the model fold on every prefix ---------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r2_replay_matches_model_on_every_prefix(seed):
    ops = _ops(seed)
    log = _build(ops)
    engine = _engine()
    for k in range(len(ops) + 1):
        prefix = log[:k]
        before = _snap(prefix)
        result = engine.replay(prefix)
        model = _fold(ops[:k])
        assert result["state"] == model
        assert list(result["state"]) == list(model)
        assert result["state_id"] == diff.state_id(copy.deepcopy(model))
        assert result["applied"] == k
        assert result["head"] == (log[k - 1]["entry_id"] if k else wal.GENESIS)
        assert _snap(prefix) == before
        for rec in result["state"].values():
            assert all(rec is not e["payload"]["record"] for e in prefix)


@pytest.mark.parametrize("seed", SEEDS)
def test_r3_determinism_and_replay_idempotence(seed):
    ops = _ops(seed)
    first, second = _build(ops), _build(ops)
    assert first == second
    assert all(a is not b for a, b in zip(first, second, strict=True))
    engine = _engine()
    assert engine.replay(first) == engine.replay(first) == engine.replay(second)


def test_r4_state_semantics_of_put_and_delete():
    empty_id = _engine().replay([])["state_id"]
    assert empty_id == diff.state_id({})
    # delete of an absent identity is a logged no-op on state
    log = _build([("delete", 0)])
    assert len(log) == 1 and _engine().replay(log)["state"] == {}
    assert _engine().replay(log)["state_id"] == empty_id
    # put then delete returns to the prior state id; the head still moves
    log = _build([("put", 3), ("put", 1), ("delete", 1)])
    replay = _engine().replay(log)
    assert replay["state_id"] == _engine().replay(log[:1])["state_id"]
    assert replay["head"] != log[0]["entry_id"]
    # repeated put is idempotent on state but appends
    log = _build([("put", 2), ("put", 2)])
    assert _engine().replay(log)["state"] == _fold([("put", 2)])
    assert log[0]["entry_id"] != log[1]["entry_id"]


# -- R5: tampering any single entry fails closed, typed, atomically -------------

def _other_prior(entry):
    return "wal1:" + ("0" if entry["prior_entry_id"][-1] != "0" else "1") * 64


def _flip(entry_id):
    last = entry_id[-1]
    return entry_id[:-1] + ("0" if last != "0" else "1")


def _reforge(entry):
    """Relink to a different prior AND re-derive a self-consistent entry
    id, so only the chain-link check can see it."""
    entry["prior_entry_id"] = _other_prior(entry)
    entry["entry_id"] = _entry_id(
        entry["sequence"], entry["op"], entry["payload"]["identity"],
        entry["payload"]["record"], entry["prior_entry_id"])


def _reforge_sequence(entry):
    """Lower the sequence and re-derive a self-consistent entry id."""
    entry["sequence"] -= 1
    entry["entry_id"] = _entry_id(
        entry["sequence"], entry["op"], entry["payload"]["identity"],
        entry["payload"]["record"], entry["prior_entry_id"])


def _sub_field(entry, field):
    record = entry["payload"]["record"]
    record[field] = _Str(record[field])


TAMPERS = {
    "sequence-shift": (lambda e, i: e.__setitem__("sequence", e["sequence"] + 1),
                       "sequence_conflict"),
    "sequence-str": (lambda e, i: e.__setitem__("sequence", str(e["sequence"])),
                     "malformed_wal_entry"),
    "sequence-bool": (lambda e, i: e.__setitem__("sequence", True),
                      "malformed_wal_entry"),
    "unknown-op": (lambda e, i: e.__setitem__("op", "move"), "unknown_operation"),
    "op-non-str": (lambda e, i: e.__setitem__("op", 1), "malformed_wal_entry"),
    "entry-id-flip": (lambda e, i: e.__setitem__("entry_id", _flip(e["entry_id"])),
                      "corrupt_chain"),
    "entry-id-grammar": (lambda e, i: e.__setitem__("entry_id", "wal1:xyz"),
                         "malformed_wal_entry"),
    "prior-relink": (lambda e, i: e.__setitem__("prior_entry_id", _other_prior(e)),
                     "corrupt_chain"),
    "prior-relink-reforged": (lambda e, i: _reforge(e), "corrupt_chain"),
    "entry-extra-key": (lambda e, i: e.__setitem__("zz", 1), "malformed_wal_entry"),
    "entry-missing-key": (lambda e, i: e.pop("prior_entry_id"),
                          "malformed_wal_entry"),
    "payload-extra-key": (lambda e, i: e["payload"].__setitem__("zz", 1),
                          "malformed_wal_entry"),
    "identity-swap": (lambda e, i: e["payload"].__setitem__(
        "identity", IDENTITIES[(IDENTITIES.index(e["payload"]["identity"]) + 1)
                               % len(IDENTITIES)]), "malformed_wal_entry"),
    "digest-swap": (lambda e, i: e["payload"]["record"].__setitem__(
        "digest", RECORDS[(IDENTITIES.index(e["payload"]["identity"]) + 1)
                          % len(RECORDS)]["digest"]), "malformed_wal_entry"),
    "record-extra-key": (lambda e, i: e["payload"]["record"].__setitem__("zz", "x"),
                         "malformed_wal_entry"),
    "record-clocks": (lambda e, i: e["payload"]["record"].__setitem__(
        "snapshot_fen", e["payload"]["record"]["snapshot_fen"][:-3] + "5 9"),
        "malformed_wal_entry"),
    "entry-not-dict": (lambda e, i: ["not", "an", "entry"], "malformed_wal_entry"),
    "sequence-lower-reforged": (lambda e, i: _reforge_sequence(e), "sequence_conflict"),
    "prior-grammar": (lambda e, i: e.__setitem__("prior_entry_id", "wal1:xyz"),
                      "malformed_wal_entry"),
    "entry-dict-subclass": (lambda e, i: _Dict(e), "malformed_wal_entry"),
    "payload-dict-subclass": (lambda e, i: e.__setitem__("payload", _Dict(e["payload"])),
                              "malformed_wal_entry"),
    "record-dict-subclass": (lambda e, i: e["payload"].__setitem__(
        "record", _Dict(e["payload"]["record"])), "malformed_wal_entry"),
    "op-str-subclass": (lambda e, i: e.__setitem__("op", _Str(e["op"])),
                        "malformed_wal_entry"),
    "entry-id-str-subclass": (lambda e, i: e.__setitem__("entry_id", _Str(e["entry_id"])),
                              "malformed_wal_entry"),
    "prior-str-subclass": (lambda e, i: e.__setitem__(
        "prior_entry_id", _Str(e["prior_entry_id"])), "malformed_wal_entry"),
    "identity-str-subclass": (lambda e, i: e["payload"].__setitem__(
        "identity", _Str(e["payload"]["identity"])), "malformed_wal_entry"),
    "record-variant-str-subclass": (lambda e, i: _sub_field(e, "variant"),
                                    "malformed_wal_entry"),
    "record-fen-str-subclass": (lambda e, i: _sub_field(e, "snapshot_fen"),
                                "malformed_wal_entry"),
    "record-digest-str-subclass": (lambda e, i: _sub_field(e, "digest"),
                                   "malformed_wal_entry"),
    "entry-str-subclass-key": (lambda e, i: e.__setitem__(_Str("op"), e.pop("op")),
                               "malformed_wal_entry"),
    "payload-str-subclass-key": (lambda e, i: e["payload"].__setitem__(
        _Str("identity"), e["payload"].pop("identity")), "malformed_wal_entry"),
    "record-str-subclass-key": (lambda e, i: e["payload"]["record"].__setitem__(
        _Str("digest"), e["payload"]["record"].pop("digest")), "malformed_wal_entry"),
    "entry-colliding-key": (lambda e, i: e.__setitem__(_Colliding("op"), 1),
                            "malformed_wal_entry"),
    "payload-colliding-key": (lambda e, i: e["payload"].__setitem__(
        _Colliding("identity"), 1), "malformed_wal_entry"),
    "record-colliding-key": (lambda e, i: e["payload"]["record"].__setitem__(
        _Colliding("digest"), 1), "malformed_wal_entry"),
}


@pytest.mark.parametrize("name", sorted(TAMPERS))
def test_r5_single_entry_tamper_rejects_typed_and_atomic(name):
    mutate, failure = TAMPERS[name]
    for seed in range(12):
        ops = _ops(seed, length=random.Random(seed).randrange(1, 9))
        log = _build(ops)
        position = random.Random(seed + 1000).randrange(len(log))
        replacement = mutate(log[position], position)
        if replacement is not None:
            log[position] = replacement
        for label in ("replay", "append"):
            request = _request("put", seed % len(RECORDS))
            before, before_request = _snap(log), copy.deepcopy(request)
            with pytest.raises(wal.WalError) as exc:
                if label == "replay":
                    _armed(_engine().replay, log)
                else:
                    _armed(_engine().append, log, request)
            assert exc.value.failure_class == failure, (name, seed, label)
            assert exc.value.code == wal.FAILURE_MAPPING[failure]
            assert _snap(log) == before
            assert request == before_request


# -- R6: malformed append requests ----------------------------------------------

def _bad_requests():
    good = _request("put", 0)
    payload, record = good["payload"], good["payload"]["record"]
    return [
        (None, "malformed_wal_entry"),
        ([], "malformed_wal_entry"),
        ({"op": "put"}, "malformed_wal_entry"),
        ({**good, "zz": 1}, "malformed_wal_entry"),
        ({**good, "op": 1}, "malformed_wal_entry"),
        ({**good, "op": "upsert"}, "unknown_operation"),
        ({**good, "op": "PUT"}, "unknown_operation"),
        ({**good, "payload": None}, "malformed_wal_entry"),
        ({**good, "payload": {"identity": payload["identity"]}},
         "malformed_wal_entry"),
        ({**good, "payload": {**payload, "identity": IDENTITIES[1]}},
         "malformed_wal_entry"),
        ({**good, "payload": {**payload, "record": {
            **record, "digest": RECORDS[1]["digest"]}}}, "malformed_wal_entry"),
        ({**good, "payload": {**payload, "record": {
            **record, "variant": "chess960"}}}, "malformed_wal_entry"),
        ({**good, "payload": {**payload, "record": {
            k: v for k, v in record.items() if k != "digest"}}},
         "malformed_wal_entry"),
        ({**good, "op": _Str("put")}, "malformed_wal_entry"),
        ({**good, "payload": {**payload, "identity": _Str(payload["identity"])}},
         "malformed_wal_entry"),
        ({**good, "payload": {**payload, "record": {
            **record, "snapshot_fen": _Str(record["snapshot_fen"])}}},
         "malformed_wal_entry"),
        ({**good, "payload": {**payload, "record": {
            **record, "digest": _Str(record["digest"])}}}, "malformed_wal_entry"),
        (_Dict(good), "malformed_wal_entry"),
        ({"payload": payload, _Str("op"): "put"}, "malformed_wal_entry"),
        ({**good, "payload": {"record": record, _Str("identity"): payload["identity"]}},
         "malformed_wal_entry"),
        ({**good, "payload": {**payload, "record": _Dict(record)}}, "malformed_wal_entry"),
        ({**good, "payload": _Dict(payload)}, "malformed_wal_entry"),
        ({**good, _Colliding("op"): 1}, "malformed_wal_entry"),
        ({**good, "payload": {**payload, "record": {
            **record, _Colliding("variant"): "x"}}}, "malformed_wal_entry"),
    ]


@pytest.mark.parametrize("seed", range(8))
def test_r6_malformed_requests_reject_typed_and_atomic(seed):
    log = _build(_ops(seed))
    for request, failure in _bad_requests():
        before, before_request = _snap(log), copy.deepcopy(request)
        with pytest.raises(wal.WalError) as exc:
            _armed(_engine().append, log, request)
        assert exc.value.failure_class == failure, request
        assert _snap(log) == before and request == before_request
    for bad_log in ((), None, {}, "log"):
        with pytest.raises(wal.WalError) as exc:
            _engine().append(bad_log, _request("put", 0))
        assert exc.value.failure_class == "malformed_wal_entry"
        with pytest.raises(wal.WalError) as exc:
            _engine().replay(bad_log)
        assert exc.value.failure_class == "malformed_wal_entry"


# -- R7: the canonicalizer boundary ---------------------------------------------

def _raiser(kind):
    def canonicalizer(identity, record):
        raise kind()
    return canonicalizer


def _forged_oracle(error):
    """An oracle raising a pre-built (forged) typed error: it must fail as
    the boundary's own class, never surface as the forged one."""
    def oracle(identity, record):
        raise error
    return oracle


_CONTRACTS = Path(__file__).resolve().parents[1] / "data" / "contracts"
# the linked modules' error classes, forged per class: DigestError per
# failure class of the digest contract, VariantError per closed error
# code of the variant contract
DIGEST_MAPPING = yaml.safe_load(
    (_CONTRACTS / "position_digest.yaml").read_text())["contract"][
        "failures"]["mapping"]
VARIANT_CODES = sorted(yaml.safe_load(
    (_CONTRACTS / "variant.yaml").read_text())["contract"]["errors"][
        "closed_enum"])

FORGED_ERRORS = {
    **{f"forged-wal-error-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
    **{f"forged-digest-error-{c}": DigestError(c, code)
       for c, code in sorted(DIGEST_MAPPING.items())},
    **{f"forged-variant-error-{code}": VariantError(code=code,
                                                   message="forged")
       for code in VARIANT_CODES},
    # wal names UnicodeEncodeError in its own except clause (the output
    # check); a RAISED one must fail closed too
    "forged-unicode-encode-error": UnicodeEncodeError(
        "utf-8", "\ud800", 0, 1, "surrogates not allowed"),
}


HOSTILE_CANONICALIZERS = {
    **{name: _forged_oracle(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda identity, record: None,
    "bytes": lambda identity, record: b"x",
    "str-subclass": lambda identity, record: _Str(
        wal.canonical_payload(identity, record)),
    "surrogate": lambda identity, record: "\ud800",
}


@pytest.mark.parametrize("name", sorted(HOSTILE_CANONICALIZERS))
@pytest.mark.parametrize("seed", range(6))
def test_r7_hostile_canonicalizer_fails_closed(name, seed):
    log = _build(_ops(seed, length=seed % 4))
    engine = _engine(HOSTILE_CANONICALIZERS[name])
    request = _request("put", seed % len(RECORDS))
    before, before_request = _snap(log), copy.deepcopy(request)
    for label in ("append", "replay") if log else ("append",):
        with pytest.raises(wal.WalError) as exc:
            if label == "append":
                engine.append(log, request)
            else:
                engine.replay(log)
        assert exc.value.failure_class == "divergent_canonicalization", label
        assert exc.value.code == wal.FAILURE_MAPPING[
            "divergent_canonicalization"], label
        assert _snap(log) == before and request == before_request
        forged = FORGED_ERRORS.get(name)
        if forged is not None:
            # FRESH: never the forged instance (not even a forged
            # divergent_canonicalization), never explicitly chained
            assert exc.value is not forged, label
            assert type(exc.value) is wal.WalError, label
            assert exc.value.__cause__ is None, label


@pytest.mark.parametrize("seed", range(12))
def test_r8b_divergent_canonicalizer_on_append_breaks_the_chain(seed):
    """On append, a divergent string on any call over the existing log
    is corrupt_chain; on the new entry's call it only changes the new
    id (the chain it heads is self-consistent)."""
    log = _build(_ops(seed, length=random.Random(seed).randrange(1, 8)))
    for bad_call in range(len(log) + 1):
        calls = []

        def canonicalizer(identity, record, bad_call=bad_call, calls=calls):
            calls.append(identity)
            text = wal.canonical_payload(identity, record)
            return text + "x" if len(calls) - 1 == bad_call else text

        work = list(log)
        request = _request("put", seed % len(RECORDS))
        before, before_request = _snap(work), copy.deepcopy(request)
        if bad_call < len(log):
            with pytest.raises(wal.WalError) as exc:
                _engine(canonicalizer).append(work, request)
            assert exc.value.failure_class == "corrupt_chain"
            assert _snap(work) == before and request == before_request
        else:
            entry = _engine(canonicalizer).append(work, request)
            honest = _entry_id(len(log) + 1, "put", request["payload"]["identity"],
                               request["payload"]["record"], log[-1]["entry_id"])
            assert entry["entry_id"] != honest
            with pytest.raises(wal.WalError) as exc:
                _engine().replay(work)
            assert exc.value.failure_class == "corrupt_chain"


@pytest.mark.parametrize("seed", range(12))
def test_r8_divergent_canonicalizer_breaks_the_chain(seed):
    """A well-formed but different canonical string on any one call is a
    divergent entry id: corrupt_chain, atomically."""
    log = _build(_ops(seed, length=random.Random(seed).randrange(1, 8)))
    bad_call = random.Random(seed + 7).randrange(len(log))
    calls = []

    def canonicalizer(identity, record):
        calls.append(identity)
        text = wal.canonical_payload(identity, record)
        return text + "x" if len(calls) - 1 == bad_call else text

    before = _snap(log)
    with pytest.raises(wal.WalError) as exc:
        _engine(canonicalizer).replay(log)
    assert exc.value.failure_class == "corrupt_chain"
    assert _snap(log) == before


@pytest.mark.parametrize("seed", range(12))
def test_r9_canonicalizer_call_count_and_detached_argument(seed):
    """replay: one call per entry; append: one per existing entry plus
    one for the new entry. The canonicalizer's record argument is a
    detached copy, so mutating it never reaches the log or request."""
    log = _build(_ops(seed))
    calls = []

    def canonicalizer(identity, record):
        text = wal.canonical_payload(identity, record)
        calls.append(identity)
        record["zz"] = 1
        record["digest"] = "tampered"
        return text

    before = _snap(log)
    _engine(canonicalizer).replay(log)
    assert len(calls) == len(log) and _snap(log) == before
    calls.clear()
    request = _request("put", seed % len(RECORDS))
    before_request = copy.deepcopy(request)
    entry = _engine(canonicalizer).append(log, request)
    assert len(calls) == len(log)  # log now holds the new entry
    assert request == before_request
    assert entry["payload"]["record"] == RECORDS[seed % len(RECORDS)]
    assert _engine().replay(log)["applied"] == len(log)


def _live_mutator(request, log, fail_on_call):
    """A canonicalizer closing over the caller's LIVE request and log: on
    every call it adds keys at every level of both and inserts into the
    log container; on FAIL_ON_CALL it returns None."""
    calls = []

    def canonicalizer(identity, record):
        calls.append(identity)
        if request is not None:
            request["zz"] = 1
            request["payload"]["zz"] = 1
            request["payload"]["record"]["zz"] = 1
        for entry in list(log):
            if type(entry) is dict and "payload" in entry:
                entry["zz"] = 1
                entry["payload"]["zz"] = 1
                entry["payload"]["record"]["zz"] = 1
        log.insert(0, {"foreign": len(calls)})
        if len(calls) == fail_on_call:
            return None
        return wal.canonical_payload(identity, record)
    return canonicalizer, calls


def _deep_ids(value):
    if type(value) is dict:
        return [id(value)] + [x for v in value.values() for x in _deep_ids(v)]
    if type(value) is list:
        return [id(value)] + [x for v in value for x in _deep_ids(v)]
    return []


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("path", ["append-ok", "append-fail-first",
                                  "append-fail-last", "replay-ok",
                                  "replay-fail-last"])
def test_r9b_live_input_mutation_is_undone_on_every_exit(seed, path):
    log = _build(_ops(seed, length=1 + seed % 5))
    n = len(log)
    request = _request("put", seed % len(RECORDS)) if path.startswith("append") else None
    fail_on = {"append-ok": 0, "append-fail-first": 1, "append-fail-last": n + 1,
               "replay-ok": 0, "replay-fail-last": n}[path]
    canonicalizer, calls = _live_mutator(request, log, fail_on)
    before_log, before_request = _snap(log), _shape(request)
    ids_log, ids_request = _deep_ids(log), _deep_ids(request)
    engine = _engine(canonicalizer)
    if fail_on:
        with pytest.raises(wal.WalError) as exc:
            engine.append(log, request) if request is not None else engine.replay(log)
        assert exc.value.failure_class == "divergent_canonicalization"
        assert _snap(log) == before_log
        assert _deep_ids(log) == ids_log
    elif request is not None:
        entry = engine.append(log, request)
        assert len(calls) == n + 1
        assert log[-1] == entry and _shape(log[:-1]) == before_log[0]
        assert _deep_ids(log[:-1])[1:] == ids_log[1:] and log is not None
        assert "zz" not in entry and "zz" not in entry["payload"]["record"]
    else:
        result = engine.replay(log)
        assert len(calls) == n and result["applied"] == n
        assert _snap(log) == before_log and _deep_ids(log) == ids_log
    if request is not None:
        assert _shape(request) == before_request
        assert _deep_ids(request) == ids_request


# -- R10: public snapshot/restore round-trip ------------------------------------

def _scramble(log, rng):
    for entry in log:
        payload, record = entry["payload"], entry["payload"]["record"]
        for target in (entry, payload, record):
            choice = rng.randrange(4)
            if choice == 0:
                target["zz"] = rng.random()
            elif choice == 1:
                target.pop(next(iter(target)))
            elif choice == 2:
                first = next(iter(target))
                target[first] = target.pop(first)  # key order change
            else:
                target.clear()
    if log:
        log.append(log[0])
        log.insert(0, {"foreign": True})
        if rng.random() < 0.5:
            log.reverse()
        else:
            del log[1]


@pytest.mark.parametrize("seed", SEEDS)
def test_r10_snapshot_restore_round_trip(seed):
    log = _build(_ops(seed))
    before = _snap(log)
    objects = [(e, e["payload"], e["payload"]["record"]) for e in log]
    container, saved = wal.snapshot(log)
    _scramble(log, random.Random(seed))
    wal.restore(log, container, saved)
    assert _snap(log) == before
    assert [(e, e["payload"], e["payload"]["record"]) for e in log] == objects
    assert all(a is b for pair in zip(
        [(e, e["payload"], e["payload"]["record"]) for e in log], objects,
        strict=True) for a, b in zip(*pair, strict=True))
    assert _engine().replay(log)["applied"] == len(log)


@pytest.mark.parametrize("fen", [
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1" + " " * 300,
    "x" * 257,
    "4k3/8/8/8/8/8/8/4K3 w - -",
])
def test_r11_rejections_are_never_cached(fen):
    """Bad records reject on the memoized and the unbounded path alike,
    on every repetition (only successes are cached), and a good record
    still validates between them."""
    good = _request("put", 3)
    bad = {"op": "put", "payload": {"identity": IDENTITIES[3], "record": {
        **RECORDS[3], "snapshot_fen": fen}}}
    long_variant = {"op": "put", "payload": {"identity": IDENTITIES[3],
                                              "record": {**RECORDS[3],
                                                         "variant": "v" * 65}}}
    for _ in range(3):
        for request in (bad, long_variant):
            log = []
            with pytest.raises(wal.WalError) as exc:
                _engine().append(log, request)
            assert exc.value.failure_class == "malformed_wal_entry"
            assert log == []
        log = []
        assert _engine().append(log, good) == log[0]


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
    assert modules <= {"__future__", "ast", "copy", "hashlib", "random",
                       "pathlib", "pytest", "yaml", "graph", "graph.node",
                       "graph.position_digest", "tools.variant_runtime",
                       "store"}
