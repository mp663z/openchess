"""T0227 integration/restart battery for the production backup (store.backup).

The durable forms are a JSON-lines WAL log file (one entry per line) and a
JSON receipt. A restart is a fresh interpreter (subprocess) that loads
those bytes, builds a new BackupEngine (and, for continued logs, a new
linked WalEngine) and backs up, verifies or appends. Every scenario checks
the fresh process against the uninterrupted in-process run, an independent
model of data/contracts/wal.yaml + data/contracts/backup.yaml (entry-id
derivation, put/delete fold, bundle serialization, backup-id derivation)
and the contract reference engine (tests.test_t0221_backup_contract).

Coverage: happy (every prefix, process chains, persisted receipts,
hash-seed determinism), boundary (empty log, long log, torn tail, the
entry_count domain from both sides), malformed (tampered durable logs,
tampered and self-consistently forged persisted receipts, non-list
sources) and rollback (every rejection leaves the durable bytes and the
in-memory log unchanged; a clean backup after a rejection, in the same
process and after a restart, equals the model). The bundle serializer is
the only except-BaseException oracle boundary: every BackupError and
WalError class is forged from it and must fail closed as
divergent_snapshot, never as the forged object. One-edit production
mutants are pinned RED on their own scenario (MUTANTS).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from graph import diff
from graph.node import make_record, record_identity
from store import backup, wal
from tests import test_t0221_backup_contract as _contract

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SOURCE = (ROOT / "store" / "backup.py").read_text()
_CC = yaml.safe_load((ROOT / "data" / "contracts" / "backup.yaml").read_text())["contract"]
FIELDS = tuple(_CC["record"]["fields"])
COUNT_MAX = _CC["record"]["field_definitions"]["entry_count"]["max_value"]
GENESIS = "wal0:" + "0" * 64
MBR, CS, DS, DB = (
    "malformed_backup_record",
    "corrupt_source",
    "divergent_snapshot",
    "divergent_backup",
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
IDENTITIES = tuple(record_identity(r) for r in RECORDS)

# upsert, delete of a present identity, delete of an absent identity, re-put
BASE_OPS = (
    ("put", 0),
    ("put", 1),
    ("delete", 0),
    ("put", 2),
    ("delete", 6),
    ("put", 0),
    ("put", 1),
    ("put", 3),
)
LONG_OPS = tuple(("delete" if i % 5 == 4 else "put", (i * 3) % len(RECORDS)) for i in range(60))


# -- independent model ------------------------------------------------------------------


def _entry_id(sequence, op, identity, record, prior):
    canonical = f"{identity}\n{record['variant']}\n{record['digest']}\n{record['snapshot_fen']}"
    return "wal1:" + hashlib.sha256(f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


def _model_log(ops):
    log, prior = [], GENESIS
    for sequence, (op, i) in enumerate(ops, 1):
        eid = _entry_id(sequence, op, IDENTITIES[i], RECORDS[i], prior)
        log.append(
            {
                "entry_id": eid,
                "sequence": sequence,
                "op": op,
                "payload": {"identity": IDENTITIES[i], "record": dict(RECORDS[i])},
                "prior_entry_id": prior,
            }
        )
        prior = eid
    return log


def _fold(ops):
    state = {}
    for op, i in ops:
        if op == "put":
            state[IDENTITIES[i]] = dict(RECORDS[i])
        else:
            state.pop(IDENTITIES[i], None)
    return state


def _bundle(state):
    return "".join(
        f"{k}\n" + "|".join(f"{f}={state[k][f]}" for f in sorted(state[k])) + "\n"
        for k in sorted(state)
    )


def _bid(head, sid, count, bundle):
    return "bck1:" + hashlib.sha256(f"{head}\n{sid}\n{count}\n{bundle}".encode()).hexdigest()


def _receipt(ops):
    state = _fold(ops)
    head = _model_log(ops)[-1]["entry_id"] if ops else GENESIS
    sid, bundle = diff.state_id(state), _bundle(state)
    return {
        "backup_id": _bid(head, sid, len(ops), bundle),
        "head": head,
        "state_id": sid,
        "entry_count": len(ops),
        "bundle": bundle,
    }


def _forge(receipt, **over):
    """A self-consistent forgery: fields overridden, backup id rederived."""
    r = {**receipt, **over}
    r["backup_id"] = _bid(r["head"], r["state_id"], r["entry_count"], r["bundle"])
    return r


# -- durable forms ------------------------------------------------------------------------


def _dumps(log):
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in log)


def _loads(text):
    lines = text.split("\n")
    assert lines[-1] == ""
    return [json.loads(line) for line in lines[:-1]]


# -- step runner (shared by the child and the in-process run) ---------------------------


class _Str(str):
    pass


class _Base(BaseException):
    pass


class _L(list):
    pass


HOSTILE = []  # hostile dunder calls observed during verify


class _D(dict):
    pass


class _LyingDict(dict):
    """A dict subclass whose own accessors lie about its content."""

    def __getitem__(self, key):
        HOSTILE.append(f"getitem:{key}")
        return dict.__getitem__(self, key)

    def keys(self):
        HOSTILE.append("keys")
        return ["head"]

    def __iter__(self):
        HOSTILE.append("iter")
        return iter(["head"])


class _EqRaises(str):
    def __eq__(self, other):
        HOSTILE.append("eq")
        raise RuntimeError("eq")

    __hash__ = str.__hash__


class _HashCollides(str):
    def __hash__(self):
        HOSTILE.append("hash")
        return hash("head")

    def __eq__(self, other):
        HOSTILE.append("eq")
        return str.__eq__(self, other)

    __ne__ = str.__ne__


def _colliding(target):
    class _Collides(str):
        def __hash__(self):
            HOSTILE.append("hash")
            return hash(target)

        def __eq__(self, other):
            HOSTILE.append("eq")
            return str.__eq__(self, other)

        def __ne__(self, other):
            HOSTILE.append("ne")
            return str.__ne__(self, other)

    return _Collides


def _hkey(form, text, target):
    return {"plain": _Str, "eq-raises": _EqRaises, "hash-collides": _colliding(target)}[form](text)


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hostile_log(spec, log):
    """The backup argument for one hostile source row, built on a copy so
    the durable log itself stays clean."""
    kind, _, arg = spec.partition(":")
    log = [dict(e) for e in log]
    if kind == "list-subclass":
        return _L(log)
    form, _, where = arg.partition("@")
    p = {"first": 0, "last": len(log) - 1}[where]
    entry = log[p]
    if kind == "entry-dict-subclass":
        log[p] = _D(entry)
    elif kind == "entry-lying-dict":
        log[p] = _LyingDict(entry)
    elif kind == "entry-key":
        log[p] = {(_hkey(form, k, "sequence") if k == "op" else k): v for k, v in entry.items()}
    elif kind == "entry-value":
        log[p] = {**entry, form: _Str(entry[form])}
    elif kind in ("payload-dict-subclass", "payload-lying-dict"):
        wrap = _D if kind == "payload-dict-subclass" else _LyingDict
        log[p] = {**entry, "payload": wrap(entry["payload"])}
    elif kind == "payload-key":
        payload = {
            (_hkey(form, k, "record") if k == "identity" else k): v
            for k, v in entry["payload"].items()
        }
        log[p] = {**entry, "payload": payload}
    elif kind in ("record-dict-subclass", "record-lying-dict", "record-key", "record-value"):
        record = entry["payload"]["record"]
        if kind == "record-dict-subclass":
            record = _D(record)
        elif kind == "record-lying-dict":
            record = _LyingDict(record)
        elif kind == "record-key":
            record = {
                (_hkey(form, k, "digest") if k == "variant" else k): v for k, v in record.items()
            }
        else:
            record = {**record, form: _Str(record[form])}
        log[p] = {**entry, "payload": {**entry["payload"], "record": record}}
    elif kind == "payload-identity":
        log[p] = {
            **entry,
            "payload": {**entry["payload"], "identity": _Str(entry["payload"]["identity"])},
        }
    else:
        raise AssertionError(spec)
    return log


def _inert_dump(log):
    """A byte form of a (possibly hostile) log read only through list and
    dict's own methods, so no user dunder runs."""
    return json.dumps([list(dict.items(e)) for e in list.__iter__(log)], ensure_ascii=False)


def _hostile_receipt(spec):
    full = _receipt(BASE_OPS)
    kind, _, arg = spec.partition(":")
    if kind == "dict-subclass":
        return _D(full)
    if kind == "lying-dict":
        return _LyingDict(full)
    if kind == "key":
        cls = {"subclass": _Str, "eq-raises": _EqRaises, "hash-collides": _HashCollides}[arg]
        return {**{k: v for k, v in full.items() if k != "bundle"}, cls("bundle"): full["bundle"]}
    if kind == "value":
        return {**full, arg: _Str(full[arg])}
    raise AssertionError(spec)


def _serializer(module, spec, calls, forged):
    def ser(state):
        calls.append(copy.deepcopy(state))
        if spec == "honest":
            return module.serialize_bundle(state)
        if spec == "scribble":  # honest output, then scribble on what it was handed
            out = module.serialize_bundle(state)
            for rec in state.values():
                rec["variant"] = "poison"
            state.clear()
            return out
        kind, _, arg = spec.partition(":")
        if kind == "raise":
            raise {"ValueError": ValueError, "Base": _Base, "KeyboardInterrupt": KeyboardInterrupt}[
                arg
            ](arg)
        if kind == "forge-backup":
            forged[0] = backup.BackupError(arg, backup.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-wal":
            forged[0] = wal.WalError(arg, wal.FAILURE_MAPPING[arg])
            raise forged[0]
        return {"none": None, "bytes": b"x", "subclass": _Str("x"), "surrogate": "\ud800"}[kind]

    return ser


def _err(module, error, forged):
    return {
        "err": error.failure_class,
        "code": error.code,
        "typed": type(error) is backup.BackupError
        and error.code == backup.FAILURE_MAPPING.get(error.failure_class),
        "cause": error.__cause__ is not None,
        "forged": error is forged[0],
    }


def _run(module, text, steps):
    """Run STEPS over the durable log TEXT; returns per-step outcomes and
    the durable bytes of the log afterwards."""
    log = _loads(text)
    out = []
    for step in steps:
        calls, forged = [], [None]
        engine = module.BackupEngine(_serializer(module, step.get("ser", "honest"), calls, forged))
        try:
            if step["do"] == "backup":
                arg = {
                    "list": log,
                    "dict": dict(enumerate(log)),
                    "tuple": tuple(log),
                    "list-subclass": _L(log),
                }[step.get("wrap", "list")]
                res = {"ok": engine.backup(arg)}
            elif step["do"] == "verify":
                receipt = step["receipt"]
                before = json.dumps(receipt)
                got = engine.verify(receipt)
                res = {
                    "ok": got,
                    "detached": got is not receipt and list(got) == list(FIELDS),
                    "unchanged": json.dumps(receipt) == before,
                }
            elif step["do"] == "backup-hostile":
                arg = _hostile_log(step["hostile"], log)
                before = _inert_dump(arg)
                HOSTILE.clear()  # construction may hash/compare; only backup counts
                try:
                    got = engine.backup(arg)
                finally:
                    hostile = list(HOSTILE)
                res = {"ok": got, "unchanged": _inert_dump(arg) == before, "hostile": hostile}
            elif step["do"] == "verify-hostile":
                receipt = _hostile_receipt(step["hostile"])
                before = json.dumps(receipt)
                HOSTILE.clear()  # construction may hash/compare; only verify counts
                try:
                    got = engine.verify(receipt)
                finally:
                    hostile = list(HOSTILE)
                res = {"ok": got, "unchanged": json.dumps(receipt) == before, "hostile": hostile}
            else:  # append through a fresh linked WAL engine
                op, i = step["op"], step["index"]
                request = {
                    "op": op,
                    "payload": {"identity": IDENTITIES[i], "record": dict(RECORDS[i])},
                }
                wal.WalEngine(wal.canonical_payload).append(log, request)
                res = {"ok": True}
        except backup.BackupError as error:
            res = _err(module, error, forged)
            if step["do"] == "verify-hostile":
                res["hostile"] = hostile
            if step["do"] == "backup-hostile":
                res["hostile"] = hostile
                res["unchanged"] = _inert_dump(arg) == before
        except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
            res = {"raw": type(error).__name__}
        res["calls"] = len(calls)
        if calls:
            res["saw"] = calls[0]
        out.append(res)
    return {"steps": out, "log": _dumps(log)}


def _source_mutant(name, edits):
    source = BACKUP_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0227_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "backup.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.BackupError = backup.BackupError

    def _fail(failure_class):
        raise backup.BackupError(failure_class, backup.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _child_main():
    request = json.load(sys.stdin)
    name = request.get("mutant")
    module = backup if name is None else _source_mutant(name, MUTANTS[name][0])
    results = [_run(module, job["text"], job["steps"]) for job in request["jobs"]]
    sys.stdout.write(json.dumps(results, sort_keys=True))


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0227_backup_integration_restart import _child_main; _child_main()"
)


def _restart(jobs, mutant=None, hash_seed="0", raw=False):
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    run = subprocess.run(
        [sys.executable, "-c", _CHILD, str(ROOT)],
        input=json.dumps({"mutant": mutant, "jobs": jobs}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=100,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout if raw else json.loads(run.stdout)


def _local(jobs, module=backup):
    """The uninterrupted in-process run, through the same JSON round trip."""
    return json.loads(
        json.dumps([_run(module, j["text"], j["steps"]) for j in jobs], sort_keys=True)
    )


def _reference(ops):
    return _contract.BackupEngine(_contract.serialize_bundle).backup(_model_log(ops))


def _rej(cls, calls=0):
    return {
        "err": cls,
        "code": backup.FAILURE_MAPPING[cls],
        "typed": True,
        "cause": False,
        "forged": False,
        "calls": calls,
    }


# -- scenarios: (jobs, expected results) ---------------------------------------------------


def _prefix_jobs():
    jobs, want = [], []
    for k in range(len(BASE_OPS) + 1):
        ops, text = BASE_OPS[:k], _dumps(_model_log(BASE_OPS[:k]))
        r = _receipt(ops)
        jobs.append({"text": text, "steps": [{"do": "backup"}, {"do": "verify", "receipt": r}]})
        want.append(
            {
                "steps": [
                    {"ok": r, "calls": 1, "saw": _fold(ops)},
                    {"ok": r, "detached": True, "unchanged": True, "calls": 0},
                ],
                "log": text,
            }
        )
    return jobs, want


def _flip(hexid):
    return hexid[:-1] + ("0" if hexid[-1] != "0" else "1")


def _log_tampers():
    alt = _model_log(
        (
            ("put", 3),
            ("put", 4),
            ("put", 5),
            ("delete", 3),
            ("put", 6),
            ("put", 1),
            ("put", 2),
            ("put", 0),
        )
    )
    other_digest = RECORDS[5]["digest"]
    return {
        "entry-id-flip": lambda e, p: e.update(entry_id=_flip(e["entry_id"])),
        "prior-break": lambda e, p: e.update(prior_entry_id="wal1:" + "a" * 64),
        "sequence-gap": lambda e, p: e.update(sequence=e["sequence"] + 1),
        "sequence-bool": lambda e, p: e.update(
            sequence=True if e["sequence"] == 1 else 1.0 * e["sequence"]
        ),
        "unknown-op": lambda e, p: e.update(op="patch"),
        "record-digest": lambda e, p: e["payload"]["record"].update(digest=other_digest),
        "identity-mismatch": lambda e, p: e["payload"].update(
            identity=next(i for i in IDENTITIES if i != e["payload"]["identity"])
        ),
        "missing-field": lambda e, p: e.pop("prior_entry_id"),
        "extra-field": lambda e, p: e.update(note="x"),
        "foreign-entry": lambda e, p: (e.clear(), e.update(copy.deepcopy(alt[p]))),
    }


POSITIONS = {"first": 0, "middle": len(BASE_OPS) // 2, "last": len(BASE_OPS) - 1}


def _tampered_log_jobs():
    jobs, want, labels = [], [], []
    for name, edit in _log_tampers().items():
        for where, p in POSITIONS.items():
            log = _model_log(BASE_OPS)
            edit(log[p], p)
            text = _dumps(log)
            jobs.append(
                {
                    "text": text,
                    "steps": [{"do": "backup"}, {"do": "backup", "ser": "raise:ValueError"}],
                }
            )
            want.append({"steps": [_rej(CS), _rej(CS)], "log": text})
            labels.append(f"{name}@{where}")
    for wrap in ("dict", "tuple"):
        text = _dumps(_model_log(BASE_OPS))
        jobs.append({"text": text, "steps": [{"do": "backup", "wrap": wrap}]})
        want.append({"steps": [_rej(MBR)], "log": text})
        labels.append(f"container:{wrap}")
    # a list subclass is not the exact list the contract names, empty or not
    for ops in ((), BASE_OPS):
        text = _dumps(_model_log(ops))
        jobs.append({"text": text, "steps": [{"do": "backup", "wrap": "list-subclass"}]})
        want.append({"steps": [_rej(MBR)], "log": text})
        labels.append(f"container:list-subclass:{len(ops)}")
    return jobs, want, labels


def _hostile_rows():
    """(spec, ops, class) for every hostile-type row at the source boundary."""
    rows = [("list-subclass", ops, MBR) for ops in ((), BASE_OPS)]
    for where in ("first", "last"):
        rows.append((f"entry-dict-subclass:x@{where}", BASE_OPS, CS))
        rows.append((f"entry-lying-dict:x@{where}", BASE_OPS, CS))
        for form in KEY_FORMS:
            rows.append((f"entry-key:{form}@{where}", BASE_OPS, CS))
        for field in ("entry_id", "op", "prior_entry_id"):
            rows.append((f"entry-value:{field}@{where}", BASE_OPS, CS))
        rows.append((f"payload-identity:x@{where}", BASE_OPS, CS))
        # the nested mappings wal.replay reads: payload and payload["record"]
        for kind in ("payload", "record"):
            rows.append((f"{kind}-dict-subclass:x@{where}", BASE_OPS, CS))
            rows.append((f"{kind}-lying-dict:x@{where}", BASE_OPS, CS))
            for form in KEY_FORMS:
                rows.append((f"{kind}-key:{form}@{where}", BASE_OPS, CS))
        for field in ("variant", "digest", "snapshot_fen"):
            rows.append((f"record-value:{field}@{where}", BASE_OPS, CS))
    return rows


def _hostile_source_jobs():
    """Hostile types at the backup source boundary: rejected typed before
    any user dunder runs, the argument untouched, the serializer never
    called, and a clean backup of the same durable log succeeds after."""
    jobs, want, labels = [], [], []
    for spec, ops, cls in _hostile_rows():
        text = _dumps(_model_log(ops))
        jobs.append(
            {"text": text, "steps": [{"do": "backup-hostile", "hostile": spec}, {"do": "backup"}]}
        )
        ok = {"ok": _receipt(ops), "calls": 1, "saw": _fold(ops)}
        want.append({"steps": [{**_rej(cls), "hostile": [], "unchanged": True}, ok], "log": text})
        labels.append(f"{spec}@{len(ops)}")
    return jobs, want, labels


def _receipt_tampers():
    full, empty = _receipt(BASE_OPS), _receipt(())
    other_head = _model_log(BASE_OPS[:3])[-1]["entry_id"]
    rows = {
        "missing-field": ({k: v for k, v in full.items() if k != "bundle"}, MBR),
        "extra-field": ({**full, "note": "x"}, MBR),
        "backup-id-uppercase": (
            {**full, "backup_id": full["backup_id"].upper().replace("BCK1", "bck1")},
            MBR,
        ),
        "head-grammar": ({**full, "head": "wal2:" + full["head"][5:]}, MBR),
        "head-trailing-newline": (_forge(full, head=full["head"] + "\n"), MBR),
        "state-id-grammar": ({**full, "state_id": "gs2:" + full["state_id"][4:]}, MBR),
        "count-negative": (_forge(full, entry_count=-1), MBR),
        "count-over-max": (_forge(full, entry_count=COUNT_MAX + 1), MBR),
        "count-bool": (_forge(empty, entry_count=False), MBR),
        "count-float": ({**full, "entry_count": float(full["entry_count"])}, MBR),
        "count-str": ({**full, "entry_count": str(full["entry_count"])}, MBR),
        "bundle-none": ({**full, "bundle": None}, MBR),
        "bundle-surrogate": ({**full, "bundle": "\ud800"}, MBR),
        "bundle-edited": ({**full, "bundle": full["bundle"] + " "}, DB),
        "head-swapped": ({**full, "head": other_head}, DB),
        "count-edited": ({**full, "entry_count": full["entry_count"] + 1}, DB),
        "forged-empty-count-nongenesis-head": (_forge(full, entry_count=0), DB),
        "forged-genesis-head-nonzero-count": (_forge(empty, entry_count=3), DB),
        "forged-empty-nonempty-state": (_forge(empty, state_id=full["state_id"]), DB),
    }
    return rows


def _tampered_receipt_jobs():
    jobs, want, labels = [], [], []
    for name, (r, cls) in _receipt_tampers().items():
        jobs.append({"text": "", "steps": [{"do": "verify", "receipt": r}]})
        want.append({"steps": [_rej(cls)], "log": ""})
        labels.append(name)
    # hostile types at the receipt trust boundary: rejected before any
    # hostile method runs
    hostile = ["dict-subclass", "lying-dict", "key:subclass", "key:eq-raises", "key:hash-collides"]
    hostile += [f"value:{f}" for f in ("backup_id", "head", "state_id", "bundle")]
    for spec in hostile:
        jobs.append({"text": "", "steps": [{"do": "verify-hostile", "hostile": spec}]})
        want.append({"steps": [{**_rej(MBR), "hostile": []}], "log": ""})
        labels.append(f"hostile:{spec}")
    # the domain ceiling itself is accepted when self-consistent
    r = _forge(_receipt(BASE_OPS), entry_count=COUNT_MAX)
    jobs.append({"text": "", "steps": [{"do": "verify", "receipt": r}]})
    want.append({"steps": [{"ok": r, "detached": True, "unchanged": True, "calls": 0}], "log": ""})
    labels.append("count-at-max-accepted")
    return jobs, want, labels


SERIALIZER_FAULTS = (
    [
        "raise:ValueError",
        "raise:Base",
        "raise:KeyboardInterrupt",
        "none",
        "bytes",
        "subclass",
        "surrogate",
    ]
    + [f"forge-backup:{c}" for c in sorted(backup.FAILURE_MAPPING)]
    + [f"forge-wal:{c}" for c in sorted(wal.FAILURE_MAPPING)]
)


def _fault_jobs():
    jobs, want = [], []
    for ops in ((), BASE_OPS):
        text, r = _dumps(_model_log(ops)), _receipt(ops)
        for fault in SERIALIZER_FAULTS:
            jobs.append({"text": text, "steps": [{"do": "backup", "ser": fault}, {"do": "backup"}]})
            want.append(
                {
                    "steps": [
                        {**_rej(DS, calls=1), "saw": _fold(ops)},
                        {"ok": r, "calls": 1, "saw": _fold(ops)},
                    ],
                    "log": text,
                }
            )
    return jobs, want


def _scribble_jobs():
    text, r = _dumps(_model_log(BASE_OPS)), _receipt(BASE_OPS)
    jobs = [{"text": text, "steps": [{"do": "backup", "ser": "scribble"}, {"do": "backup"}]}]
    ok = {"ok": r, "calls": 1, "saw": _fold(BASE_OPS)}
    return jobs, [{"steps": [ok, ok], "log": text}]


# -- tests: happy -----------------------------------------------------------------------


def test_restart_at_every_prefix_backs_up_and_verifies_like_the_model():
    jobs, want = _prefix_jobs()
    assert _restart(jobs) == want
    assert _local(jobs) == want
    for k in range(len(BASE_OPS) + 1):
        assert _reference(BASE_OPS[:k]) == _receipt(BASE_OPS[:k])


def test_process_chain_append_then_backup_after_every_restart():
    text = ""
    for k, (op, i) in enumerate(BASE_OPS, 1):
        [res] = _restart(
            [{"text": text, "steps": [{"do": "append", "op": op, "index": i}, {"do": "backup"}]}]
        )
        assert res["steps"][0] == {"ok": True, "calls": 0}
        assert res["steps"][1]["ok"] == _receipt(BASE_OPS[:k])
        assert res["log"] == _dumps(_model_log(BASE_OPS[:k]))
        text = res["log"]
    # the durable receipt of the chained run verifies in yet another process
    [res] = _restart([{"text": text, "steps": [{"do": "verify", "receipt": _receipt(BASE_OPS)}]}])
    assert res["steps"][0]["ok"] == _receipt(BASE_OPS)


def test_fresh_process_output_is_deterministic_across_hash_seeds():
    jobs, _ = _prefix_jobs()
    assert _restart(jobs, hash_seed="0", raw=True) == _restart(jobs, hash_seed="1", raw=True)


# -- tests: boundary --------------------------------------------------------------------


def test_empty_log_boundary_pins_genesis_and_empty_state():
    [res] = _restart([{"text": "", "steps": [{"do": "backup"}]}])
    r = res["steps"][0]["ok"]
    assert r == _receipt(())
    assert (r["head"], r["state_id"], r["entry_count"]) == (GENESIS, backup.EMPTY_STATE_ID, 0)


def test_long_log_boundary():
    text = _dumps(_model_log(LONG_OPS))
    jobs = [
        {"text": text, "steps": [{"do": "backup"}, {"do": "verify", "receipt": _receipt(LONG_OPS)}]}
    ]
    got = _restart(jobs)
    assert got == _local(jobs)
    assert got[0]["steps"][0]["ok"] == _receipt(LONG_OPS) == _reference(LONG_OPS)
    assert got[0]["log"] == text


@pytest.mark.parametrize("lost", [1, 3])
def test_torn_tail_backs_up_the_surviving_prefix(lost):
    text = _dumps(_model_log(BASE_OPS))
    torn = "".join(text.splitlines(keepends=True)[:-lost])
    [res] = _restart([{"text": torn, "steps": [{"do": "backup"}]}])
    assert res["steps"][0]["ok"] == _receipt(BASE_OPS[:-lost])


# -- tests: malformed + rollback --------------------------------------------------------


def test_tampered_durable_logs_are_rejected_typed_and_untouched():
    jobs, want, labels = _tampered_log_jobs()
    got = _restart(jobs)
    bad = [label for label, g, w in zip(labels, got, want, strict=True) if g != w]
    assert bad == []
    assert got == _local(jobs)


def test_tampered_persisted_receipts_are_rejected_typed_and_untouched():
    jobs, want, labels = _tampered_receipt_jobs()
    got = _restart(jobs)
    bad = [label for label, g, w in zip(labels, got, want, strict=True) if g != w]
    assert bad == []
    assert got == _local(jobs)


def test_hostile_types_at_the_source_boundary_are_rejected_typed_and_inert():
    jobs, want, labels = _hostile_source_jobs()
    got = _restart(jobs)
    bad = [label for label, g, w in zip(labels, got, want, strict=True) if g != w]
    assert bad == []
    assert got == _local(jobs)


def test_serializer_faults_roll_back_then_back_up_cleanly_before_and_after_restart():
    jobs, want = _fault_jobs()
    got = _restart(jobs)
    bad = [j["steps"][0]["ser"] for j, g, w in zip(jobs, got, want, strict=True) if g != w]
    assert bad == []
    assert got == _local(jobs)
    # a later process over the same durable bytes is unaffected
    assert _restart([{"text": jobs[-1]["text"], "steps": [{"do": "backup"}]}])[0]["steps"][0][
        "ok"
    ] == (_receipt(BASE_OPS))


def test_every_backup_and_wal_error_class_is_forged():
    assert {f.split(":")[1] for f in SERIALIZER_FAULTS if f.startswith("forge-backup")} == set(
        backup.FAILURE_MAPPING
    )
    assert {f.split(":")[1] for f in SERIALIZER_FAULTS if f.startswith("forge-wal")} == set(
        wal.FAILURE_MAPPING
    )


def test_scribbling_serializer_is_inert_after_restart():
    jobs, want = _scribble_jobs()
    assert _restart(jobs) == want
    assert _local(jobs) == want


# -- in-file mutants: each is RED on its own scenario -----------------------------------------


def _red(builder):
    def red(module):
        jobs, want = builder()[:2]
        return _local(jobs, module) != want

    return red


_BOUNDARY = (
    '        except BaseException:\n            _fail("divergent_snapshot")\n        if type(out)'
)

MUTANTS = {
    "wal-error-escapes": (
        [
            (
                '        except _wal.WalError:\n            _fail("corrupt_source")',
                "        except _wal.WalError:\n            raise",
            )
        ],
        _red(_tampered_log_jobs),
    ),
    "source-container-isinstance": (
        [("        if type(log) is not list:", "        if not isinstance(log, (list, tuple)):")],
        _red(_tampered_log_jobs),
    ),
    "source-list-isinstance-hostile": (
        [("        if type(log) is not list:", "        if not isinstance(log, list):")],
        _red(_hostile_source_jobs),
    ),
    "except-exception": (
        [(_BOUNDARY, _BOUNDARY.replace("BaseException", "Exception"))],
        _red(_fault_jobs),
    ),
    "backup-error-passthrough": (
        [(_BOUNDARY, "        except BackupError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "wal-error-passthrough": (
        [(_BOUNDARY, "        except _wal.WalError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "serializer-output-isinstance": (
        [("        if type(out) is not str:", "        if not isinstance(out, str):")],
        _red(_fault_jobs),
    ),
    "count-from-state-size": (
        [
            (
                '                                 replayed["applied"], bundle,',
                '                                 len(replayed["state"]), bundle,',
            )
        ],
        _red(_prefix_jobs),
    ),
    "backup-id-drops-count": (
        [
            (
                '        f"{head}\\n{sid}\\n{count}\\n{bundle}".encode()).hexdigest()',
                '        f"{head}\\n{sid}\\n{bundle}".encode()).hexdigest()',
            )
        ],
        _red(_prefix_jobs),
    ),
    "verify-count-isinstance": (
        [
            (
                "        if type(count) is not int or not 0 <= count <= _COUNT_MAX:",
                "        if not isinstance(count, int) or not 0 <= count <= _COUNT_MAX:",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "verify-count-unbounded": (
        [
            (
                "        if type(count) is not int or not 0 <= count <= _COUNT_MAX:",
                "        if type(count) is not int or not 0 <= count:",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "verify-head-match-not-fullmatch": (
        [
            (
                "                    grammar.fullmatch(receipt[field]) is None:",
                "                    grammar.match(receipt[field]) is None:",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "verify-empty-genesis-dropped": (
        [
            (
                '        if count == 0 and (receipt["head"] != GENESIS or\n'
                '                           receipt["state_id"] != EMPTY_STATE_ID):\n'
                '            _fail("divergent_backup")\n',
                "",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "verify-nonempty-genesis-dropped": (
        [
            (
                '        if count > 0 and receipt["head"] == GENESIS:\n'
                '            _fail("divergent_backup")\n',
                "",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "verify-bundle-type-dropped": (
        [
            (
                '        if type(receipt["bundle"]) is not str:\n'
                '            _fail("malformed_backup_record")\n',
                "",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "verify-returns-input": (
        [("        return {field: receipt[field] for field in _FIELDS}", "        return receipt")],
        _red(_prefix_jobs),
    ),
    "source-list-isinstance": (
        [("        if type(log) is not list:", "        if not isinstance(log, list):")],
        _red(_tampered_log_jobs),
    ),
    "receipt-isinstance": (
        [
            (
                "        if type(receipt) is not dict or not _exact_str_keys(receipt) or \\\n",
                "        if not isinstance(receipt, dict) or not _exact_str_keys(receipt) or \\\n",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "receipt-key-type-dropped": (
        [
            (
                "        if type(receipt) is not dict or not _exact_str_keys(receipt) or \\\n",
                "        if type(receipt) is not dict or \\\n",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "receipt-field-isinstance": (
        [
            (
                "            if type(receipt[field]) is not str or \\\n",
                "            if not isinstance(receipt[field], str) or \\\n",
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
    "receipt-bundle-isinstance": (
        [
            (
                '        if type(receipt["bundle"]) is not str:',
                '        if not isinstance(receipt["bundle"], str):',
            )
        ],
        _red(_tampered_receipt_jobs),
    ),
}


def test_identity_mutant_is_green():
    module = _source_mutant("identity", [])
    for builder in (
        _prefix_jobs,
        _tampered_log_jobs,
        _tampered_receipt_jobs,
        _hostile_source_jobs,
        _fault_jobs,
        _scribble_jobs,
    ):
        jobs, want = builder()[:2]
        assert _local(jobs, module) == want, builder.__name__


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_scenario(name):
    edits, red = MUTANTS[name]
    assert red(_source_mutant(name, edits)), name


def test_a_mutant_is_red_in_a_fresh_process_too():
    jobs, want, _labels = _tampered_log_jobs()
    assert _restart(jobs, mutant="wal-error-escapes") != want
