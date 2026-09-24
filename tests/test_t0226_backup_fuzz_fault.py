"""T0226 deterministic fuzz/fault battery for the production backup.

Seeded generators drive the shipped store.backup (T0224). Every input
log is built from an independent model of data/contracts/wal.yaml and
data/contracts/backup.yaml (entry-id derivation, put/delete fold,
bundle serialization and backup-id derivation are restated here, never
taken from production). The contract reference engine
(tests.test_t0221_backup_contract) is the differential oracle.

The bundle serializer is store.backup's only except-BaseException oracle
boundary. It forges a BackupError of EVERY class and a WalError (the
linked module's error) of EVERY class; each must fail closed as a FRESH
divergent_snapshot. Pass-through mutants per class are pinned in
FORGE_TARGETS.
"""

from __future__ import annotations

import builtins
import copy
import functools
import hashlib
import random
import types
from pathlib import Path

import pytest

from graph import diff
from graph.node import make_record, record_identity
from store import backup, wal
from tests import test_t0221_backup_contract as _contract

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SOURCE = (ROOT / "store" / "backup.py").read_text()

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
FIELDS = ("backup_id", "head", "state_id", "entry_count", "bundle")
COUNT_MAX = 2 ** 63 - 1
BACKUP_CLASSES = tuple(sorted(backup.FAILURE_MAPPING))
WAL_CLASSES = tuple(sorted(wal.FAILURE_MAPPING))
OK = "ok"


# -- independent model --------------------------------------------------------

def _entry_id(sequence, op, identity, record, prior):
    canonical = (f"{identity}\n{record['variant']}\n{record['digest']}\n"
                 f"{record['snapshot_fen']}")
    return "wal1:" + hashlib.sha256(
        f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


def _model_log(ops):
    log, prior = [], GENESIS
    for sequence, (op, index) in enumerate(ops, 1):
        entry_id = _entry_id(sequence, op, IDENTITIES[index], RECORDS[index],
                             prior)
        log.append({"entry_id": entry_id, "sequence": sequence, "op": op,
                    "payload": {"identity": IDENTITIES[index],
                                "record": dict(RECORDS[index])},
                    "prior_entry_id": prior})
        prior = entry_id
    return log


def _rechain(log, start):
    prior = log[start - 1]["entry_id"] if start else GENESIS
    for entry in log[start:]:
        entry["prior_entry_id"] = prior
        entry["entry_id"] = _entry_id(entry["sequence"], entry["op"],
                                      entry["payload"]["identity"],
                                      entry["payload"]["record"], prior)
        prior = entry["entry_id"]
    return log


def _fold(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _bundle(state):
    return "".join(
        f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key]))
        + "\n" for key in sorted(state))


def _backup_id(head, sid, count, bundle):
    return "bck1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{bundle}".encode()).hexdigest()


def _receipt(ops, bundle=None):
    state = _fold(ops)
    log = _model_log(ops)
    head = log[-1]["entry_id"] if log else GENESIS
    sid = diff.state_id(state)
    bundle = _bundle(state) if bundle is None else bundle
    return {"backup_id": _backup_id(head, sid, len(ops), bundle), "head": head,
            "state_id": sid, "entry_count": len(ops), "bundle": bundle}


def _ops(rng, low=0, high=24):
    return tuple((rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
                 for _ in range(rng.randrange(low, high + 1)))


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
    """Hashes like a real key; comparing it raises while armed."""

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


def _rekey(mapping, old, new):
    items = [(new if key == old else key, value) for key, value in mapping.items()]
    mapping.clear()
    mapping.update(items)
    return mapping


def _shape(value):
    """Value, exact type, key order and identity at every level."""
    if isinstance(value, dict):
        return (type(value), id(value),
                [(type(k), k if type(k) is str else id(k), _shape(v))
                 for k, v in dict.items(value)])
    if isinstance(value, list):
        return (type(value), id(value), [_shape(v) for v in list.__iter__(value)])
    return (type(value), value if not isinstance(value, _Colliding) else id(value))


# -- the serializer ------------------------------------------------------------

def _serializer(module, fault, calls, forged, live=None):
    """Honest serializer (independent), or FAULT; LIVE edits the caller's
    log first."""
    def serialize(state):
        calls.append(copy.deepcopy(state))
        if live is not None:
            live()
        honest = _bundle(state)
        if fault is None:
            return honest
        kind, arg = fault
        if kind == "forge-backup":
            forged[0] = module.BackupError(arg, backup.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-wal":
            forged[0] = wal.WalError(arg, wal.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "raise":
            raise getattr(builtins, arg)("hostile serializer")
        if kind == "scribble":
            state.clear()
            state["x"] = {"variant": "chess960"}
            return honest
        if kind == "value":
            return arg
        return {"none": None, "bytes": honest.encode(), "int": 7,
                "strsub": _Str(honest), "surrogate": honest + "\ud800",
                "list": [honest]}[kind]
    return serialize


# -- cases ---------------------------------------------------------------------

def _t(pos, key, value):
    def edit(log):
        log[pos][key] = value
        return log
    return edit


def _flip(value):
    return value[:-1] + ("0" if value[-1] != "0" else "1")


SOURCE_TAMPERS = {
    "sequence-plus-one": lambda log, p: _t(p, "sequence", p + 2)(log),
    "reforged-sequence": lambda log, p: _rechain(_t(p, "sequence", p + 2)(log), p),
    "sequence-true": lambda log, p: _t(p, "sequence", True)(log),
    "sequence-intsub": lambda log, p: _t(p, "sequence", _Int(p + 1))(log),
    "op-unknown": lambda log, p: _t(p, "op", "upsert")(log),
    "op-strsub": lambda log, p: _t(p, "op", _Str(log[p]["op"]))(log),
    "entry-id-flip": lambda log, p: _t(p, "entry_id", _flip(log[p]["entry_id"]))(log),
    "entry-id-newline": lambda log, p: _t(p, "entry_id", log[p]["entry_id"] + "\n")(log),
    "prior-flip": lambda log, p: _t(p, "prior_entry_id",
                                    _flip(log[p]["prior_entry_id"]))(log),
    "extra-key": lambda log, p: _t(p, "force", True)(log),
    "rename-sequence": lambda log, p: (_rekey(log[p], "sequence", "sequencx"), log)[1],
    "rename-identity": lambda log, p: (_rekey(log[p]["payload"], "identity",
                                              "identitx"), log)[1],
    "rename-digest": lambda log, p: (_rekey(log[p]["payload"]["record"], "digest",
                                            "digesx"), log)[1],
    "strsub-key": lambda log, p: (_rekey(log[p], "op", _Str("op")), log)[1],
    "colliding-key": lambda log, p: (_rekey(log[p], "op", _Colliding("op")), log)[1],
    "record-colliding-key": lambda log, p: (_rekey(log[p]["payload"]["record"],
                                                   "digest", _Colliding("digest")),
                                            log)[1],
    "digest-swap": lambda log, p: (log[p]["payload"]["record"].update(
        digest=RECORDS[(IDENTITIES.index(log[p]["payload"]["identity"]) + 1)
                       % len(RECORDS)]["digest"]), log)[1],
    "identity-strsub": lambda log, p: (log[p]["payload"].update(
        identity=_Str(log[p]["payload"]["identity"])), log)[1],
    "identity-other": lambda log, p: (log[p]["payload"].update(
        identity=IDENTITIES[(IDENTITIES.index(log[p]["payload"]["identity"]) + 1)
                            % len(IDENTITIES)]), log)[1],
    "record-dict-subclass": lambda log, p: (log[p]["payload"].update(
        record=_Dict(log[p]["payload"]["record"])), log)[1],
    "entry-dict-subclass": lambda log, p: (log.__setitem__(p, _Dict(log[p])), log)[1],
    "entry-none": lambda log, p: (log.__setitem__(p, None), log)[1],
    "entry-list": lambda log, p: (log.__setitem__(p, list(log[p])), log)[1],
    "duplicate": lambda log, p: (log.insert(p + 1, copy.deepcopy(log[p])), log)[1],
    "payload-swap": lambda log, p: (log[p].update(payload={
        "identity": IDENTITIES[(IDENTITIES.index(log[p]["payload"]["identity"]) + 1)
                               % len(IDENTITIES)],
        "record": dict(RECORDS[(IDENTITIES.index(log[p]["payload"]["identity"]) + 1)
                               % len(IDENTITIES)])}), log)[1],
}
NON_LIST_SOURCES = {"none": None, "tuple": (), "dict": {}, "str": "log", "int": 0,
                    "list-subclass": "SUB"}

SERIALIZER_FAULTS = (
    [("forge-backup", cls) for cls in BACKUP_CLASSES]
    + [("forge-wal", cls) for cls in WAL_CLASSES]
    + [("raise", exc) for exc in ("ValueError", "KeyboardInterrupt", "SystemExit",
                                  "GeneratorExit", "MemoryError", "RecursionError")]
    + [(kind, None) for kind in ("none", "bytes", "int", "strsub", "surrogate",
                                 "list")])
# any exact UTF-8 str is bound into the id as the bundle, never re-serialized
ACCEPTED_OUTPUTS = ("", "x", "\u00e9\u4e2d\U0001f600", "a\nb\n")

LIVE_EDITS = ("add-entry-key", "add-payload-key", "add-record-key", "change-op",
              "drop-record-field", "clear-record", "clear-entry", "append-entry",
              "insert-entry", "delete-entry", "reverse", "clear-log")
LIVE_ENDINGS = ("honest", "raise", "bytes")


def _live_edit(log, name):
    def edit():
        if not log:
            log.append({"x": 1})
            return
        entry = log[len(log) // 2]
        if name == "add-entry-key":
            entry["zz"] = 1
        elif name == "add-payload-key":
            entry["payload"]["zz"] = 1
        elif name == "add-record-key":
            entry["payload"]["record"]["zz"] = 1
        elif name == "change-op":
            entry["op"] = "delete" if entry["op"] == "put" else "put"
        elif name == "drop-record-field":
            entry["payload"]["record"].pop("digest")
        elif name == "clear-record":
            entry["payload"]["record"].clear()
        elif name == "clear-entry":
            entry.clear()
        elif name == "append-entry":
            log.append(copy.deepcopy(entry))
        elif name == "insert-entry":
            log.insert(0, {"entry_id": 1})
        elif name == "delete-entry":
            del log[0]
        elif name == "reverse":
            log.reverse()
        else:
            log.clear()
    return edit


def _verify_tampers():
    """name -> (edit(receipt) -> receipt, class or OK)."""
    t = {}
    mer, div = "malformed_backup_record", "divergent_backup"

    def add(name, edit, cls):
        t[name] = (edit, cls)

    def reforge(r):
        r["backup_id"] = _backup_id(r["head"], r["state_id"], r["entry_count"],
                                    r["bundle"])
        return r

    for name, value in (("none", None), ("list", []), ("str", "r"), ("int", 0)):
        add(f"receipt-{name}", lambda r, v=value: v, mer)
    add("receipt-dict-subclass", lambda r: _Dict(r), mer)
    add("receipt-empty", lambda r: {}, mer)
    add("receipt-extra-key", lambda r: {**r, "zz": 1}, mer)
    add("receipt-extra-non-ascii-key", lambda r: {**r, "\u00e9": 1}, mer)
    for field in FIELDS:
        add(f"missing-{field}", lambda r, f=field: (r.pop(f), r)[1], mer)
        add(f"rename-{field}", lambda r, f=field: _rekey(r, f, f[:-1] + "x"), mer)
        add(f"strsub-key-{field}", lambda r, f=field: _rekey(r, f, _Str(f)), mer)
        add(f"colliding-key-{field}",
            lambda r, f=field: _rekey(r, f, _Colliding(f)), mer)
    for field in ("backup_id", "head", "state_id"):
        for name, fn in (("strsub", _Str), ("int", lambda v: 1), ("none", lambda v: None),
                         ("bytes", lambda v: v.encode()),
                         ("newline", lambda v: v + "\n"),
                         ("leading-space", lambda v: " " + v),
                         ("upper", lambda v: v[:5] + v[5:].upper()),
                         ("short", lambda v: v[:-1]), ("long", lambda v: v + "0")):
            add(f"{field}-{name}", lambda r, f=field, fn=fn: (r.update({f: fn(r[f])}), r)[1],
                mer)
        add(f"{field}-flip", lambda r, f=field: (r.update({f: _flip(r[f])}), r)[1], div)
    for name, value in (("true", True), ("float", 1.0), ("str", "1"), ("intsub", _Int(1)),
                        ("minus-one", -1), ("over-max", COUNT_MAX + 1),
                        ("double-max", 2 * COUNT_MAX), ("huge", 2 ** 200),
                        ("none", None)):
        add(f"count-{name}", lambda r, v=value: (r.update(entry_count=v), r)[1], mer)
    add("count-max-reforged",
        lambda r: reforge((r.update(entry_count=COUNT_MAX), r)[1]) if r["entry_count"]
        else r, OK)
    add("count-changed", lambda r: (r.update(entry_count=r["entry_count"] + 1), r)[1], div)
    add("count-reforged",
        lambda r: reforge((r.update(entry_count=r["entry_count"] + 1), r)[1])
        if r["entry_count"] else r, OK)
    for name, value in (("int", 1), ("none", None), ("bytes", b"x")):
        add(f"bundle-{name}", lambda r, v=value: (r.update(bundle=v), r)[1], mer)
    add("bundle-strsub", lambda r: (r.update(bundle=_Str(r["bundle"])), r)[1], mer)
    add("bundle-surrogate", lambda r: (r.update(bundle=r["bundle"] + "\ud800"), r)[1], mer)
    add("bundle-changed", lambda r: (r.update(bundle=r["bundle"] + "x"), r)[1], div)
    add("bundle-reforged", lambda r: reforge((r.update(bundle=r["bundle"] + "x"), r)[1]),
        OK)
    add("genesis-positive-reforged",
        lambda r: reforge((r.update(head=GENESIS, entry_count=max(r["entry_count"], 1)),
                           r)[1]), div)
    add("zero-count-non-genesis-reforged",
        lambda r: reforge((r.update(entry_count=0, head="wal1:" + "a" * 64,
                                    state_id=backup.EMPTY_STATE_ID), r)[1]), div)
    add("zero-count-non-empty-state-reforged",
        lambda r: reforge((r.update(entry_count=0, head=GENESIS,
                                    state_id="gs1:" + "b" * 64), r)[1]), div)
    add("reordered", lambda r: dict(reversed(list(r.items()))), OK)
    return t


VERIFY_TAMPERS = _verify_tampers()

GENERATORS = {
    "happy": 64,
    "accepted": len(ACCEPTED_OUTPUTS) * 4,
    "source": len(SOURCE_TAMPERS) * 3 * 2,
    "container": len(NON_LIST_SOURCES),
    "serializer": len(SERIALIZER_FAULTS) * 3,
    "live": len(LIVE_EDITS) * len(LIVE_ENDINGS) * 2,
    "verify": len(VERIFY_TAMPERS) * 3,
}


def _case(gen, seed):
    """A fresh case: label, kind, a builder of fresh inputs and the
    independently known outcome."""
    rng = random.Random(f"t0226:{gen}:{seed}")
    if gen == "happy":
        ops = _ops(rng) if seed else ()
        return {"label": f"happy:{len(ops)}", "kind": "backup",
                "log": lambda: _model_log(ops), "expect": _receipt(ops),
                "state": _fold(ops)}
    if gen == "accepted":
        out = ACCEPTED_OUTPUTS[seed % len(ACCEPTED_OUTPUTS)]
        ops = _ops(rng)
        return {"label": f"accepted:{seed % len(ACCEPTED_OUTPUTS)}", "kind": "backup",
                "log": lambda: _model_log(ops), "fault": ("value", out),
                "expect": _receipt(ops, bundle=out), "state": _fold(ops)}
    if gen == "source":
        names = sorted(SOURCE_TAMPERS)
        name = names[seed % len(names)]
        where = ("first", "middle", "last")[(seed // len(names)) % 3]
        ops = _ops(rng, 3, 10)
        pos = {"first": 0, "middle": len(ops) // 2, "last": len(ops) - 1}[where]
        return {"label": f"source:{name}@{where}", "kind": "backup",
                "log": lambda: SOURCE_TAMPERS[name](_model_log(ops), pos),
                "expect": "corrupt_source", "calls": 0}
    if gen == "container":
        name = sorted(NON_LIST_SOURCES)[seed]
        value = NON_LIST_SOURCES[name]
        ops = _ops(rng, 1, 5)
        return {"label": f"container:{name}", "kind": "backup",
                "log": (lambda: _List(_model_log(ops))) if value == "SUB"
                else (lambda: copy.copy(value)),
                "expect": "malformed_backup_record", "calls": 0}
    if gen == "serializer":
        fault = SERIALIZER_FAULTS[seed % len(SERIALIZER_FAULTS)]
        ops = () if seed // len(SERIALIZER_FAULTS) == 0 else _ops(rng, 1, 12)
        return {"label": f"serializer:{fault[0]}:{fault[1]}", "kind": "backup",
                "log": lambda: _model_log(ops), "fault": fault,
                "expect": "divergent_snapshot", "calls": 1}
    if gen == "live":
        edit = LIVE_EDITS[seed % len(LIVE_EDITS)]
        ending = LIVE_ENDINGS[(seed // len(LIVE_EDITS)) % len(LIVE_ENDINGS)]
        ops = _ops(rng, 1, 10)
        fault = {"honest": None, "raise": ("raise", "KeyboardInterrupt"),
                 "bytes": ("bytes", None)}[ending]
        return {"label": f"live:{edit}:{ending}", "kind": "backup",
                "log": lambda: _model_log(ops), "fault": fault, "live": edit,
                "expect": _receipt(ops) if ending == "honest" else "divergent_snapshot",
                "state": _fold(ops), "calls": 1}
    names = sorted(VERIFY_TAMPERS)
    name = names[seed % len(names)]
    # a genesis head has no hex to flip or upper-case: head tampers use a
    # non-empty log
    empty = (seed // len(names)) == 0 and not name.startswith("head-")
    ops = () if empty else _ops(rng, 1, 12)
    edit, cls = VERIFY_TAMPERS[name]
    return {"label": f"verify:{name}", "kind": "verify",
            "receipt": lambda: edit(_receipt(ops)), "expect": cls}


# -- running a case --------------------------------------------------------------

def _outcome(module, case):
    """(outcome, reasons) of CASE through MODULE."""
    calls, forged, why = [], [None], []
    if case["kind"] == "backup":
        log = case["log"]()
        before = _shape(log)
        live = _live_edit(log, case["live"]) if "live" in case else None
        engine = module.BackupEngine(_serializer(module, case.get("fault"), calls,
                                                 forged, live))
        _ARMED[0] = True
        try:
            result = engine.backup(log)
        except module.BackupError as error:
            result = error
        except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
            return f"raw:{type(error).__name__}", [f"raw {type(error).__name__}"]
        finally:
            _ARMED[0] = False
        if _shape(log) != before:
            why.append("log changed")
        if "calls" in case and len(calls) != case["calls"]:
            why.append(f"{len(calls)} serializer calls")
        if "state" in case and calls and calls[0] != case["state"]:
            why.append("serializer saw a wrong state")
    else:
        receipt = case["receipt"]()
        before = _shape(receipt)
        engine = module.BackupEngine(_serializer(module, None, calls, forged))
        _ARMED[0] = True
        try:
            result = engine.verify(receipt)
        except module.BackupError as error:
            result = error
        except BaseException as error:  # noqa: BLE001
            return f"raw:{type(error).__name__}", [f"raw {type(error).__name__}"]
        finally:
            _ARMED[0] = False
        if _shape(receipt) != before:
            why.append("receipt changed")
        if calls:
            why.append("verify called the serializer")
        if type(result) is dict:
            if result is receipt or list(result) != list(FIELDS) or \
                    any(type(result[f]) is not type(receipt[f]) for f in FIELDS):
                why.append("verify output not detached, typed and ordered")
            result = OK if result == receipt else result
    if isinstance(result, BaseException):
        if type(result) is not module.BackupError or \
                result.code != backup.FAILURE_MAPPING.get(result.failure_class):
            why.append("untyped failure")
        if forged[0] is not None and result is forged[0]:
            why.append("forged error escaped")
        return result.failure_class, why
    return result, why


def _check(module, gen, seed):
    """Reasons CASE fails through MODULE (empty when it holds)."""
    case = _case(gen, seed)
    got, why = _outcome(module, case)
    if got != case["expect"]:
        why.append(f"got {got!r}, expected {case['expect']!r}")
    oracle = _oracle(gen, seed)
    if got != oracle:
        why.append(f"differs from the contract reference {oracle!r}")
    return why


@functools.cache
def _oracle(gen, seed):
    """The contract reference outcome (it never changes, so it is cached)."""
    return _outcome(_contract, _case(gen, seed))[0]


def _all_cases():
    return [(gen, seed) for gen, count in GENERATORS.items() for seed in range(count)]


# -- tests: fuzz -----------------------------------------------------------------

@pytest.mark.parametrize("gen", sorted(GENERATORS))
def test_fuzz_cases_hold(gen):
    bad = {}
    for seed in range(GENERATORS[gen]):
        why = _check(backup, gen, seed)
        if why:
            bad[_case(gen, seed)["label"]] = why
    assert not bad, bad


def test_determinism_by_value():
    for gen, seed in _all_cases()[::7]:
        a, _ = _outcome(backup, _case(gen, seed))
        b, _ = _outcome(backup, _case(gen, seed))
        assert a == b, (gen, seed)


def test_no_poisoning_after_rejections():
    engine = backup.BackupEngine(backup.serialize_bundle)
    ops = (("put", 0), ("put", 1), ("delete", 0))
    for gen in ("source", "serializer", "verify"):
        for seed in range(GENERATORS[gen]):
            _outcome(backup, _case(gen, seed))
    assert engine.backup(_model_log(ops)) == _receipt(ops)
    assert engine.verify(_receipt(ops)) == _receipt(ops)


def test_corpus_reaches_every_class_and_position():
    labels = {_case(gen, seed)["label"] for gen, seed in _all_cases()}
    expects = {_case(gen, seed)["expect"] for gen, seed in _all_cases()
               if type(_case(gen, seed)["expect"]) is str}
    assert set(BACKUP_CLASSES) | {OK} <= expects
    for name in SOURCE_TAMPERS:
        for where in ("first", "middle", "last"):
            assert f"source:{name}@{where}" in labels
    for cls in BACKUP_CLASSES:
        assert f"serializer:forge-backup:{cls}" in labels
    for cls in WAL_CLASSES:
        assert f"serializer:forge-wal:{cls}" in labels
    for edit in LIVE_EDITS:
        for ending in LIVE_ENDINGS:
            assert f"live:{edit}:{ending}" in labels


def test_every_boundary_error_class_is_forged():
    assert {a for k, a in SERIALIZER_FAULTS if k == "forge-backup"} == \
        set(backup.FAILURE_MAPPING)
    assert {a for k, a in SERIALIZER_FAULTS if k == "forge-wal"} == \
        set(wal.FAILURE_MAPPING)
    assert set(FORGE_TARGETS) <= set(MUTANTS)


def test_count_bound_from_both_sides():
    ops = (("put", 0),)
    engine = backup.BackupEngine(backup.serialize_bundle)
    for count, cls in ((COUNT_MAX, None), (COUNT_MAX + 1, "malformed_backup_record"),
                       (-1, "malformed_backup_record")):
        r = _receipt(ops)
        r["entry_count"] = count
        r["backup_id"] = _backup_id(r["head"], r["state_id"], count, r["bundle"])
        if cls is None:
            assert engine.verify(r) == r
        else:
            with pytest.raises(backup.BackupError) as info:
                engine.verify(r)
            assert info.value.failure_class == cls


# -- mutation check --------------------------------------------------------------

def _source_mutant(name, edits):
    """store/backup.py with EDITS (each matching exactly once) as a fresh
    module; BackupError and _fail are rebound to the production class."""
    source = BACKUP_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0226_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "backup.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.BackupError = backup.BackupError

    def _fail(failure_class):
        raise backup.BackupError(failure_class, backup.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


_BOUNDARY = ("        except BaseException:\n"
             "            _fail(\"divergent_snapshot\")\n"
             "        if type(out) is not str:\n")


def _passthrough(error, cls):
    guard = "" if cls is None else (
        f"            if error.failure_class != {cls!r}:\n"
        "                _fail(\"divergent_snapshot\")\n")
    return [(_BOUNDARY, f"        except {error} as error:\n" + guard +
             "            raise\n" + _BOUNDARY)]


def _isinstance(expr, kind):
    return (f"type({expr}) is not {kind}", f"not isinstance({expr}, {kind})")


_ZERO = ('        if count == 0 and (receipt["head"] != GENESIS or\n'
         '                           receipt["state_id"] != EMPTY_STATE_ID):\n')

MUTANTS = {
    "except-exception": [(_BOUNDARY, _BOUNDARY.replace("BaseException", "Exception"))],
    "backup-error-passthrough": _passthrough("BackupError", None),
    **{f"backup-error-passthrough-{c}": _passthrough("BackupError", c)
       for c in BACKUP_CLASSES},
    "wal-error-passthrough": _passthrough("_wal.WalError", None),
    **{f"wal-error-passthrough-{c}": _passthrough("_wal.WalError", c)
       for c in WAL_CLASSES},
    "serializer-output-isinstance": [_isinstance("out", "str")],
    "log-type-isinstance": [_isinstance("log", "list")],
    "no-log-type-check": [("        if type(log) is not list:\n"
                           "            _fail(\"malformed_backup_record\")\n", "")],
    "wal-rejection-narrowed": [("        except _wal.WalError:\n"
                                "            _fail(\"corrupt_source\")\n",
                                "        except _wal.WalError as error:\n"
                                "            if error.failure_class != \"corrupt_chain\":\n"
                                "                raise\n"
                                "            _fail(\"corrupt_source\")\n")],
    "no-log-restore": [("            _wal.restore(log, container, saved)\n",
                        "            pass\n")],
    "no-key-guard": [("not _exact_str_keys(receipt) or \\\n                ", "")],
    "receipt-key-len": [("set(dict.keys(receipt)) != set(_FIELDS)",
                         "len(receipt) != len(_FIELDS)")],
    "receipt-key-superset": [("set(dict.keys(receipt)) != set(_FIELDS)",
                              "not set(dict.keys(receipt)) >= set(_FIELDS)")],
    "receipt-isinstance": [("if type(receipt) is not dict",
                            "if not isinstance(receipt, dict)")],
    "id-isinstance": [_isinstance("receipt[field]", "str")],
    "id-grammar-match": [("grammar.fullmatch(receipt[field])",
                          "grammar.match(receipt[field])")],
    "id-grammar-search": [("grammar.fullmatch(receipt[field])",
                           "grammar.search(receipt[field])")],
    "count-isinstance": [_isinstance("count", "int")],
    "bundle-isinstance": [_isinstance('receipt["bundle"]', "str")],
    "no-count-max": [("not 0 <= count <= _COUNT_MAX", "not 0 <= count")],
    "count-max-exclusive": [("not 0 <= count <= _COUNT_MAX", "not 0 <= count < _COUNT_MAX")],
    "no-count-min": [("not 0 <= count <= _COUNT_MAX", "not count <= _COUNT_MAX")],
    "no-zero-count-check": [(_ZERO, "        if False:\n")],
    "zero-count-head-only": [(_ZERO, '        if count == 0 and receipt["head"] != GENESIS:\n')],
    "zero-count-state-only": [(_ZERO, "        if count == 0 and "
                               'receipt["state_id"] != EMPTY_STATE_ID:\n')],
    "no-genesis-positive-check": [('        if count > 0 and receipt["head"] == GENESIS:\n',
                                   "        if False:\n")],
    "no-backup-id-check": [('                receipt["backup_id"]:\n'
                            '            _fail("divergent_backup")\n',
                            '                receipt["backup_id"]:\n'
                            "            pass\n")],
    "no-derive-utf8-check": [('            value.encode("utf-8")\n',
                              '            value.encode("utf-8", "surrogatepass")\n')],
    "verify-returns-input": [("        return {field: receipt[field] for field in _FIELDS}",
                              "        return receipt")],
    "verify-input-order": [("        return {field: receipt[field] for field in _FIELDS}",
                            "        return dict(receipt)")],
}

# the killed list for the oracle boundary: each forge pass-through mutant and
# the exact case label it must fail
FORGE_TARGETS = {
    "except-exception": "serializer:raise:KeyboardInterrupt",
    "backup-error-passthrough": f"serializer:forge-backup:{BACKUP_CLASSES[0]}",
    **{f"backup-error-passthrough-{c}": f"serializer:forge-backup:{c}"
       for c in BACKUP_CLASSES},
    "wal-error-passthrough": f"serializer:forge-wal:{WAL_CLASSES[0]}",
    **{f"wal-error-passthrough-{c}": f"serializer:forge-wal:{c}" for c in WAL_CLASSES},
}

# one-guard edits no black-box case can separate from production: the
# _derive UTF-8 check behind the serializer boundary catches an unchecked
# surrogate as divergent_snapshot too
EQUIVALENT_EDITS = {
    "no-serializer-utf8-check": [('            out.encode("utf-8")\n'
                                  "        except UnicodeEncodeError:\n"
                                  '            _fail("divergent_snapshot")\n',
                                  "            pass\n"
                                  "        except UnicodeEncodeError:\n"
                                  '            _fail("divergent_snapshot")\n')],
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
