"""T0254 integration/restart battery for the production export (store.export).

The durable form is a JSON-lines WAL log file. A restart is a fresh
interpreter (subprocess) that loads those bytes, builds a new
ExportEngine (and, to continue or truncate the log, a fresh linked
WalEngine / RollbackEngine) and exports; the child reports the durable
bytes of the log afterwards. Every scenario checks the fresh process
against the uninterrupted in-process run, an independent model of
data/contracts/{wal,export}.yaml (replayed state, state-id, canonical
document and export-id derivations) and, on the happy path, the
contract reference engine (tests.test_t0248_export_contract).

Coverage: happy (an export of every log prefix, export -> restart ->
continue -> restart -> truncate -> export chains, hash-seed
determinism), boundary (the empty log, a log whose state is empty after
deletes, repeated exports), malformed (requests, formats, containers,
tampered durable logs, precedence between them) and rollback (export is
read-only: every export, accepted or rejected, leaves the durable bytes
and the request unchanged; a clean export after a rejection, in the same
process and after a restart, equals the model). The document exporter
is the only except-BaseException oracle boundary: every ExportError and
WalError class, and UnicodeEncodeError, is forged from it and must fail
closed as divergent_export, never as the forged object. One-edit
production mutants are pinned RED on their own scenario (MUTANTS).
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
from store import export, rollback, wal
from tests import test_t0248_export_contract as _contract

ROOT = Path(__file__).resolve().parents[1]
EXPORT_SOURCE = (ROOT / "store" / "export.py").read_text()
RESULT_FIELDS = ("export_id", "head", "state_id", "record_count", "format", "document")
GENESIS = "wal0:" + "0" * 64
FMT = "jsonl-v1"
MER, UF, CS, DE = (
    "malformed_export_request",
    "unsupported_format",
    "corrupt_source",
    "divergent_export",
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
    ("put", 5),
    ("put", 3),
)
ALT_OPS = (
    ("put", 3),
    ("put", 4),
    ("delete", 3),
    ("put", 5),
    ("put", 6),
    ("put", 1),
    ("put", 2),
    ("delete", 5),
)
EMPTIED_OPS = (("put", 4), ("put", 2), ("delete", 4), ("delete", 2))


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


def _model_state(ops):
    live = {}
    for op, i in ops:
        if op == "put":
            live[i] = True
        else:
            live.pop(i, None)
    return {IDENTITIES[i]: dict(RECORDS[i]) for i in live}


def _model_state_id(state):
    parts = []
    for key in sorted(state):
        body = "|".join(f"{f}={state[key][f]}" for f in sorted(state[key]))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256("".join(parts).encode()).hexdigest()


def _model_document(state):
    lines = []
    for key in sorted(state):
        rec = state[key]
        fields = ",".join(f'"{f}":{json.dumps(rec[f], ensure_ascii=False)}' for f in sorted(rec))
        lines.append(f'{{"identity":{json.dumps(key)},"record":{{{fields}}}}}\n')
    return "".join(lines)


def _result(ops):
    log = _model_log(ops)
    state = _model_state(ops)
    head = log[-1]["entry_id"] if log else GENESIS
    sid = _model_state_id(state)
    doc = _model_document(state)
    eid = (
        "exp1:" + hashlib.sha256(f"{head}\n{sid}\n{len(state)}\n{FMT}\n{doc}".encode()).hexdigest()
    )
    return {
        "export_id": eid,
        "head": head,
        "state_id": sid,
        "record_count": len(state),
        "format": FMT,
        "document": doc,
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


def _hostile(spec, log, target=FMT):
    """(log argument, request) for one hostile row; built in the running
    process so the subclasses survive the restart."""
    kind, _, arg = spec.partition(":")
    request = {"format": target}
    log = [dict(e) for e in log]  # a copy: the durable log itself stays clean
    if kind == "log-list-subclass":
        return _L(log), request
    if kind == "request-dict-subclass":
        return log, _D(request)
    if kind == "request-lying-dict":
        return log, _LyingDict(request)
    if kind == "request-key":
        return log, {_hkey(arg, "format", "note"): target}
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


def _exporter(module, spec, calls, forged):
    def exporter(state, fmt):
        calls.append([copy.deepcopy(state), fmt])
        kind, _, arg = spec.partition(":")
        honest = module.render_document(copy.deepcopy(state), fmt)
        if kind == "honest":
            return honest
        if kind == "scribble":  # honest document, then scribble on the state it was handed
            for rec in state.values():
                rec["variant"] = "poison"
            state.clear()
            return honest
        if kind == "raise":
            if arg == "UnicodeEncodeError":
                raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "forged")
            raise {"ValueError": ValueError, "Base": _Base, "KeyboardInterrupt": KeyboardInterrupt}[
                arg
            ](arg)
        if kind == "forge-export":
            forged[0] = export.ExportError(arg, export.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-wal":
            forged[0] = wal.WalError(arg, wal.FAILURE_MAPPING[arg])
            raise forged[0]
        lines = honest.split("\n")[:-1]
        return {
            "none": None,
            "bytes": honest.encode(),
            "subclass": _Str(honest),
            "surrogate": honest + "\ud800",
            "trailing-newline": honest + "\n",
            "no-final-newline": honest[:-1],
            "reversed": "".join(line + "\n" for line in reversed(lines)),
            "spaced": honest.replace('":', '": ').replace(',"', ', "'),
            "crlf": honest.replace("\n", "\r\n"),
            "dropped-line": "".join(line + "\n" for line in lines[1:]),
            "empty": "",
        }[kind]

    return exporter


def _request(spec):
    return {
        "exact": lambda: {"format": FMT},
        "extra-key": lambda: {"format": FMT, "note": "x"},
        "missing-key": lambda: {},
        "renamed-key": lambda: {"formats": FMT},
        "subclass-key": lambda: {_Str("format"): FMT},
        "not-a-dict": lambda: [("format", FMT)],
        "format-subclass": lambda: {"format": _Str(FMT)},
        "format-bool": lambda: {"format": True},
        "format-none": lambda: {"format": None},
        "format-bytes": lambda: {"format": FMT.encode()},
        "format-upper": lambda: {"format": FMT.upper()},
        "format-newline": lambda: {"format": FMT + "\n"},
        "format-v2": lambda: {"format": "jsonl-v2"},
        "format-empty": lambda: {"format": ""},
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
            elif step["do"] == "truncate":
                rollback.RollbackEngine(rollback.archive_tail).rollback(
                    log, {"target_sequence": step["target"]}
                )
                res["ok"] = True
            elif step["do"] == "hostile":
                engine = module.ExportEngine(_exporter(module, "honest", calls, forged))
                arg, request = _hostile(step["hostile"], log)
                before_arg, before_req = _dumps(list(arg)), json.dumps(dict(request))
                HOSTILE.clear()  # construction may hash/compare; only the call counts
                try:
                    got = engine.export(arg, request)
                finally:
                    res = {
                        "hostile": list(HOSTILE),
                        "arg_unchanged": _dumps(list(arg)) == before_arg,
                        "request_unchanged": json.dumps(dict(request)) == before_req,
                    }
                res["ok"] = got
            else:
                engine = module.ExportEngine(
                    _exporter(module, step.get("exporter", "honest"), calls, forged)
                )
                request = _request(step.get("request", "exact"))
                before = repr(request)
                logbefore = _dumps(log)
                arg = {"list": log, "tuple": tuple(log), "dict": dict(enumerate(log))}[
                    step.get("wrap", "list")
                ]
                got = engine.export(arg, request)
                res = {
                    "ok": got,
                    "fields": list(got) == list(RESULT_FIELDS),
                    "exact_types": [type(v).__name__ for v in got.values()],
                    "request": repr(request) == before,
                    "log": _dumps(log) == logbefore,
                }
        except export.ExportError as error:
            res = {
                **(res if step["do"] == "hostile" else {}),
                "err": error.failure_class,
                "code": error.code,
                "typed": error.code == export.FAILURE_MAPPING.get(error.failure_class),
                "cause": error.__cause__ is not None,
                "forged": error is forged[0],
            }
        except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
            res = {**(res if step["do"] == "hostile" else {}), "raw": type(error).__name__}
        res["calls"] = calls
        out.append(res)
    return {"steps": out, "log": _dumps(log)}


def _source_mutant(name, edits):
    source = EXPORT_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0254_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "export.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.ExportError = export.ExportError

    def _fail(failure_class):
        raise export.ExportError(failure_class, export.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _child_main():
    request = json.load(sys.stdin)
    name = request.get("mutant")
    module = export if name is None else _source_mutant(name, MUTANTS[name][0])
    results = [_run(module, job["text"], job["steps"]) for job in request["jobs"]]
    sys.stdout.write(json.dumps(results, sort_keys=True))


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0254_export_integration_restart import _child_main; _child_main()"
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


def _local(jobs, module=export):
    return json.loads(
        json.dumps([_run(module, j["text"], j["steps"]) for j in jobs], sort_keys=True)
    )


def _ok(ops):
    return {
        "ok": _result(ops),
        "fields": True,
        "exact_types": ["str", "str", "str", "int", "str", "str"],
        "request": True,
        "log": True,
        "calls": [[_model_state(ops), FMT]],
    }


def _rej(cls, calls=()):
    return {
        "err": cls,
        "code": export.FAILURE_MAPPING[cls],
        "typed": True,
        "cause": False,
        "forged": False,
        "calls": list(calls),
    }


# -- scenarios: (jobs, expected, labels) ---------------------------------------------------


def _prefix_jobs():
    jobs, want, labels = [], [], []
    for name, seq in (("base", BASE_OPS), ("alt", ALT_OPS), ("emptied", EMPTIED_OPS)):
        for n in range(len(seq) + 1):
            ops = seq[:n]
            jobs.append({"text": _text(ops), "steps": [{"do": "export"}, {"do": "export"}]})
            want.append({"steps": [_ok(ops), _ok(ops)], "log": _text(ops)})
            labels.append(f"{name}[:{n}]")
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
        ("request-extra-key", {"request": "extra-key"}, MER),
        ("request-missing-key", {"request": "missing-key"}, MER),
        ("request-renamed-key", {"request": "renamed-key"}, MER),
        ("request-subclass-key", {"request": "subclass-key"}, MER),
        ("request-not-a-dict", {"request": "not-a-dict"}, MER),
        ("format-subclass", {"request": "format-subclass"}, MER),
        ("format-bool", {"request": "format-bool"}, MER),
        ("format-none", {"request": "format-none"}, MER),
        ("format-bytes", {"request": "format-bytes"}, MER),
        ("format-upper", {"request": "format-upper"}, UF),
        ("format-newline", {"request": "format-newline"}, UF),
        ("format-v2", {"request": "format-v2"}, UF),
        ("format-empty", {"request": "format-empty"}, UF),
        ("log-tuple", {"wrap": "tuple"}, MER),
        ("log-dict", {"wrap": "dict"}, MER),
    ]
    for label, step, cls in rows:
        jobs.append({"text": text, "steps": [{"do": "export", **step}, {"do": "export"}]})
        want.append({"steps": [_rej(cls), _ok(BASE_OPS)], "log": text})
        labels.append(label)
    positions = {"first": 0, "middle": n // 2, "last": n - 1}
    for name, edit in _log_tampers().items():
        for where, p in positions.items():
            log = _model_log(BASE_OPS)
            edit(log[p], p)
            bad = _dumps(log)
            steps = [
                {"do": "export"},
                # precedence: request shape, then format type, then format
                # membership, all before source validation
                {"do": "export", "request": "format-v2"},
                {"do": "export", "request": "format-bool"},
                {"do": "export", "request": "extra-key"},
            ]
            jobs.append({"text": bad, "steps": steps})
            want.append({"steps": [_rej(CS), _rej(UF), _rej(MER), _rej(MER)], "log": bad})
            labels.append(f"{name}@{where}")
    return jobs, want, labels


def _hostile_rows():
    """(spec, ops, class) for every hostile-type row at the input boundaries."""
    rows = []
    for ops in ((), BASE_OPS):
        rows.append(("log-list-subclass", ops, MER))
        for spec in ("request-dict-subclass", "request-lying-dict"):
            rows.append((spec, ops, MER))
        for form in KEY_FORMS:
            rows.append((f"request-key:{form}", ops, MER))
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
        text = _text(ops)
        steps = [{"do": "hostile", "hostile": spec}]
        if cls != CS:  # a clean export follows when the durable log itself is clean
            steps.append({"do": "export"})
        jobs.append({"text": text, "steps": steps})
        hostile = {"hostile": [], "arg_unchanged": True, "request_unchanged": True}
        want.append(
            {"steps": [{**_rej(cls), **hostile}] + ([_ok(ops)] if cls != CS else []), "log": text}
        )
        labels.append(f"{spec}@{len(ops)}")
    return jobs, want, labels


EXPORTER_FAULTS = (
    [f"raise:{e}" for e in ("ValueError", "Base", "KeyboardInterrupt", "UnicodeEncodeError")]
    + [
        "none",
        "bytes",
        "subclass",
        "surrogate",
        "trailing-newline",
        "no-final-newline",
        "reversed",
        "spaced",
        "crlf",
        "dropped-line",
        "empty",
    ]
    + [f"forge-export:{c}" for c in sorted(export.FAILURE_MAPPING)]
    + [f"forge-wal:{c}" for c in sorted(wal.FAILURE_MAPPING)]
)
# faults whose output equals the honest document on an empty state
_EMPTY_HONEST = {"no-final-newline", "reversed", "spaced", "crlf", "dropped-line", "empty"}


def _fault_jobs():
    jobs, want, labels = [], [], []
    for ops in ((), BASE_OPS[:2], BASE_OPS):
        text = _text(ops)
        for fault in EXPORTER_FAULTS:
            if not ops and fault in _EMPTY_HONEST:
                continue
            jobs.append(
                {"text": text, "steps": [{"do": "export", "exporter": fault}, {"do": "export"}]}
            )
            want.append({"steps": [_rej(DE, [[_model_state(ops), FMT]]), _ok(ops)], "log": text})
            labels.append(f"{fault}@{len(ops)}")
    jobs.append({"text": _text(BASE_OPS), "steps": [{"do": "export", "exporter": "scribble"}]})
    want.append({"steps": [_ok(BASE_OPS)], "log": _text(BASE_OPS)})
    labels.append("scribbling-exporter")
    return jobs, want, labels


def _bad(builder, got):
    jobs, want, labels = builder()
    return [label for label, g, w in zip(labels, got, want, strict=True) if g != w]


# -- tests ----------------------------------------------------------------------------


def test_export_after_restart_of_every_prefix_matches_the_model_and_reference():
    jobs, _want, _labels = _prefix_jobs()
    got = _restart(jobs)
    assert _bad(_prefix_jobs, got) == []
    assert got == _local(jobs)
    for seq in (BASE_OPS, ALT_OPS, EMPTIED_OPS):
        for n in range(len(seq) + 1):
            log = _model_log(seq[:n])
            ref = _contract.ExportEngine(_contract.render_document).export(log, {"format": FMT})
            assert ref == _result(seq[:n])
            assert log == _model_log(seq[:n])


_CHAIN_TAIL = [{"do": "export"}, {"do": "truncate", "target": 5}, {"do": "export"}]


def test_export_restart_continue_truncate_export_chain():
    # process 1 exports, process 2 continues the durable log with other ops,
    # process 3 exports, truncates the log and exports again
    text = _text(BASE_OPS[:3])
    [r1] = _restart([{"text": text, "steps": [{"do": "export"}]}])
    assert r1 == {"steps": [_ok(BASE_OPS[:3])], "log": text}
    new = ALT_OPS[:4]
    [r2] = _restart(
        [{"text": r1["log"], "steps": [{"do": "append", "op": o, "index": i} for o, i in new]}]
    )
    ops = BASE_OPS[:3] + new
    assert r2["log"] == _text(ops)
    [r3] = _restart(
        [
            {
                "text": r2["log"],
                "steps": _CHAIN_TAIL,
            }
        ]
    )
    assert r3["steps"][0] == _ok(ops)
    assert r3["steps"][2] == _ok(ops[:5])
    assert r3["log"] == _text(ops[:5])
    assert r3 == _local([{"text": r2["log"], "steps": _CHAIN_TAIL}])[0]


def test_fresh_process_output_is_deterministic_across_hash_seeds():
    jobs, _want, _labels = _prefix_jobs()
    assert _restart(jobs, hash_seed="0", raw=True) == _restart(jobs, hash_seed="1", raw=True)


def test_boundaries_empty_log_emptied_state_and_unsorted_insertion():
    got = _restart(
        [
            {"text": "", "steps": [{"do": "export"}]},
            {"text": _text(EMPTIED_OPS), "steps": [{"do": "export"}]},
            {"text": _text(BASE_OPS), "steps": [{"do": "export"}]},
        ]
    )
    empty = got[0]["steps"][0]["ok"]
    assert empty["head"] == GENESIS and empty["record_count"] == 0 and empty["document"] == ""
    emptied = got[1]["steps"][0]["ok"]
    assert emptied["head"] != GENESIS and emptied["record_count"] == 0
    assert emptied["state_id"] == empty["state_id"] and emptied["export_id"] != empty["export_id"]
    # the replayed state's insertion order is not identity order; the document is sorted
    assert list(_model_state(BASE_OPS)) != sorted(_model_state(BASE_OPS))
    doc = got[2]["steps"][0]["ok"]["document"]
    ids = [json.loads(line)["identity"] for line in doc.split("\n")[:-1]]
    assert ids == sorted(_model_state(BASE_OPS))


def test_malformed_requests_and_tampered_logs_are_rejected_typed_and_untouched():
    jobs, _want, _labels = _malformed_jobs()
    got = _restart(jobs)
    assert _bad(_malformed_jobs, got) == []
    assert got == _local(jobs)


def test_exporter_faults_fail_closed_then_export_cleanly_before_and_after_restart():
    jobs, _want, _labels = _fault_jobs()
    got = _restart(jobs)
    assert _bad(_fault_jobs, got) == []
    assert got == _local(jobs)
    assert _restart([{"text": _text(BASE_OPS), "steps": [{"do": "export"}]}]) == [
        {"steps": [_ok(BASE_OPS)], "log": _text(BASE_OPS)}
    ]


def test_hostile_types_at_every_input_boundary_are_rejected_typed_and_inert():
    jobs, _want, _labels = _hostile_jobs()
    got = _restart(jobs)
    assert _bad(_hostile_jobs, got) == []
    assert got == _local(jobs)


def test_every_export_and_wal_error_class_is_forged():
    assert {f.split(":")[1] for f in EXPORTER_FAULTS if f.startswith("forge-export")} == set(
        export.FAILURE_MAPPING
    )
    assert {f.split(":")[1] for f in EXPORTER_FAULTS if f.startswith("forge-wal")} == set(
        wal.FAILURE_MAPPING
    )


# -- in-file mutants: each is RED on its own scenario -----------------------------------------


def _red(builder):
    def red(module):
        jobs, want, _labels = builder()
        return _local(jobs, module) != want

    return red


_BOUNDARY = '        except BaseException:\n            _fail("divergent_export")\n'
_KEYS = (
    "        if not all(type(key) is str for key in dict.keys(request)) or \\\n"
    "                set(dict.keys(request)) != _REQUEST_FIELDS:\n"
)
_TYPE = '        if type(fmt) is not str:\n            _fail("malformed_export_request")\n'
_MEMBER = '        if fmt not in FORMATS:\n            _fail("unsupported_format")\n'
_SOURCE = (
    "        try:\n            replayed = self._wal.replay(log)\n"
    '        except _wal.WalError:\n            _fail("corrupt_source")\n'
)

MUTANTS = {
    "request-keys-by-length": (
        [
            (
                "                set(dict.keys(request)) != _REQUEST_FIELDS:",
                "                len(dict.keys(request)) != len(_REQUEST_FIELDS):",
            )
        ],
        _red(_malformed_jobs),
    ),
    "wal-error-escapes": (
        [(_SOURCE, _SOURCE.replace('_fail("corrupt_source")', "raise"))],
        _red(_malformed_jobs),
    ),
    "log-container-isinstance": (
        [
            (
                "        if type(log) is not list or",
                "        if not isinstance(log, (list, tuple)) or",
            )
        ],
        _red(_malformed_jobs),
    ),
    "request-key-type-dropped": (
        [(_KEYS, "        if set(dict.keys(request)) != _REQUEST_FIELDS:\n")],
        _red(_malformed_jobs),
    ),
    "log-list-isinstance": (
        [("        if type(log) is not list or", "        if not isinstance(log, list) or")],
        _red(_hostile_jobs),
    ),
    "request-isinstance": (
        [
            (
                "type(request) is not dict:\n",
                "not isinstance(request, dict):\n",
            )
        ],
        _red(_hostile_jobs),
    ),
    "format-isinstance": (
        [("        if type(fmt) is not str:", "        if not isinstance(fmt, str):")],
        _red(_malformed_jobs),
    ),
    "format-membership-before-type": (
        [(_TYPE + _MEMBER, _MEMBER + _TYPE)],
        _red(_malformed_jobs),
    ),
    "source-before-format": (
        [(_TYPE + _MEMBER + _SOURCE, _SOURCE + _TYPE + _MEMBER)],
        _red(_malformed_jobs),
    ),
    "except-exception": (
        [(_BOUNDARY, _BOUNDARY.replace("BaseException", "Exception"))],
        _red(_fault_jobs),
    ),
    "export-error-passthrough": (
        [(_BOUNDARY, "        except ExportError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "wal-error-passthrough": (
        [(_BOUNDARY, "        except _wal.WalError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "unicode-error-passthrough": (
        [(_BOUNDARY, "        except UnicodeEncodeError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "output-isinstance": (
        [("        if type(out) is not str:", "        if not isinstance(out, str):")],
        _red(_fault_jobs),
    ),
    "binding-dropped": (
        [
            (
                "            if document != render_document(frozen_state, fmt):\n",
                "            if False:\n",
            )
        ],
        _red(_fault_jobs),
    ),
    "exporter-gets-live-state": (
        [
            (
                "                {key: dict(rec) for key, rec in frozen_state.items()}, fmt)",
                "                frozen_state, fmt)",
            )
        ],
        _red(_fault_jobs),
    ),
    "exporter-gets-shared-records": (
        [
            (
                "                {key: dict(rec) for key, rec in frozen_state.items()}, fmt)",
                "                dict(frozen_state), fmt)",
            )
        ],
        _red(_fault_jobs),
    ),
    "render-unsorted": (
        [("        for key in sorted(state))", "        for key in state)")],
        _red(_prefix_jobs),
    ),
    "record-count-counts-entries": (
        [("        count = len(frozen_state)", "        count = len(log)")],
        _red(_prefix_jobs),
    ),
    "export-id-drops-count": (
        [
            (
                '        f"{head}\\n{sid}\\n{record_count}\\n{fmt}\\n{document}"',
                '        f"{head}\\n{sid}\\n{fmt}\\n{document}"',
            )
        ],
        _red(_prefix_jobs),
    ),
}


def test_identity_mutant_is_green():
    module = _source_mutant("identity", [])
    for builder in (_prefix_jobs, _malformed_jobs, _fault_jobs, _hostile_jobs):
        jobs, want, _labels = builder()
        assert _local(jobs, module) == want, builder.__name__


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_scenario(name):
    edits, red = MUTANTS[name]
    assert red(_source_mutant(name, edits)), name


def test_a_mutant_is_red_in_a_fresh_process_too():
    jobs, want, _labels = _prefix_jobs()
    assert _restart(jobs, mutant="render-unsorted") != want
