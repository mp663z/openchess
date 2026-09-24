"""T0245 integration/restart battery for the production rollback (store.rollback).

The durable form is a JSON-lines WAL log file. A restart is a fresh
interpreter (subprocess) that loads those bytes, builds a new
RollbackEngine (and a fresh linked WalEngine to continue the log) and
rolls back, appends or replays; the child reports the durable bytes of
the log afterwards. Every scenario checks the fresh process against the
uninterrupted in-process run, an independent model of
data/contracts/{wal,rollback}.yaml (entry-id derivation, archive-token
and rollback-id derivations) and, on the happy path, the contract
reference engine (tests.test_t0239_rollback_contract).

Coverage: happy (every (length, target) pair, rollback -> restart ->
continue -> rollback chains, hash-seed determinism), boundary (target 0,
target == len, the empty log, target just outside either end), malformed
(requests, containers, tampered durable logs, precedence between them)
and rollback (every rejection leaves the durable bytes and the request
unchanged; a clean rollback after a rejection, in the same process and
after a restart, equals the model). The tail archiver is the only
except-BaseException oracle boundary: every RollbackError and WalError
class is forged from it and must fail closed as divergent_archive, never
as the forged object. One-edit production mutants are pinned RED on
their own scenario (MUTANTS).
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

from graph.node import make_record, record_identity
from store import rollback, wal
from tests import test_t0239_rollback_contract as _contract

ROOT = Path(__file__).resolve().parents[1]
ROLLBACK_SOURCE = (ROOT / "store" / "rollback.py").read_text()
RESULT_FIELDS = ("rollback_id", "from_head", "to_head", "truncated_count", "archive_token")
GENESIS = "wal0:" + "0" * 64
MRR, CS, UT, DA = (
    "malformed_rollback_record",
    "corrupt_source",
    "unknown_target",
    "divergent_archive",
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
BASE_OPS = (
    ("put", 0),
    ("put", 1),
    ("delete", 0),
    ("put", 2),
    ("delete", 6),
    ("put", 0),
)
ALT_OPS = (("put", 3), ("put", 4), ("delete", 3), ("put", 5), ("put", 6), ("put", 1))


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


def _token(tail):
    parts = ["arc1"]
    for e in tail:
        rec = e["payload"]["record"]
        for field in (
            e["sequence"],
            e["op"],
            e["entry_id"],
            e["prior_entry_id"],
            e["payload"]["identity"],
            rec["variant"],
            rec["digest"],
            rec["snapshot_fen"],
        ):
            text = str(field)
            parts.append(f"{len(text)}:{text}")
    return "arc1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _result(ops, target):
    log = _model_log(ops)
    tail = log[target:]
    frm = log[-1]["entry_id"] if log else GENESIS
    to = log[target - 1]["entry_id"] if target else GENESIS
    token = _token(tail)
    rid = "rbk1:" + hashlib.sha256(f"{frm}\n{to}\n{len(tail)}\n{token}".encode()).hexdigest()
    return {
        "rollback_id": rid,
        "from_head": frm,
        "to_head": to,
        "truncated_count": len(tail),
        "archive_token": token,
    }


def _plain(value):
    """VALUE rebuilt through dict's and list's own methods, never a
    subclass override, so a lying mapping cannot change what is compared."""
    if isinstance(value, dict):
        return {k: _plain(dict.__getitem__(value, k)) for k in dict.keys(value)}
    if isinstance(value, list):
        return [_plain(v) for v in list.__iter__(value)]
    return value


def _dumps(log):
    return "".join(json.dumps(_plain(e), ensure_ascii=False) + "\n" for e in log)


def _loads(text):
    lines = text.split("\n")
    assert lines[-1] == ""
    return [json.loads(line) for line in lines[:-1]]


def _text(ops):
    return _dumps(_model_log(ops))


# -- step runner (shared by the child and the in-process run) ---------------------------


class _Str(str):
    pass


class _Base(BaseException):
    pass


# -- hostile types at every input boundary (coordinator standing rule) -------------------

HOSTILE = []  # user dunder calls observed during the engine call


class _L(list):
    pass


class _D(dict):
    pass


class _LyingDict(dict):
    """A dict subclass whose own accessors lie and log their calls."""

    def __getitem__(self, key):
        HOSTILE.append(f"getitem:{key}")
        return dict.__getitem__(self, key)

    def keys(self):
        HOSTILE.append("keys")
        return []

    def __iter__(self):
        HOSTILE.append("iter")
        return iter([])

    def items(self):
        HOSTILE.append("items")
        return []


class _EqRaises(str):
    def __eq__(self, other):
        HOSTILE.append("eq")
        raise RuntimeError("eq")

    def __ne__(self, other):
        HOSTILE.append("ne")
        raise RuntimeError("ne")

    __hash__ = str.__hash__


def _colliding(target):
    class _HashCollides(str):
        def __hash__(self):
            HOSTILE.append("hash")
            return hash(target)

        def __eq__(self, other):
            HOSTILE.append("eq")
            return str.__eq__(self, other)

        def __ne__(self, other):
            HOSTILE.append("ne")
            return str.__ne__(self, other)

    return _HashCollides


def _hkey(form, text, target):
    return {"plain": _Str, "eq-raises": _EqRaises, "hash-collides": _colliding(target)}[form](text)


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hostile(spec, log, target):
    """(log argument, request) for one hostile row; built in the running
    process so the subclasses survive the restart."""
    kind, _, arg = spec.partition(":")
    request = {"target_sequence": target}
    log = [dict(e) for e in log]  # a copy: the durable log itself stays clean
    if kind == "log-list-subclass":
        return _L(log), request
    if kind == "request-dict-subclass":
        return log, _D(request)
    if kind == "request-lying-dict":
        return log, _LyingDict(request)
    if kind == "request-key":
        return log, {_hkey(arg, "target_sequence", "note"): target}
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
    return log, request


def _archiver(module, spec, calls, forged):
    def archive(tail):
        calls.append(copy.deepcopy(tail))
        kind, _, arg = spec.partition(":")
        honest = module.archive_tail(tail)
        if kind == "honest":
            return honest
        if kind == "scribble":  # honest token, then scribble on the tail it was handed
            for e in tail:
                e["payload"]["record"]["variant"] = "poison"
                e["sequence"] = -1
            tail.clear()
            return honest
        if kind == "raise":
            raise {"ValueError": ValueError, "Base": _Base, "KeyboardInterrupt": KeyboardInterrupt}[
                arg
            ](arg)
        if kind == "forge-rollback":
            forged[0] = rollback.RollbackError(arg, rollback.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-wal":
            forged[0] = wal.WalError(arg, wal.FAILURE_MAPPING[arg])
            raise forged[0]
        return {
            "none": None,
            "bytes": honest.encode(),
            "subclass": _Str(honest),
            "grammar": "arc2:" + honest[5:],
            "uppercase": "arc1:" + honest[5:].upper(),
            "trailing-newline": honest + "\n",
            "surrogate": honest[:-1] + "\ud800",
            "other-tail": "arc1:" + hashlib.sha256(b"other").hexdigest(),
        }[kind]

    return archive


def _request(spec, target):
    return {
        "exact": lambda: {"target_sequence": target},
        "extra-key": lambda: {"target_sequence": target, "note": "x"},
        "missing-key": lambda: {},
        "subclass-key": lambda: {_Str("target_sequence"): target},
        "not-a-dict": lambda: [("target_sequence", target)],
    }[spec]()


def _run(module, text, steps):
    log = _loads(text)
    out = []
    for step in steps:
        calls, forged = [], [None]
        res = {}
        try:
            if step["do"] == "append":
                op, i = step["op"], step["index"]
                request = {
                    "op": op,
                    "payload": {"identity": IDENTITIES[i], "record": dict(RECORDS[i])},
                }
                wal.WalEngine(wal.canonical_payload).append(log, request)
                res["ok"] = True
            elif step["do"] == "hostile":
                engine = module.RollbackEngine(_archiver(module, "honest", calls, forged))
                arg, request = _hostile(step["hostile"], log, step["target"])
                before_arg, before_req = _dumps(list(arg)), json.dumps(dict(request))
                HOSTILE.clear()  # construction may hash/compare; only the call counts
                try:
                    got = engine.rollback(arg, request)
                finally:
                    res = {
                        "hostile": list(HOSTILE),
                        "arg_unchanged": _dumps(list(arg)) == before_arg,
                        "request_unchanged": json.dumps(dict(request)) == before_req,
                    }
                res["ok"] = got
            else:
                engine = module.RollbackEngine(
                    _archiver(module, step.get("archiver", "honest"), calls, forged)
                )
                request = _request(step.get("request", "exact"), step["target"])
                before = repr(request)
                arg = {"list": log, "tuple": tuple(log), "dict": dict(enumerate(log))}[
                    step.get("wrap", "list")
                ]
                got = engine.rollback(arg, request)
                res = {
                    "ok": got,
                    "fields": list(got) == list(RESULT_FIELDS),
                    "request": repr(request) == before,
                }
        except rollback.RollbackError as error:
            res = {
                **(res if step["do"] == "hostile" else {}),
                "err": error.failure_class,
                "code": error.code,
                "typed": error.code == rollback.FAILURE_MAPPING.get(error.failure_class),
                "cause": error.__cause__ is not None,
                "forged": error is forged[0],
            }
        except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
            res = {**(res if step["do"] == "hostile" else {}), "raw": type(error).__name__}
        res["calls"] = calls
        out.append(res)
    return {"steps": out, "log": _dumps(log)}


def _source_mutant(name, edits):
    source = ROLLBACK_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0245_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "rollback.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.RollbackError = rollback.RollbackError

    def _fail(failure_class):
        raise rollback.RollbackError(failure_class, rollback.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _child_main():
    request = json.load(sys.stdin)
    name = request.get("mutant")
    module = rollback if name is None else _source_mutant(name, MUTANTS[name][0])
    results = [_run(module, job["text"], job["steps"]) for job in request["jobs"]]
    sys.stdout.write(json.dumps(results, sort_keys=True))


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0245_rollback_integration_restart import _child_main; _child_main()"
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


def _local(jobs, module=rollback):
    return json.loads(
        json.dumps([_run(module, j["text"], j["steps"]) for j in jobs], sort_keys=True)
    )


def _ok(ops, target):
    return {
        "ok": _result(ops, target),
        "fields": True,
        "request": True,
        "calls": [_model_log(ops)[target:]],
    }


def _rej(cls, calls=()):
    return {
        "err": cls,
        "code": rollback.FAILURE_MAPPING[cls],
        "typed": True,
        "cause": False,
        "forged": False,
        "calls": list(calls),
    }


# -- scenarios: (jobs, expected, labels) ---------------------------------------------------


def _pair_jobs():
    jobs, want, labels = [], [], []
    for n in range(len(BASE_OPS) + 1):
        for t in range(n + 1):
            ops = BASE_OPS[:n]
            jobs.append({"text": _text(ops), "steps": [{"do": "rollback", "target": t}]})
            want.append({"steps": [_ok(ops, t)], "log": _text(ops[:t])})
            labels.append(f"{n}->{t}")
    return jobs, want, labels


def _log_tampers():
    alt = _model_log(ALT_OPS)
    return {
        "entry-id-flip": lambda e, p: e.update(
            entry_id=e["entry_id"][:-1] + ("0" if e["entry_id"][-1] != "0" else "1")
        ),
        "prior-break": lambda e, p: e.update(prior_entry_id="wal1:" + "a" * 64),
        "sequence-gap": lambda e, p: e.update(sequence=e["sequence"] + 1),
        "unknown-op": lambda e, p: e.update(op="patch"),
        "record-digest": lambda e, p: e["payload"]["record"].update(digest=RECORDS[5]["digest"]),
        "identity-mismatch": lambda e, p: e["payload"].update(
            identity=next(i for i in IDENTITIES if i != e["payload"]["identity"])
        ),
        "missing-field": lambda e, p: e.pop("prior_entry_id"),
        "extra-field": lambda e, p: e.update(note="x"),
        "foreign-entry": lambda e, p: (e.clear(), e.update(copy.deepcopy(alt[p]))),
    }


def _malformed_jobs():
    jobs, want, labels = [], [], []
    n = len(BASE_OPS)
    text = _text(BASE_OPS)
    rows = [
        ("target-over-len", {"target": n + 1}, UT),
        ("target-negative", {"target": -1}, UT),
        ("target-bool", {"target": True}, MRR),
        ("target-float", {"target": 2.0}, MRR),
        ("target-str", {"target": "2"}, MRR),
        ("target-none", {"target": None}, MRR),
        ("request-extra-key", {"target": 2, "request": "extra-key"}, MRR),
        ("request-missing-key", {"target": 2, "request": "missing-key"}, MRR),
        ("request-subclass-key", {"target": 2, "request": "subclass-key"}, MRR),
        ("request-not-a-dict", {"target": 2, "request": "not-a-dict"}, MRR),
        ("log-tuple", {"target": 2, "wrap": "tuple"}, MRR),
        ("log-dict", {"target": 2, "wrap": "dict"}, MRR),
    ]
    for label, step, cls in rows:
        jobs.append(
            {"text": text, "steps": [{"do": "rollback", **step}, {"do": "rollback", "target": 2}]}
        )
        want.append({"steps": [_rej(cls), _ok(BASE_OPS, 2)], "log": _text(BASE_OPS[:2])})
        labels.append(label)
    # the empty log: only target 0 exists
    jobs.append(
        {"text": "", "steps": [{"do": "rollback", "target": 1}, {"do": "rollback", "target": 0}]}
    )
    want.append({"steps": [_rej(UT), _ok((), 0)], "log": ""})
    labels.append("empty-log-target-1")
    positions = {"first": 0, "middle": n // 2, "last": n - 1}
    for name, edit in _log_tampers().items():
        for where, p in positions.items():
            log = _model_log(BASE_OPS)
            edit(log[p], p)
            bad = _dumps(log)
            steps = [
                {"do": "rollback", "target": 2},
                # precedence: the source check runs before target resolution,
                # after the request checks
                {"do": "rollback", "target": n + 5},
                {"do": "rollback", "target": 2, "request": "extra-key"},
            ]
            jobs.append({"text": bad, "steps": steps})
            want.append({"steps": [_rej(CS), _rej(CS), _rej(MRR)], "log": bad})
            labels.append(f"{name}@{where}")
    return jobs, want, labels


def _hostile_rows():
    """(spec, ops, class) for every hostile-type row at the input boundaries."""
    rows = []
    for ops in ((), BASE_OPS):
        rows.append(("log-list-subclass", ops, MRR))
        for spec in ("request-dict-subclass", "request-lying-dict"):
            rows.append((spec, ops, MRR))
        for form in KEY_FORMS:
            rows.append((f"request-key:{form}", ops, MRR))
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


def _hostile_jobs():
    jobs, want, labels = [], [], []
    for spec, ops, cls in _hostile_rows():
        t = 0 if not ops else 2
        text = _text(ops)
        jobs.append({"text": text, "steps": [{"do": "hostile", "hostile": spec, "target": t}]})
        want.append(
            {
                "steps": [
                    {
                        **_rej(cls),
                        "hostile": [],
                        "arg_unchanged": True,
                        "request_unchanged": True,
                    }
                ],
                "log": text,
            }
        )
        labels.append(f"{spec}@{len(ops)}")
    return jobs, want, labels


ARCHIVER_FAULTS = (
    [f"raise:{e}" for e in ("ValueError", "Base", "KeyboardInterrupt")]
    + [
        "none",
        "bytes",
        "subclass",
        "grammar",
        "uppercase",
        "trailing-newline",
        "surrogate",
        "other-tail",
    ]
    + [f"forge-rollback:{c}" for c in sorted(rollback.FAILURE_MAPPING)]
    + [f"forge-wal:{c}" for c in sorted(wal.FAILURE_MAPPING)]
)


def _fault_jobs():
    jobs, want, labels = [], [], []
    for ops, t in (((), 0), (BASE_OPS, 2), (BASE_OPS, len(BASE_OPS))):
        text = _text(ops)
        for fault in ARCHIVER_FAULTS:
            jobs.append(
                {
                    "text": text,
                    "steps": [
                        {"do": "rollback", "target": t, "archiver": fault},
                        {"do": "rollback", "target": t},
                    ],
                }
            )
            want.append(
                {"steps": [_rej(DA, [_model_log(ops)[t:]]), _ok(ops, t)], "log": _text(ops[:t])}
            )
            labels.append(f"{fault}@{len(ops)}->{t}")
    jobs.append(
        {
            "text": _text(BASE_OPS),
            "steps": [{"do": "rollback", "target": 2, "archiver": "scribble"}],
        }
    )
    want.append({"steps": [_ok(BASE_OPS, 2)], "log": _text(BASE_OPS[:2])})
    labels.append("scribbling-archiver")
    return jobs, want, labels


def _bad(builder, got):
    jobs, want, labels = builder()
    return [label for label, g, w in zip(labels, got, want, strict=True) if g != w]


# -- tests ----------------------------------------------------------------------------


def test_rollback_after_restart_at_every_length_and_target_matches_the_model():
    jobs, _want, _labels = _pair_jobs()
    got = _restart(jobs)
    assert _bad(_pair_jobs, got) == []
    assert got == _local(jobs)
    for n in range(len(BASE_OPS) + 1):
        for t in range(n + 1):
            log = _model_log(BASE_OPS[:n])
            ref = _contract.RollbackEngine(_contract.archive_tail).rollback(
                log, {"target_sequence": t}
            )
            assert ref == _result(BASE_OPS[:n], t)
            assert log == _model_log(BASE_OPS[:t])


def test_rollback_restart_continue_rollback_chain():
    # process 1 truncates, process 2 continues the truncated durable log
    # with other ops, process 3 rolls the continued log back again
    [r1] = _restart([{"text": _text(BASE_OPS), "steps": [{"do": "rollback", "target": 3}]}])
    assert r1["steps"][0]["ok"] == _result(BASE_OPS, 3)
    new = ALT_OPS[:4]
    steps = [{"do": "append", "op": op, "index": i} for op, i in new]
    [r2] = _restart([{"text": r1["log"], "steps": steps}])
    ops = BASE_OPS[:3] + new
    assert r2["log"] == _text(ops)
    [r3] = _restart(
        [
            {
                "text": r2["log"],
                "steps": [{"do": "rollback", "target": 5}, {"do": "rollback", "target": 0}],
            }
        ]
    )
    assert r3["steps"][0]["ok"] == _result(ops, 5)
    assert r3["steps"][1]["ok"] == _result(ops[:5], 0)
    assert r3["log"] == ""


def test_fresh_process_output_is_deterministic_across_hash_seeds():
    jobs, _want, _labels = _pair_jobs()
    assert _restart(jobs, hash_seed="0", raw=True) == _restart(jobs, hash_seed="1", raw=True)


def test_boundaries_target_zero_target_len_and_the_empty_log():
    n = len(BASE_OPS)
    jobs = [
        {"text": _text(BASE_OPS), "steps": [{"do": "rollback", "target": n}]},
        {"text": _text(BASE_OPS), "steps": [{"do": "rollback", "target": 0}]},
        {"text": "", "steps": [{"do": "rollback", "target": 0}]},
    ]
    got = _restart(jobs)
    assert got[0] == {"steps": [_ok(BASE_OPS, n)], "log": _text(BASE_OPS)}
    assert got[0]["steps"][0]["ok"]["truncated_count"] == 0
    assert got[1]["steps"][0]["ok"]["to_head"] == GENESIS and got[1]["log"] == ""
    assert got[2]["steps"][0]["ok"]["from_head"] == got[2]["steps"][0]["ok"]["to_head"] == GENESIS


def test_malformed_requests_and_tampered_logs_are_rejected_typed_and_untouched():
    jobs, _want, _labels = _malformed_jobs()
    got = _restart(jobs)
    assert _bad(_malformed_jobs, got) == []
    assert got == _local(jobs)


def test_archiver_faults_roll_back_then_truncate_cleanly_before_and_after_restart():
    jobs, _want, _labels = _fault_jobs()
    got = _restart(jobs)
    assert _bad(_fault_jobs, got) == []
    assert got == _local(jobs)
    assert _restart([{"text": _text(BASE_OPS), "steps": [{"do": "rollback", "target": 2}]}]) == [
        {"steps": [_ok(BASE_OPS, 2)], "log": _text(BASE_OPS[:2])}
    ]


def test_hostile_types_at_every_input_boundary_are_rejected_typed_and_inert():
    jobs, _want, _labels = _hostile_jobs()
    got = _restart(jobs)
    assert _bad(_hostile_jobs, got) == []
    assert got == _local(jobs)


def test_every_rollback_and_wal_error_class_is_forged():
    assert {f.split(":")[1] for f in ARCHIVER_FAULTS if f.startswith("forge-rollback")} == set(
        rollback.FAILURE_MAPPING
    )
    assert {f.split(":")[1] for f in ARCHIVER_FAULTS if f.startswith("forge-wal")} == set(
        wal.FAILURE_MAPPING
    )


# -- in-file mutants: each is RED on its own scenario -----------------------------------------


def _red(builder):
    def red(module):
        jobs, want, _labels = builder()
        return _local(jobs, module) != want

    return red


_BOUNDARY = '        except BaseException:\n            _fail("divergent_archive")\n'

MUTANTS = {
    "wal-error-escapes": (
        [
            (
                '        except _wal.WalError:\n            _fail("corrupt_source")',
                "        except _wal.WalError:\n            raise",
            )
        ],
        _red(_malformed_jobs),
    ),
    "log-container-isinstance": (
        [("        if type(log) is not list:", "        if not isinstance(log, (list, tuple)):")],
        _red(_malformed_jobs),
    ),
    "request-key-type-dropped": (
        [("                not all(type(key) is str for key in dict.keys(request)) or \\\n", "")],
        _red(_malformed_jobs),
    ),
    "log-list-isinstance": (
        [("        if type(log) is not list:", "        if not isinstance(log, list):")],
        _red(_hostile_jobs),
    ),
    "request-isinstance": (
        [
            (
                "        if type(request) is not dict or",
                "        if not isinstance(request, dict) or",
            )
        ],
        _red(_hostile_jobs),
    ),
    "target-isinstance": (
        [("        if type(target) is not int:", "        if not isinstance(target, int):")],
        _red(_malformed_jobs),
    ),
    "target-upper-bound-off-by-one": (
        [
            (
                "        if target < 0 or target > len(log):",
                "        if target < 0 or target >= len(log):",
            )
        ],
        _red(_pair_jobs),
    ),
    "target-lower-bound-dropped": (
        [("        if target < 0 or target > len(log):", "        if target > len(log):")],
        _red(_malformed_jobs),
    ),
    "target-checked-before-source": (
        [
            (
                "        try:\n            self._wal.replay(log)\n        except _wal.WalError:\n"
                '            _fail("corrupt_source")\n        if target < 0 or target > len(log):\n'
                '            _fail("unknown_target")\n',
                '        if target < 0 or target > len(log):\n            _fail("unknown_target")\n'
                "        try:\n            self._wal.replay(log)\n        except _wal.WalError:\n"
                '            _fail("corrupt_source")\n',
            ),
        ],
        _red(_malformed_jobs),
    ),
    "except-exception": (
        [(_BOUNDARY, _BOUNDARY.replace("BaseException", "Exception"))],
        _red(_fault_jobs),
    ),
    "rollback-error-passthrough": (
        [(_BOUNDARY, "        except RollbackError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "wal-error-passthrough": (
        [(_BOUNDARY, "        except _wal.WalError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "token-isinstance": (
        [
            (
                "        if type(out) is not str or _TOKEN_RE",
                "        if not isinstance(out, str) or _TOKEN_RE",
            )
        ],
        _red(_fault_jobs),
    ),
    "token-bind-dropped": (
        [("            if token != archive_tail(frozen_tail):\n", "            if False:\n")],
        _red(_fault_jobs),
    ),
    "archiver-gets-live-tail": (
        [
            (
                "            out = self.archiver(copy.deepcopy(frozen_tail))",
                "            out = self.archiver(frozen_tail)",
            )
        ],
        _red(_fault_jobs),
    ),
    "truncates-one-short": (
        [("        del log[target:]", "        del log[target + 1:]")],
        _red(_pair_jobs),
    ),
    "rollback-id-drops-count": (
        [
            (
                '        f"{from_head}\\n{to_head}\\n{truncated_count}\\n{archive_token}"',
                '        f"{from_head}\\n{to_head}\\n{archive_token}"',
            )
        ],
        _red(_pair_jobs),
    ),
}


def test_identity_mutant_is_green():
    module = _source_mutant("identity", [])
    for builder in (_pair_jobs, _malformed_jobs, _fault_jobs, _hostile_jobs):
        jobs, want, _labels = builder()
        assert _local(jobs, module) == want, builder.__name__


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_scenario(name):
    edits, red = MUTANTS[name]
    assert red(_source_mutant(name, edits)), name


def test_a_mutant_is_red_in_a_fresh_process_too():
    jobs, want, _labels = _pair_jobs()
    assert _restart(jobs, mutant="truncates-one-short") != want
