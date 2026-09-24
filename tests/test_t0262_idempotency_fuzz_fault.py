"""T0262 Store/idempotency/fuzz-fault: deterministic fuzz/fault battery for the
production store idempotency (store/idempotency.py, T0260).

Seeded generators build WAL logs, receipt ledgers and keyed requests from an
independent model of data/contracts/idempotency.yaml (entry-id, request-
fingerprint and receipt-id derivations and key-then-fingerprint dedup are
restated here, never taken from production) and then damage them:

- request: one edit to the request (every leaf type, str-subclass values at
  every level, near-miss keys, unregistered ops, bad payloads, dropped,
  added and same-arity renamed keys, str-subclass, eq-raising and hash-
  colliding keys, dict subclasses and a lying dict at request, payload and
  record), over a clean or a corrupt log;
- container: list-subclass, non-list logs and ledgers;
- source: one log-entry tamper (sequence, chain, entry id, op, payload and
  record edits, hostile types at entry, payload and record);
- ledger: one receipt tamper (shape, grammar, derivation, sequence bounds
  and order, duplicate keys, binding to the live entry, hostile types);
- oracle: a fingerprinter fault over a fresh, a replayed and a conflicting
  apply (every BaseException kind, a forged error of every class
  store/idempotency.py raises or names in its own except clauses, non-str,
  str-subclass, upper-case, trailing-newline, trailing-junk, other-request
  and constant output, honest, honest-then-scribble on its arguments);
- live: a fingerprinter that edits the caller's live log, ledger and request
  and then answers honestly, raises or diverges;
- sequence: a run of applies (fresh keys, exact replays, conflicts) checked
  step by step against the model, plus log rollback past a receipt.

Every case must satisfy, against production: no raw escape (only a typed
IdempotencyError from the closed class set with its mapped code, never a
forged object, never chained); the same outcome and result as the contract
reference engine (tests.test_t0257_idempotency_contract) and, where known,
the model outcome; on rejection log, ledger and request identical by value,
exact type, key order and object identity at every level; on success the
request identical and log and ledger extended by exactly the model entry
and receipt (applied) or untouched (replayed); no fingerprinter call on a
request, source or ledger rejection and exactly one otherwise, on detached
exact-type arguments; no poisoning; determinism.

Hostile types: every input boundary (request, payload, record, log, entry,
entry payload and record, ledger, receipt) gets list-subclass, dict-subclass
and lying-dict rows, str-subclass keys in 3 forms (plain, eq-raises, hash-
collides) and str-subclass values; each row pins the typed class, an empty
hostile-call log and unchanged input.

Mutation check: _probe runs a fixed slice of every generator against a
source mutant of store/idempotency.py. Every entry in MUTANTS must turn the
probe red; every entry in EQUIVALENT_EDITS must keep it green.
"""

from __future__ import annotations

import ast
import copy
import functools
import hashlib
import random
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.node import make_record, record_identity  # noqa: E402
from store import idempotency  # noqa: E402
from store import wal as _wal  # noqa: E402
from tests import test_t0257_idempotency_contract as _reference  # noqa: E402
from tools.idempotency_contract_lint import FAILURE_MAPPING  # noqa: E402
from tools.wal_contract_lint import FAILURE_MAPPING as WAL_MAPPING  # noqa: E402

SOURCE_TEXT = (ROOT / "store" / "idempotency.py").read_text()
WAL_TEXT = (ROOT / "store" / "wal.py").read_text()
_WAL_GUARD = (
    "wal",
    "    return all(type(key) is str for key in dict.keys(mapping))",
    "    return True",
)
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
IDENTS = tuple(record_identity(record) for record in RECORDS)
CLASSES = frozenset(FAILURE_MAPPING)
MR, CS, CL, KC, DF = (
    "malformed_idempotency_request",
    "corrupt_source",
    "corrupt_ledger",
    "key_conflict",
    "divergent_fingerprint",
)
OPS = ("put", "delete")
KEYS = ("k1", "order:42", "A.b-c_d", "z" * 128, "9", "x-1", "Y.2", "q:q")
GENESIS = "wal0:" + "0" * 64
GENERATORS = (
    "request",
    "container",
    "source",
    "ledger",
    "hostile",
    "oracle",
    "live",
    "sequence",
)
FUZZ_SEEDS = {
    "request": 240,
    "container": 24,
    "source": 192,
    "ledger": 192,
    "hostile": 0,
    "oracle": 0,
    "live": 96,
    "sequence": 48,
}
PROBE_SEEDS = 48  # per generator; the oracle slice covers every fault once
HOSTILE = []  # user dunder calls observed while armed


class _Str(str):
    pass


class _Int(int):
    pass


class _Dict(dict):
    pass


class _List(list):
    pass


class _Armed:
    on = False


class _Colliding:
    """Hashes like a real name; while armed comparing it raises, so any
    comparison before an exact-str key guard escapes raw."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __deepcopy__(self, memo):
        return self

    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("colliding-eq")
            raise RuntimeError("hostile __eq__")
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)


class _EqRaises(str):
    """A str subclass whose comparison and hashing are logged while armed."""

    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("eqraises-eq")
            raise RuntimeError("hostile __eq__")
        return str.__eq__(self, other)

    def __hash__(self):
        if _Armed.on:
            HOSTILE.append("eqraises-hash")
        return str.__hash__(self)

    def __deepcopy__(self, memo):
        return self


class _Lying(dict):
    """A dict subclass whose every view is logged while armed."""

    def _log(self, name):
        if _Armed.on:
            HOSTILE.append(f"lying-{name}")

    def keys(self):
        self._log("keys")
        return dict.keys(self)

    def items(self):
        self._log("items")
        return dict.items(self)

    def values(self):
        self._log("values")
        return dict.values(self)

    def get(self, key, default=None):
        self._log("get")
        return dict.get(self, key, default)

    def __iter__(self):
        self._log("iter")
        return dict.__iter__(self)

    def __getitem__(self, key):
        self._log("getitem")
        return dict.__getitem__(self, key)

    def __len__(self):
        self._log("len")
        return dict.__len__(self)

    def __deepcopy__(self, memo):
        return _Lying({k: copy.deepcopy(v, memo) for k, v in dict.items(self)})


class _LogList(list):
    def __iter__(self):
        if _Armed.on:
            HOSTILE.append("list-iter")
        return list.__iter__(self)

    def __len__(self):
        if _Armed.on:
            HOSTILE.append("list-len")
        return list.__len__(self)

    def __getitem__(self, index):
        if _Armed.on:
            HOSTILE.append("list-getitem")
        return list.__getitem__(self, index)


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hkey(form, text):
    if form == "plain":
        return _Str(text)
    if form == "eq-raises":
        return _EqRaises(text)
    return _Colliding(text)


# -- the independent model --------------------------------------------------------


def _fp(op, identity, record):
    parts = ["idf1"]
    for text in (op, identity, record["variant"], record["digest"], record["snapshot_fen"]):
        parts.append(f"{len(text)}:{text}")
    return "idf1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _rid(key, fp, entry_id, seq):
    return "idr1:" + hashlib.sha256(f"{key}\n{fp}\n{entry_id}\n{seq}".encode()).hexdigest()


def _entry(seq, op, index, prior):
    ident, rec = IDENTS[index], RECORDS[index]
    canonical = f"{ident}\n{rec['variant']}\n{rec['digest']}\n{rec['snapshot_fen']}"
    return {
        "entry_id": "wal1:"
        + hashlib.sha256(f"{seq}\n{op}\n{canonical}\n{prior}".encode()).hexdigest(),
        "sequence": seq,
        "op": op,
        "payload": {"identity": ident, "record": dict(rec)},
        "prior_entry_id": prior,
    }


def _request(key, op, index):
    return {
        "idempotency_key": key,
        "op": op,
        "payload": {"identity": IDENTS[index], "record": dict(RECORDS[index])},
    }


def _model_apply(log, ledger, key, op, index):
    """(outcome, receipt, new_log, new_ledger) or ("err", class)."""
    token = _fp(op, IDENTS[index], RECORDS[index])
    for receipt in ledger:
        if receipt["idempotency_key"] == key:
            if receipt["request_fingerprint"] != token:
                return ("err", KC)
            return ("replayed", dict(receipt), log, ledger)
    prior = log[-1]["entry_id"] if log else GENESIS
    entry = _entry(len(log) + 1, op, index, prior)
    receipt = {
        "receipt_id": _rid(key, token, entry["entry_id"], entry["sequence"]),
        "idempotency_key": key,
        "request_fingerprint": token,
        "entry_id": entry["entry_id"],
        "sequence": entry["sequence"],
    }
    return ("applied", receipt, [*log, entry], [*ledger, receipt])


def _store(rng, low=0, high=5, keyed_every=True):
    """A valid (log, ledger, used) built through the model; with
    keyed_every False some entries carry no receipt."""
    log, ledger, used = [], [], []
    for _ in range(rng.randint(low, high)):
        op, index = rng.choice(OPS), rng.randrange(len(RECORDS))
        prior = log[-1]["entry_id"] if log else GENESIS
        entry = _entry(len(log) + 1, op, index, prior)
        log.append(entry)
        if keyed_every or rng.random() < 0.6:
            key = rng.choice([k for k in KEYS if k not in used] or ["fresh"])
            used.append(key)
            token = _fp(op, IDENTS[index], RECORDS[index])
            ledger.append(
                {
                    "receipt_id": _rid(key, token, entry["entry_id"], entry["sequence"]),
                    "idempotency_key": key,
                    "request_fingerprint": token,
                    "entry_id": entry["entry_id"],
                    "sequence": entry["sequence"],
                }
            )
    return log, ledger, used


def _fresh_key(used):
    return next(k for k in (*KEYS, "fresh-0", "fresh-1") if k not in used)


def _case_of(label, log, ledger, request, oracle=None, expect=None, model=None):
    return {
        "label": label,
        "log": log,
        "ledger": ledger,
        "request": request,
        "oracle": oracle,
        "expect": expect,
        "model": model,
    }


def _clean(rng, low=0, high=5):
    """A clean case with its model result: fresh, replay or conflict."""
    log, ledger, used = _store(rng, low, high, keyed_every=rng.random() < 0.5)
    mode = rng.choice(("fresh", "replay", "conflict")) if ledger else "fresh"
    if mode == "fresh":
        key, op, index = _fresh_key(used), rng.choice(OPS), rng.randrange(len(RECORDS))
    else:
        receipt = rng.choice(ledger)
        entry = log[receipt["sequence"] - 1]
        key, op = receipt["idempotency_key"], entry["op"]
        index = IDENTS.index(entry["payload"]["identity"])
        if mode == "conflict":
            index = (index + 1) % len(RECORDS)
    model = _model_apply(log, ledger, key, op, index)
    return log, ledger, _request(key, op, index), model, mode


# -- generators -------------------------------------------------------------------

LEAVES = (None, True, 1, 0.0, b"k1", ["k1"], {"k1": 1}, ("k1",), frozenset({"k1"}))
NEAR_KEYS = ("", " k1", "k1 ", "-k1", "k1\n", "z" * 129, "k/1", "k 1", "ké")
BAD_OPS = ("", "Put", "PUT", "upsert", "put ", "del")


def _corrupt_log(rng):
    log, ledger, used = _store(rng, 1, 4)
    log[rng.randrange(len(log))]["prior_entry_id"] = GENESIS.replace("wal0", "wal1")
    return log, ledger, used


def _hostile_mapping(rng, mapping, spot):
    """A copy of MAPPING damaged at the type level; returns (new, label)."""
    kind = rng.choice(("dict-subclass", "lying", "list", "key", "value"))
    if kind == "dict-subclass":
        return _Dict(mapping), f"{spot}-dict-subclass"
    if kind == "lying":
        return _Lying(mapping), f"{spot}-lying"
    if kind == "list":
        return _List(mapping.items()), f"{spot}-list-subclass"
    field = rng.choice(list(mapping))
    if kind == "key":
        form = rng.choice(KEY_FORMS)
        return {(_hkey(form, k) if k == field else k): v for k, v in mapping.items()}, (
            f"{spot}-key-{form}"
        )
    value = mapping[field]
    if type(value) is str:
        return {**mapping, field: _Str(value)}, f"{spot}-str-subclass-value"
    if type(value) is int:
        return {**mapping, field: _Int(value)}, f"{spot}-int-subclass-value"
    return {(k + "_x" if k == field else k): v for k, v in mapping.items()}, f"{spot}-renamed"


def _typed(mapping, kind, field):
    """MAPPING damaged at the type level by KIND at FIELD (deterministic)."""
    if kind == "dict-subclass":
        return _Dict(mapping)
    if kind == "lying":
        return _Lying(mapping)
    if kind == "list-subclass":
        return _List(mapping.items())
    if kind.startswith("key-"):
        form = kind[4:]
        return {(_hkey(form, k) if k == field else k): v for k, v in mapping.items()}
    value = mapping[field]
    return {**mapping, field: _Str(value) if type(value) is str else _Int(value)}


_SAMPLE_LOG, _SAMPLE_LEDGER, _ = _store(random.Random("sample"), 2, 2)
_SAMPLE_REQ = _request("k1", "put", 0)
HOSTILE_SPOTS = {
    "request": (_SAMPLE_REQ, MR),
    "payload": (_SAMPLE_REQ["payload"], MR),
    "record": (_SAMPLE_REQ["payload"]["record"], MR),
    "entry": (_SAMPLE_LOG[1], CS),
    "entry-payload": (_SAMPLE_LOG[1]["payload"], CS),
    "entry-record": (_SAMPLE_LOG[1]["payload"]["record"], CS),
    "receipt": (_SAMPLE_LEDGER[1], CL),
}


def _hostile_rows():
    rows = []
    for spot, (mapping, _cls) in HOSTILE_SPOTS.items():
        rows.extend((spot, kind, None) for kind in ("dict-subclass", "lying", "list-subclass"))
        for field, value in mapping.items():
            rows.extend((spot, f"key-{form}", field) for form in KEY_FORMS)
            if type(value) in (str, int):
                rows.append((spot, "subclass-value", field))
    return rows


HOSTILE_ROWS = _hostile_rows()


def _gen_hostile(seed):
    spot, kind, field = HOSTILE_ROWS[seed]
    log, ledger, used = _store(random.Random("sample"), 2, 2)
    request = _request("k1", "put", 0)
    cls = HOSTILE_SPOTS[spot][1]
    if spot == "request":
        request = _typed(request, kind, field)
    elif spot == "payload":
        request["payload"] = _typed(request["payload"], kind, field)
    elif spot == "record":
        request["payload"]["record"] = _typed(request["payload"]["record"], kind, field)
    elif spot == "entry":
        log[1] = _typed(log[1], kind, field)
    elif spot == "entry-payload":
        log[1]["payload"] = _typed(log[1]["payload"], kind, field)
    elif spot == "entry-record":
        log[1]["payload"]["record"] = _typed(log[1]["payload"]["record"], kind, field)
    else:
        ledger[1] = _typed(ledger[1], kind, field)
    label = f"hostile:{spot}:{kind}:{field}"
    return _case_of(label, log, ledger, request, expect=("err", cls))


def _gen_request(seed):
    rng = random.Random(f"request-{seed}")
    corrupt = rng.random() < 0.2
    if corrupt:
        log, ledger, used = _corrupt_log(rng)
        request = _request(_fresh_key(used), "put", 0)
    else:
        log, ledger, request, _model, _mode = _clean(rng)
    edit = rng.choice(("leaf", "key", "op", "payload", "record", "shape", "hostile"))
    if edit == "leaf":
        field = rng.choice(("idempotency_key", "op", "payload"))
        request[field] = rng.choice(LEAVES)
        label = f"leaf-{field}"
    elif edit == "key":
        request["idempotency_key"] = rng.choice(NEAR_KEYS)
        label = "near-key"
    elif edit == "op":
        request["op"] = rng.choice(BAD_OPS)
        label = "bad-op"
    elif edit == "payload":
        payload = request["payload"]
        sub = rng.choice(("drop", "add", "rename", "identity", "leaf"))
        if sub == "drop":
            del payload[rng.choice(("identity", "record"))]
        elif sub == "add":
            payload["extra"] = "x"
        elif sub == "rename":
            request["payload"] = {
                ("identity_x" if k == "identity" else k): v for k, v in payload.items()
            }
        elif sub == "identity":
            payload["identity"] = IDENTS[(IDENTS.index(payload["identity"]) + 1) % len(IDENTS)]
        else:
            payload[rng.choice(("identity", "record"))] = rng.choice(LEAVES)
        label = f"payload-{sub}"
    elif edit == "record":
        record = request["payload"]["record"]
        sub = rng.choice(("drop", "add", "rename", "digest", "fen", "variant"))
        if sub == "drop":
            del record[rng.choice(list(record))]
        elif sub == "add":
            record["extra"] = "x"
        elif sub == "rename":
            field = rng.choice(list(record))
            request["payload"]["record"] = {
                (k + "_x" if k == field else k): v for k, v in record.items()
            }
        elif sub == "digest":
            record["digest"] = record["digest"][:-1] + ("0" if record["digest"][-1] != "0" else "1")
        elif sub == "fen":
            record["snapshot_fen"] = record["snapshot_fen"].replace(" 0 1", " 3 9")
        else:
            record["variant"] = rng.choice(("Standard", "chess960x", ""))
        label = f"record-{sub}"
    elif edit == "shape":
        sub = rng.choice(("drop", "add", "rename"))
        if sub == "drop":
            del request[rng.choice(list(request))]
        elif sub == "add":
            request["extra"] = "x"
        else:
            field = rng.choice(list(request))
            request = {(k + "_x" if k == field else k): v for k, v in request.items()}
        label = f"request-{sub}"
    else:
        spot = rng.choice(("request", "payload", "record"))
        if spot == "request":
            request, label = _hostile_mapping(rng, request, spot)
        elif spot == "payload":
            request["payload"], label = _hostile_mapping(rng, request["payload"], spot)
        else:
            new, label = _hostile_mapping(rng, request["payload"]["record"], spot)
            request["payload"]["record"] = new
    return _case_of(f"request:{label}", log, ledger, request, expect=("err", MR))


def _gen_container(seed):
    rng = random.Random(f"container-{seed}")
    log, ledger, request, _model, _mode = _clean(rng, 1, 4)
    which = ("log", "ledger")[seed % 2]
    kind = ("list-subclass", "tuple", "dict", "none")[(seed // 2) % 4]
    target = log if which == "log" else ledger
    new = {
        "list-subclass": lambda: _LogList(target),
        "tuple": lambda: tuple(target),
        "dict": lambda: dict(enumerate(target)),
        "none": lambda: None,
    }[kind]()
    if which == "log":
        log = new
    else:
        ledger = new
    cls = CS if which == "log" else CL
    return _case_of(f"container:{which}-{kind}", log, ledger, request, expect=("err", cls))


SOURCE_TAMPERS = (
    "sequence",
    "prior",
    "entry-id",
    "op",
    "identity",
    "digest",
    "drop-field",
    "extra-field",
    "renamed-field",
    "swap",
    "entry-hostile",
    "payload-hostile",
    "record-hostile",
    "not-a-dict",
)


def _tamper_log(log, kind, rng):
    i = rng.randrange(len(log))
    entry = log[i]
    if kind == "sequence":
        entry["sequence"] += rng.choice((1, -1, len(log)))
    elif kind == "prior":
        entry["prior_entry_id"] = "wal1:" + "f" * 64
    elif kind == "entry-id":
        entry["entry_id"] = entry["entry_id"][:-1] + ("0" if entry["entry_id"][-1] != "0" else "1")
    elif kind == "op":
        entry["op"] = rng.choice(BAD_OPS)
    elif kind == "identity":
        ident = entry["payload"]["identity"]
        entry["payload"]["identity"] = IDENTS[(IDENTS.index(ident) + 1) % len(IDENTS)]
    elif kind == "digest":
        rec = entry["payload"]["record"]
        rec["digest"] = rec["digest"][:-1] + ("0" if rec["digest"][-1] != "0" else "1")
    elif kind == "drop-field":
        del entry[rng.choice(list(entry))]
    elif kind == "extra-field":
        entry["extra"] = 1
    elif kind == "renamed-field":
        field = rng.choice(list(entry))
        log[i] = {(k + "_x" if k == field else k): v for k, v in entry.items()}
    elif kind == "swap":
        if len(log) < 2:
            log.append(dict(log[0]))
        else:
            log[0], log[1] = log[1], log[0]
    elif kind == "entry-hostile":
        log[i], _label = _hostile_mapping(rng, entry, "entry")
    elif kind == "payload-hostile":
        entry["payload"], _label = _hostile_mapping(rng, entry["payload"], "payload")
    elif kind == "record-hostile":
        new, _label = _hostile_mapping(rng, entry["payload"]["record"], "record")
        entry["payload"]["record"] = new
    else:
        log[i] = rng.choice(LEAVES)


def _gen_source(seed):
    rng = random.Random(f"source-{seed}")
    log, ledger, request, _model, _mode = _clean(rng, 1, 5)
    kind = SOURCE_TAMPERS[seed % len(SOURCE_TAMPERS)]
    _tamper_log(log, kind, rng)
    return _case_of(f"source:{kind}", log, ledger, request, expect=("err", CS))


LEDGER_TAMPERS = (
    "receipt-id",
    "fingerprint",
    "entry-id",
    "sequence-high",
    "sequence-zero",
    "sequence-order",
    "sequence-bool",
    "duplicate-key",
    "key-grammar",
    "grammar-upper",
    "grammar-newline",
    "rebound-entry",
    "drop-field",
    "extra-field",
    "renamed-field",
    "receipt-hostile",
    "not-a-dict",
)


def _reforge(receipt, **changes):
    out = {**receipt, **changes}
    out["receipt_id"] = _rid(
        out["idempotency_key"], out["request_fingerprint"], out["entry_id"], out["sequence"]
    )
    return out


def _tamper_ledger(log, ledger, kind, rng):
    i = rng.randrange(len(ledger))
    receipt = ledger[i]
    if kind == "receipt-id":
        receipt["receipt_id"] = "idr1:" + "0" * 64
    elif kind == "fingerprint":
        ledger[i] = _reforge(receipt, request_fingerprint="idf1:" + "0" * 64)
    elif kind == "entry-id":
        ledger[i] = _reforge(receipt, entry_id="wal1:" + "0" * 64)
    elif kind == "sequence-high":
        ledger[i] = _reforge(receipt, sequence=len(log) + 1)
    elif kind == "sequence-zero":
        ledger[i] = _reforge(receipt, sequence=rng.choice((0, -1)))
    elif kind == "sequence-order":
        if len(ledger) < 2:
            ledger.append(dict(receipt))
        else:
            ledger[0], ledger[-1] = ledger[-1], ledger[0]
    elif kind == "sequence-bool":
        ledger[i] = (
            _reforge(receipt, sequence=True)
            if receipt["sequence"] == 1
            else (_reforge(receipt, sequence=_Int(receipt["sequence"])))
        )
    elif kind == "duplicate-key":
        if len(ledger) < 2:
            ledger.append(dict(receipt))
        else:
            ledger[-1] = _reforge(ledger[-1], idempotency_key=ledger[0]["idempotency_key"])
    elif kind == "key-grammar":
        ledger[i] = _reforge(receipt, idempotency_key=rng.choice(NEAR_KEYS))
    elif kind == "grammar-upper":
        field = rng.choice(("receipt_id", "request_fingerprint", "entry_id"))
        receipt[field] = receipt[field].upper()
    elif kind == "grammar-newline":
        field = rng.choice(("receipt_id", "request_fingerprint", "entry_id"))
        receipt[field] = receipt[field] + "\n"
    elif kind == "rebound-entry":
        seq = receipt["sequence"]
        other = seq % len(log) + 1
        if other == seq:
            ledger[i] = _reforge(receipt, entry_id=log[0]["prior_entry_id"])
        else:
            ledger[i] = _reforge(receipt, sequence=other, entry_id=log[other - 1]["entry_id"])
    elif kind == "drop-field":
        del receipt[rng.choice(list(receipt))]
    elif kind == "extra-field":
        receipt["extra"] = 1
    elif kind == "renamed-field":
        field = rng.choice(list(receipt))
        ledger[i] = {(k + "_x" if k == field else k): v for k, v in receipt.items()}
    elif kind == "receipt-hostile":
        ledger[i], _label = _hostile_mapping(rng, receipt, "receipt")
    else:
        ledger[i] = rng.choice(LEAVES)


def _gen_ledger(seed):
    rng = random.Random(f"ledger-{seed}")
    log, ledger, used = _store(rng, 1, 5)
    kind = LEDGER_TAMPERS[seed % len(LEDGER_TAMPERS)]
    _tamper_ledger(log, ledger, kind, rng)
    request = _request(_fresh_key(used), rng.choice(OPS), rng.randrange(len(RECORDS)))
    return _case_of(f"ledger:{kind}", log, ledger, request, expect=None)


def _raise(kind):
    def oracle(op, payload):
        raise kind()

    return oracle


def _forged(error):
    def oracle(op, payload):
        raise error

    return oracle


FORGED = {
    **{
        f"forged-idempotency-{c}": idempotency.IdempotencyError(c, FAILURE_MAPPING[c])
        for c in sorted(FAILURE_MAPPING)
    },
    **{f"forged-wal-{c}": _wal.WalError(c, WAL_MAPPING[c]) for c in sorted(WAL_MAPPING)},
    "forged-UnicodeEncodeError": UnicodeEncodeError("utf-8", "\udcff", 0, 1, "forged"),
    "forged-builtin-ValueError": ValueError("forged"),
}
_KINDS = {
    f"raise-{k.__name__}": k
    for k in (
        ValueError,
        KeyError,
        RuntimeError,
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
        MemoryError,
        RecursionError,
    )
}


def _honest(op, payload):
    return _fp(op, payload["identity"], payload["record"])


def _scribbler(op, payload):
    out = _honest(op, payload)
    payload["identity"] = "scribbled"
    payload["record"]["digest"] = "scribbled"
    payload["extra"] = 1
    return out


OUTPUTS = {
    "none": lambda op, p: None,
    "bytes": lambda op, p: _honest(op, p).encode(),
    "str-subclass": lambda op, p: _Str(_honest(op, p)),
    "upper": lambda op, p: _honest(op, p).upper(),
    "trailing-newline": lambda op, p: _honest(op, p) + "\n",
    "trailing-junk": lambda op, p: _honest(op, p) + "x",
    "other-request": lambda op, p: _fp(
        "delete" if op == "put" else "put", p["identity"], p["record"]
    ),
    "constant": lambda op, p: "idf1:" + "0" * 64,
    "op-only": lambda op, p: "idf1:" + hashlib.sha256(op.encode()).hexdigest(),
    "honest": _honest,
    "scribble-args": _scribbler,
}
HONEST_OUTPUTS = {"honest", "scribble-args"}
ORACLE_FAULTS = (*_KINDS, *sorted(FORGED), *OUTPUTS)
SPOTS = ("fresh", "replay", "conflict")


def _oracle_for(fault):
    if fault in _KINDS:
        return _raise(_KINDS[fault])
    if fault in FORGED:
        return _forged(FORGED[fault])
    return OUTPUTS[fault]


def _spot_case(rng, spot):
    for _ in range(64):
        log, ledger, request, model, mode = _clean(rng, 1, 5)
        if mode == spot:
            return log, ledger, request, model
    raise AssertionError(spot)


def _gen_oracle(seed):
    fault, spot = ORACLE_FAULTS[seed // len(SPOTS)], SPOTS[seed % len(SPOTS)]
    rng = random.Random(f"oracle-{seed}")
    log, ledger, request, model = _spot_case(rng, spot)
    fn = _oracle_for(fault)
    if fault in HONEST_OUTPUTS:
        expect = model[:2] if model[0] == "err" else (model[0],)
    else:
        expect = ("err", DF)
    return _case_of(
        f"oracle:{fault}@{spot}",
        log,
        ledger,
        request,
        oracle=lambda log, ledger, request, fn=fn: fn,
        expect=expect,
        model=model if fault in HONEST_OUTPUTS else None,
    )


SCRAMBLES = (
    "log-append",
    "log-clear",
    "entry-edit",
    "ledger-append",
    "ledger-clear",
    "receipt-edit",
    "request-key",
    "request-payload",
    "record-edit",
)
ACTIONS = ("honest", "raise", "divergent")


def _scramble(kind, log, ledger, request):
    if kind == "log-append":
        log.append({"junk": 1})
    elif kind == "log-clear":
        log.clear()
    elif kind == "entry-edit" and log:
        log[0]["op"] = "scribbled"
        log[0]["payload"]["record"]["digest"] = "scribbled"
    elif kind == "ledger-append":
        ledger.append({"junk": 1})
    elif kind == "ledger-clear":
        ledger.clear()
    elif kind == "receipt-edit" and ledger:
        ledger[0]["idempotency_key"] = "scribbled"
    elif kind == "request-key":
        request["idempotency_key"] = "scribbled"
    elif kind == "request-payload":
        request["payload"] = {"junk": 1}
    elif kind == "record-edit":
        request["payload"]["record"]["snapshot_fen"] = "scribbled"


def _gen_live(seed):
    rng = random.Random(f"live-{seed}")
    kind, action = SCRAMBLES[seed % len(SCRAMBLES)], ACTIONS[(seed // len(SCRAMBLES)) % 3]
    log, ledger, request, model, _mode = _clean(rng, 1, 5)

    def factory(live_log, live_ledger, live_request, kind=kind, action=action):
        def oracle(op, payload):
            _scramble(kind, live_log, live_ledger, live_request)
            if action == "raise":
                raise RuntimeError("after scramble")
            if action == "divergent":
                return "idf1:" + "0" * 64
            return _honest(op, payload)

        return oracle

    if action == "honest":
        expect = model[:2] if model[0] == "err" else (model[0],)
    else:
        expect = ("err", DF)
    return _case_of(
        f"live:{kind}:{action}",
        log,
        ledger,
        request,
        oracle=factory,
        expect=expect,
        model=model if action == "honest" else None,
    )


def _gen_sequence(seed):
    """The LAST apply of a model-built run; earlier steps become the store."""
    rng = random.Random(f"sequence-{seed}")
    log, ledger, history = [], [], []
    steps = rng.randint(1, 8)
    for step in range(steps):
        mode = rng.choice(("fresh", "replay", "conflict")) if history else "fresh"
        if mode == "fresh":
            used = [h[0] for h in history]
            key, op, index = _fresh_key(used), rng.choice(OPS), rng.randrange(len(RECORDS))
        else:
            key, op, index = rng.choice(history)
            if mode == "conflict":
                index = (index + rng.randrange(1, len(RECORDS))) % len(RECORDS)
        model = _model_apply(log, ledger, key, op, index)
        if step == steps - 1:
            expect = model[:2] if model[0] == "err" else (model[0],)
            return _case_of(
                f"sequence:{mode}",
                log,
                ledger,
                _request(key, op, index),
                expect=expect,
                model=model,
            )
        if model[0] == "applied":
            log, ledger = model[2], model[3]
            history.append((key, op, index))
    raise AssertionError("unreachable")


GEN = {
    "request": _gen_request,
    "container": _gen_container,
    "source": _gen_source,
    "ledger": _gen_ledger,
    "hostile": _gen_hostile,
    "oracle": _gen_oracle,
    "live": _gen_live,
    "sequence": _gen_sequence,
}
FUZZ_SEEDS["oracle"] = len(ORACLE_FAULTS) * len(SPOTS)
FUZZ_SEEDS["hostile"] = len(HOSTILE_ROWS)
PROBE = {
    g: FUZZ_SEEDS[g] if g in ("oracle", "hostile") else min(PROBE_SEEDS, FUZZ_SEEDS[g])
    for g in GENERATORS
}


def _case(gen, seed):
    return GEN[gen](seed)


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


def _deep(value):
    """Value, exact type, key order and object identity at every level."""
    if isinstance(value, dict):
        return (
            "d",
            type(value).__name__,
            _Pin(value),
            tuple(
                (type(k).__name__, _Pin(k) if type(k) is not str else k, _deep(v))
                for k, v in dict.items(value)
            ),
        )
    if isinstance(value, list):
        return (
            "l",
            type(value).__name__,
            _Pin(value),
            tuple(_deep(v) for v in list.__iter__(value)),
        )
    if isinstance(value, tuple):
        return ("t", _Pin(value), tuple(_deep(v) for v in value))
    if isinstance(value, str) and type(value) is not str:
        return ("s", type(value).__name__, _Pin(value))
    if isinstance(value, int) and type(value) not in (int, bool):
        return ("i", type(value).__name__, _Pin(value))
    return ("v", type(value).__name__, value if type(value) is not frozenset else _Pin(value))


def _exact_args(op, payload):
    return (
        type(op) is str
        and type(payload) is dict
        and list(payload) == ["identity", "record"]
        and type(payload["identity"]) is str
        and type(payload["record"]) is dict
        and all(type(k) is str and type(v) is str for k, v in payload["record"].items())
    )


def _run(module, case):
    """(outcome, before, after, case_objects, calls, raised, hostile)."""
    log = copy.deepcopy(case["log"])
    ledger = copy.deepcopy(case["ledger"])
    request = copy.deepcopy(case["request"])
    inner = _honest if case["oracle"] is None else case["oracle"](log, ledger, request)
    calls = []

    def oracle(op, payload):
        calls.append((op, payload, _exact_args(op, payload)))
        return inner(op, payload)

    engine = module.IdempotencyEngine(oracle)
    before = (_deep(log), _deep(ledger), _deep(request))
    live = (copy.deepcopy(log), copy.deepcopy(ledger))
    raised = None
    HOSTILE.clear()
    _Armed.on = True
    try:
        outcome = ("ok", engine.apply(log, ledger, request))
    except (idempotency.IdempotencyError, _reference.IdempotencyError) as error:
        outcome = ("err", error.failure_class, error.code)
        raised = error
    except BaseException as error:  # noqa: BLE001 - raw escape is a defect
        outcome = ("raw", type(error).__name__)
    finally:
        _Armed.on = False
    hostile = list(HOSTILE)
    after = (_deep(log), _deep(ledger), _deep(request))
    return outcome, before, after, (log, ledger, request, live), calls, raised, hostile


@functools.cache
def _reference_outcome(gen, seed):
    out = _run(_reference, _case(gen, seed))[0]
    return out if out[0] != "ok" else ("ok", repr(out[1]))


def _check(module, gen, seed):
    """Failing reasons for one case through MODULE (empty when it holds)."""
    case = _case(gen, seed)
    outcome, before, after, objs, calls, raised, hostile = _run(module, case)
    log, ledger, request, live = objs
    if outcome[0] == "raw":
        return [f"raw {outcome[1]}"]
    why = []
    if outcome[0] == "err" and (
        outcome[1] not in CLASSES or outcome[2] != FAILURE_MAPPING.get(outcome[1])
    ):
        why.append(f"untyped {outcome[1:]}")
    if any(raised is forged for forged in FORGED.values()):
        why.append("forged error object passed through")
    if raised is not None and (raised.__cause__ is not None or raised.__context__ is not None):
        why.append("typed error chained")
    ref = _reference_outcome(gen, seed)
    mine = outcome if outcome[0] != "ok" else ("ok", repr(outcome[1]))
    if mine[:2] != ref[:2]:
        why.append(f"reference disagrees {mine[:2]!r}"[:160])
    expect = case["expect"]
    if expect is not None:
        got = outcome[:2] if outcome[0] == "err" else (outcome[1]["outcome"],)
        if got != expect:
            why.append(f"expected {expect}, got {got}")
    if hostile:
        why.append(f"hostile calls {sorted(set(hostile))}")
    if after[2] != before[2]:
        why.append("request not restored")
    early = outcome[0] == "err" and outcome[1] in (MR, CS, CL)
    if early and calls:
        why.append("fingerprinter called before a request, source or ledger rejection")
    if not early and len(calls) != 1:
        why.append(f"fingerprinter called {len(calls)} times")
    for _op, payload, exact in calls:
        if not exact:
            why.append("fingerprinter arguments not exact built-in types")
        if type(request) is dict and (
            payload is request.get("payload")
            or (
                type(request.get("payload")) is dict
                and payload.get("record") is request["payload"].get("record")
            )
        ):
            why.append("fingerprinter received the caller's live payload")
    unchanged = outcome[0] == "err" or outcome[1].get("outcome") == "replayed"
    if unchanged and after[:2] != before[:2]:
        why.append("log or ledger not restored")
    if outcome[0] == "ok":
        result = outcome[1]
        if list(result) != ["outcome", "receipt"] or result["outcome"] not in (
            "applied",
            "replayed",
        ):
            why.append("result shape")
        model = case["model"]
        known = model is not None and model[0] != "err"
        if known and result != {"outcome": model[0], "receipt": model[1]}:
            why.append("result differs from the model")
        if result.get("outcome") == "applied":
            live_log, live_ledger = live
            known = model is not None and model[0] == "applied"
            if known and (log != model[2] or ledger != model[3]):
                why.append("log or ledger differs from the model")
            if len(log) != len(live_log) + 1 or len(ledger) != len(live_ledger) + 1:
                why.append("applied did not append exactly one entry and receipt")
            if before[0][3] != after[0][3][:-1] or before[1][3] != after[1][3][:-1]:
                why.append("existing entries or receipts disturbed")
            if ledger and ledger[-1] is result["receipt"]:
                why.append("returned receipt shares the stored receipt")
            if ledger and ledger[-1] != result["receipt"]:
                why.append("stored receipt differs from the returned one")
        elif ledger and any(r is result["receipt"] for r in ledger):
            why.append("replayed receipt shares the stored receipt")
    if outcome[0] == "err" and not _unpoisoned(module):
        why.append("poisoned after rejection")
    return why


def _unpoisoned(module):
    log, ledger = [], []
    engine = module.IdempotencyEngine(_honest)
    try:
        first = engine.apply(log, ledger, _request("k1", "put", 2))
        again = engine.apply(log, ledger, _request("k1", "put", 2))
    except BaseException:  # noqa: BLE001 - any failure is poisoning
        return False
    want = _model_apply([], [], "k1", "put", 2)
    return first == {"outcome": "applied", "receipt": want[1]} and again == {
        "outcome": "replayed",
        "receipt": want[1],
    }


# -- fuzz tests -------------------------------------------------------------------


def _chunks(gen, size=48):
    total = FUZZ_SEEDS[gen]
    return [(gen, start, min(start + size, total)) for start in range(0, total, size)]


@pytest.mark.parametrize("gen,start,stop", [c for g in GENERATORS for c in _chunks(g)])
def test_fuzz_cases_hold(gen, start, stop):
    failures = []
    for seed in range(start, stop):
        why = _check(idempotency, gen, seed)
        if why:
            failures.append((_case(gen, seed)["label"], why))
    assert failures == []


@pytest.mark.parametrize("seed", range(24))
def test_happy_runs_match_the_model_step_by_step(seed):
    rng = random.Random(f"happy-{seed}")
    engine = idempotency.IdempotencyEngine(idempotency.request_fingerprint)
    log, ledger, mlog, mledger, history = [], [], [], [], []
    for _ in range(rng.randint(2, 10)):
        mode = rng.choice(("fresh", "replay", "conflict")) if history else "fresh"
        if mode == "fresh":
            key = _fresh_key([h[0] for h in history])
            op, index = rng.choice(OPS), rng.randrange(len(RECORDS))
        else:
            key, op, index = rng.choice(history)
            if mode == "conflict":
                index = (index + 1) % len(RECORDS)
        model = _model_apply(mlog, mledger, key, op, index)
        request = _request(key, op, index)
        snap = copy.deepcopy(request)
        if model[0] == "err":
            with pytest.raises(idempotency.IdempotencyError) as exc:
                engine.apply(log, ledger, request)
            assert exc.value.failure_class == model[1] and exc.value.__cause__ is None
        else:
            got = engine.apply(log, ledger, request)
            assert got == {"outcome": model[0], "receipt": model[1]}
            mlog, mledger = model[2], model[3]
            if model[0] == "applied":
                history.append((key, op, index))
        assert request == snap and log == mlog and ledger == mledger


def test_boundaries():
    engine = idempotency.IdempotencyEngine(idempotency.request_fingerprint)
    # empty store; the key grammar's edges; a ledger covering a subset
    for key in ("9", "z" * 128, "A" + "._:-" * 31 + "abc"):
        log, ledger = [], []
        got = engine.apply(log, ledger, _request(key, "delete", 6))
        assert got["receipt"] == _model_apply([], [], key, "delete", 6)[1]
    log, ledger, used = _store(random.Random("subset"), 4, 4, keyed_every=False)
    model = _model_apply(log, ledger, _fresh_key(used), "put", 0)
    assert engine.apply(log, ledger, _request(_fresh_key(used), "put", 0))["receipt"] == model[1]
    # rollback: a log rolled back past a receipt fails the stale ledger closed
    log, ledger, used = _store(random.Random("rollback"), 3, 3)
    for cut in range(3):
        with pytest.raises(idempotency.IdempotencyError) as exc:
            engine.apply(
                log[:cut],
                [dict(r) for r in ledger],
                _request(
                    ledger[0]["idempotency_key"],
                    log[0]["op"],
                    IDENTS.index(log[0]["payload"]["identity"]),
                ),
            )
        assert exc.value.failure_class == CL and exc.value.__cause__ is None


@pytest.mark.parametrize("gen", GENERATORS)
def test_determinism_by_value(gen):
    for seed in range(0, FUZZ_SEEDS[gen], 5):
        first = _run(idempotency, _case(gen, seed))[0]
        again = _run(idempotency, _case(gen, seed))[0]
        assert repr(first) == repr(again), _case(gen, seed)["label"]


def test_corpus_reaches_every_class_and_every_edit():
    classes, oks = set(), 0
    for gen in GENERATORS:
        for seed in range(FUZZ_SEEDS[gen]):
            outcome = _reference_outcome(gen, seed)
            if outcome[0] == "err":
                classes.add(outcome[1])
            else:
                oks += 1
    assert classes == CLASSES and oks >= 60
    labels = {_case(g, s)["label"] for g in GENERATORS for s in range(FUZZ_SEEDS[g])}
    assert {f"source:{t}" for t in SOURCE_TAMPERS} <= labels
    assert {f"ledger:{t}" for t in LEDGER_TAMPERS} <= labels
    assert {f"oracle:{f}@{s}" for f in ORACLE_FAULTS for s in SPOTS} <= labels
    assert {f"live:{k}:{a}" for k in SCRAMBLES for a in ACTIONS} <= labels
    heads = {label.rsplit(":", 1)[0] for label in labels if label.startswith("hostile:")}
    for spot in HOSTILE_SPOTS:
        for kind in ("dict-subclass", "lying", "list-subclass", "subclass-value"):
            assert f"hostile:{spot}:{kind}" in heads, (spot, kind)
        for form in KEY_FORMS:
            assert f"hostile:{spot}:key-{form}" in heads, (spot, form)
    probe = {
        _reference_outcome(g, s)[1]
        for g in GENERATORS
        for s in range(PROBE[g])
        if _reference_outcome(g, s)[0] == "err"
    }
    assert probe == CLASSES


def test_forges_cover_every_class_the_module_raises_or_catches():
    caught = set()
    for node in ast.walk(ast.parse(SOURCE_TEXT)):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            elts = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            caught.update(ast.unparse(e) for e in elts)
    assert caught == {"BaseException", "_wal.WalError", "UnicodeEncodeError"}
    forged = {type(e).__name__ for e in FORGED.values()}
    assert {"IdempotencyError", "WalError", "UnicodeEncodeError"} <= forged
    assert {f"forged-idempotency-{c}" for c in CLASSES} <= set(FORGED)
    assert {f"forged-wal-{c}" for c in WAL_MAPPING} <= set(FORGED)


# -- mutation check ---------------------------------------------------------------


def _source_mutant(name, edits):
    """store/idempotency.py with EDITS (old, new) applied, each matching
    exactly once, executed as a fresh module; IdempotencyError and _fail are
    rebound to the production class so typed failures stay comparable. An
    edit ("wal", old, new) instead applies to a fresh copy of store/wal.py
    that the mutant links in place of the production WAL, for mutants that
    remove a guard the linked layer repeats."""
    text = SOURCE_TEXT
    wal_edits = [e[1:] for e in edits if len(e) == 3]
    for old, new in (e for e in edits if len(e) == 2):
        assert text.count(old) == 1, (name, old)
        text = text.replace(old, new)
    module = types.ModuleType("store._t0262_mutant")
    module.__file__ = str(ROOT / "store" / "idempotency.py")
    exec(compile(text, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.IdempotencyError = idempotency.IdempotencyError

    def _fail(failure_class):
        raise idempotency.IdempotencyError(failure_class, FAILURE_MAPPING[failure_class])

    module._fail = _fail
    if wal_edits:
        wal_text = WAL_TEXT
        for old, new in wal_edits:
            assert wal_text.count(old) == 1, (name, old)
            wal_text = wal_text.replace(old, new)
        linked = types.ModuleType("store._t0262_wal_mutant")
        linked.__file__ = str(ROOT / "store" / "wal.py")
        exec(compile(wal_text, f"<wal mutant {name}>", "exec"), linked.__dict__)  # noqa: S102
        module._wal = linked
    return module


def _probe(module):
    failures = []
    for gen in GENERATORS:
        for seed in range(PROBE[gen]):
            try:
                why = _check(module, gen, seed)
            except BaseException as error:  # noqa: BLE001
                why = [f"check raised {type(error).__name__}"]
            if why:
                failures.append(_case(gen, seed)["label"])
    return failures


_BE = "        except BaseException:\n            failed = True\n"
_OUT = "        if type(out) is not str or _FP_RE.fullmatch(out) is None:"
_BIND = '        if out != request_fingerprint(frozen_req["op"], payload):'
_REQ = (
    "        if type(request) is not dict or not _str_keyed(request) or \\\n"
    "                set(dict.keys(request)) != _REQUEST_KEYS:"
)
_WAL_REQ = (
    "        except _wal.WalError:\n            rejected = True\n"
    '        if rejected:\n            _fail("malformed_idempotency_request")'
)
_REPLAY = "            self._wal.replay(log)\n"
_RECEIPT = (
    "            if type(receipt) is not dict or not _str_keyed(receipt) or \\\n"
    "                    set(dict.keys(receipt)) != _FIELDS:"
)
_SEQ = "            if type(seq) is not int or seq <= last_seq or seq > len(log):"
_BINDING = (
    '            if entry["entry_id"] != entry_id or \\\n'
    '                    request_fingerprint(entry["op"], entry["payload"]) != fp:'
)
_DEDUP = '                if stored["request_fingerprint"] != token:'

MUTANTS = {
    "fingerprinter-boundary-narrow": [(_BE, _BE.replace("BaseException", "Exception"))],
    "fingerprinter-forged-passes-through": [
        (_BE, "        except idempotency_error_passthrough:\n            raise\n" + _BE),
        ("class IdempotencyError(Exception):", "class IdempotencyError(Exception):  # noqa"),
        (
            "def _fail(failure_class):",
            "idempotency_error_passthrough = (Exception,)\n\n\ndef _fail(failure_class):",
        ),
    ],
    "fingerprinter-error-chained": [
        (
            "        except BaseException:\n            failed = True\n        if failed:\n"
            '            _fail("divergent_fingerprint")',
            "        except BaseException as exc:\n"
            '            raise IdempotencyError("divergent_fingerprint",'
            ' FAILURE_MAPPING["divergent_fingerprint"]) from exc',
        )
    ],
    "fingerprinter-fail-inside-except": [
        (
            "        except BaseException:\n            failed = True\n        if failed:\n"
            '            _fail("divergent_fingerprint")',
            '        except BaseException:\n            _fail("divergent_fingerprint")',
        )
    ],
    "request-rejection-inside-except": [
        (
            "        except _wal.WalError:\n            rejected = True\n        if rejected:\n"
            '            _fail("malformed_idempotency_request")',
            '        except _wal.WalError:\n            _fail("malformed_idempotency_request")',
        )
    ],
    "source-rejection-inside-except": [
        (
            "        except _wal.WalError:\n            rejected = True\n        if rejected:\n"
            '            _fail("corrupt_source")',
            '        except _wal.WalError:\n            _fail("corrupt_source")',
        )
    ],
    "output-isinstance": [(_OUT, _OUT.replace("type(out) is not str", "not isinstance(out, str)"))],
    "output-not-bound": [(_BIND, "        if False:")],
    "fingerprinter-called-twice": [
        (
            "            token = self._fingerprint(frozen_req)\n",
            "            token = self._fingerprint(frozen_req)\n"
            "            token = self._fingerprint(frozen_req)\n",
        )
    ],
    "fingerprinter-gets-live-record": [
        (
            '                {"identity": payload["identity"],\n'
            '                 "record": dict(payload["record"])})',
            '                {"identity": payload["identity"],\n'
            '                 "record": payload["record"]})',
        )
    ],
    "request-keys-by-length": [
        (_REQ, "        if type(request) is not dict or len(request) != len(_REQUEST_KEYS):")
    ],
    "request-dict-isinstance": [
        (_REQ, _REQ.replace("type(request) is not dict", "not isinstance(request, dict)"))
    ],
    "request-key-types-unchecked": [(_REQ, _REQ.replace(" or not _str_keyed(request)", ""))],
    "key-grammar-match": [
        (
            "    return type(key) is str and _KEY_RE.fullmatch(key) is not None",
            "    return type(key) is str and _KEY_RE.match(key) is not None",
        )
    ],
    "key-type-isinstance": [
        (
            "    return type(key) is str and _KEY_RE.fullmatch(key) is not None",
            "    return isinstance(key, str) and _KEY_RE.fullmatch(key) is not None",
        )
    ],
    "payload-not-validated": [(_WAL_REQ, "        except _wal.WalError:\n            pass")],
    "payload-key-guard-off": [
        (
            '        if type(request["op"]) is not str or \\\n'
            '                not _payload_str_keyed(request["payload"]):',
            '        if type(request["op"]) is not str:',
        ),
        _WAL_GUARD,
    ],
    "log-key-guard-off": [
        ("        if not _log_str_keyed(log):\n", "        if False:\n"),
        _WAL_GUARD,
    ],
    "log-not-replayed": [(_REPLAY, "            pass\n")],
    "ledger-not-list-checked": [
        ('        if type(ledger) is not list:\n            _fail("corrupt_ledger")\n', "")
    ],
    "receipt-fields-by-length": [
        (
            _RECEIPT,
            "            if type(receipt) is not dict or len(receipt) != len(_FIELDS):",
        )
    ],
    "receipt-key-types-unchecked": [
        (_RECEIPT, _RECEIPT.replace(" or not _str_keyed(receipt)", ""))
    ],
    "sequence-bound-off": [(_SEQ, _SEQ.replace(" or seq > len(log)", ""))],
    "sequence-order-off": [(_SEQ, _SEQ.replace(" or seq <= last_seq", " or seq <= 0"))],
    "sequence-int-isinstance": [
        (_SEQ, _SEQ.replace("type(seq) is not int", "not isinstance(seq, int)"))
    ],
    "duplicate-key-allowed": [
        ('            if key in seen_keys:\n                _fail("corrupt_ledger")\n', "")
    ],
    "receipt-id-not-rederived": [
        (
            "            if rid != derive_receipt_id(key, fp, entry_id, seq):",
            "            if False:",
        )
    ],
    "entry-binding-off": [(_BINDING, "            if False:")],
    "entry-fingerprint-binding-off": [(_BINDING, '            if entry["entry_id"] != entry_id:')],
    "dedup-key-only": [(_DEDUP, "                if False:")],
    "dedup-fingerprint-only": [
        (
            '                if receipt["idempotency_key"] == frozen_req["idempotency_key"]:',
            '                if receipt["request_fingerprint"] == self._fp_probe:',
        ),
        (
            "            token = self._fingerprint(frozen_req)\n",
            "            token = self._fingerprint(frozen_req)\n"
            "            self._fp_probe = token\n",
        ),
    ],
    "log-not-restored": [("            _wal.restore(log, container, saved)\n", "")],
    "ledger-not-restored": [("            ledger[:] = ledger_container\n", "")],
    "receipts-not-restored": [
        (
            "            for obj, snap in receipts:\n                obj.clear()\n",
            "            for obj, snap in []:\n                obj.clear()\n",
        )
    ],
    "request-not-restored": [("            req.clear()\n            req.update(req_copy)\n", "")],
    "record-not-restored": [("            rec.clear()\n            rec.update(rec_copy)\n", "")],
    "result-receipt-shared": [
        (
            '        return {"outcome": outcome, "receipt": dict(receipt)}',
            '        return {"outcome": outcome, "receipt": receipt}',
        ),
        ("            ledger.append(dict(receipt))\n", "            ledger.append(receipt)\n"),
    ],
    "replay-returns-stored": [
        (
            '                outcome, receipt, staged = "replayed", dict(stored), None',
            '                outcome, receipt, staged = "replayed", '
            "ledger[frozen_ledger.index(stored)], None",
        ),
        (
            '        return {"outcome": outcome, "receipt": dict(receipt)}',
            '        return {"outcome": outcome, '
            '"receipt": receipt if staged is None else dict(receipt)}',
        ),
    ],
}

# Edits that must NOT change behavior; each is asserted green.
# output-match / output-grammar-off: after the exact-type check the only
# reads of the output before the byte-exact bind to the local derivation
# are the grammar check itself and the utf-8 encode, which fails only as
# divergent_fingerprint (flag pattern, fresh); the bind rejects any output
# that differs from the honest token, which is in the grammar, so a weaker
# grammar check still ends in divergent_fingerprint after the same single
# call. Unlike store.migration there are no further oracle calls.
# receipt-grammar-match: a grammar-valid prefix plus a trailing newline is
# never equal to a derived value; every check between the grammar check and
# the receipt-id rederivation and live-entry binding fails only as
# corrupt_ledger, so the ledger still fails as corrupt_ledger.
# stored-receipt-local: the stored receipt is a fresh local dict that is
# never returned (the result carries its own copy).
# first-matching-key: ledger keys are validated unique before dedup, so the
# first and the last receipt with the key are the same receipt.
_OUT_EDIT = "        if type(out) is not str or _FP_RE.fullmatch(out) is None:"
EQUIVALENT_EDITS = {
    "output-match": [(_OUT_EDIT, _OUT_EDIT.replace("_FP_RE.fullmatch(out)", "_FP_RE.match(out)"))],
    "output-grammar-off": [(_OUT_EDIT, "        if type(out) is not str:")],
    "receipt-grammar-match": [
        ("grammar.fullmatch(value) is None:", "grammar.match(value) is None:")
    ],
    "stored-receipt-local": [
        ("            ledger.append(dict(receipt))\n", "            ledger.append(receipt)\n")
    ],
    "first-matching-key": [
        (
            "                    stored = receipt\n",
            "                    stored = receipt\n                    break\n",
        )
    ],
}


def test_identity_source_mutant_is_green():
    module = _source_mutant("identity", [])
    assert module.IdempotencyEngine is not idempotency.IdempotencyEngine
    assert _probe(module) == []


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edits_stay_green(name):
    assert _probe(_source_mutant(name, EQUIVALENT_EDITS[name])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(_source_mutant(name, MUTANTS[name])) != [], name


class _PinProbeInt(int):
    pass


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original leaf and
    the second can land on its freed address, which a bare id() would
    miss. The fingerprint pins the original, so the swap goes red."""
    value = {"k": _PinProbeInt(7)}
    before = _deep(value)
    value["k"] = _PinProbeInt(7)
    value["k"] = _PinProbeInt(7)
    assert _deep(value) != before
