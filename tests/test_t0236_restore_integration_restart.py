"""T0236 integration/restart battery for the production restore (store.restore).

The durable form is a JSON backup receipt (as store.backup emits it for a
JSON-lines WAL log). A restart is a fresh interpreter (subprocess) that
loads the receipt, builds a new RestoreEngine and restores. Every scenario
checks the fresh process against the uninterrupted in-process run, an
independent model of data/contracts/{wal,backup,restore}.yaml (entry-id
derivation, fold, bundle, backup-id, state-id and restore-id
derivations) and, on the happy path, the contract reference engine
(tests.test_t0230_restore_contract).

Coverage: happy (every prefix, a backup -> restore process pipeline,
repeated restores, hash-seed determinism), boundary (empty backup, long
log, a self-consistent receipt at the entry_count ceiling), malformed
(receipts the linked backup verify rejects; self-consistent forged
receipts whose content is wrong, so only restore's own checks catch
them) and rollback (every rejection leaves the persisted receipt
unchanged; a clean restore after a rejection, in the same process and
after a restart, equals the model). The bundle parser is the only
except-BaseException oracle boundary: every RestoreError and BackupError
class is forged from it and must fail closed as divergent_parse, never
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
import yaml

from graph.node import make_record, record_identity
from store import backup, restore
from tests import test_t0230_restore_contract as _contract

ROOT = Path(__file__).resolve().parents[1]
RESTORE_SOURCE = (ROOT / "store" / "restore.py").read_text()
_BC = yaml.safe_load((ROOT / "data" / "contracts" / "backup.yaml").read_text())["contract"]
_RC = yaml.safe_load((ROOT / "data" / "contracts" / "restore.yaml").read_text())["contract"]
RESULT_FIELDS = tuple(_RC["record"]["fields"])
COUNT_MAX = _BC["record"]["field_definitions"]["entry_count"]["max_value"]
GENESIS = "wal0:" + "0" * 64
MRR, UB, DP, DS = (
    "malformed_restore_record",
    "unverified_backup",
    "divergent_parse",
    "divergent_state",
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


def _body(rec):
    return "|".join(f"{f}={rec[f]}" for f in sorted(rec))


def _bundle(state, order=None):
    keys = sorted(state) if order is None else order
    return "".join(f"{k}\n{_body(state[k])}\n" for k in keys)


def _sid(state):
    return "gs1:" + hashlib.sha256(_bundle(state).encode()).hexdigest()


def _bid(head, sid, count, bundle):
    return "bck1:" + hashlib.sha256(f"{head}\n{sid}\n{count}\n{bundle}".encode()).hexdigest()


def _receipt(ops):
    state = _fold(ops)
    head = _model_log(ops)[-1]["entry_id"] if ops else GENESIS
    sid, bundle = _sid(state), _bundle(state)
    return {
        "backup_id": _bid(head, sid, len(ops), bundle),
        "head": head,
        "state_id": sid,
        "entry_count": len(ops),
        "bundle": bundle,
    }


def _forge(receipt, **over):
    """Self-consistent forgery: fields overridden, backup id rederived, so
    the linked backup verify accepts it."""
    r = {**receipt, **over}
    r["backup_id"] = _bid(r["head"], r["state_id"], r["entry_count"], r["bundle"])
    return r


def _restored(receipt, state):
    rid = (
        "rst1:"
        + hashlib.sha256(f"{receipt['backup_id']}\n{receipt['state_id']}".encode()).hexdigest()
    )
    return {
        "restore_id": rid,
        "backup_id": receipt["backup_id"],
        "state_id": receipt["state_id"],
        "state": state,
    }


# -- step runner (shared by the child and the in-process run) ---------------------------


class _Map(dict):
    pass


class _Base(BaseException):
    pass


HOSTILE = []  # hostile dunder calls observed after the parser returned


class _PlainKey(str):
    pass


class _EqRaisesKey(str):
    def __eq__(self, other):
        HOSTILE.append("eq")
        raise RuntimeError("eq")

    def __ne__(self, other):
        HOSTILE.append("ne")
        raise RuntimeError("ne")

    __hash__ = str.__hash__


def _colliding(target):
    class _HashCollidesKey(str):
        def __hash__(self):
            HOSTILE.append("hash")
            return hash(target)

        def __eq__(self, other):
            HOSTILE.append("eq")
            return str.__eq__(self, other)

        def __ne__(self, other):
            HOSTILE.append("ne")
            return str.__ne__(self, other)

    return _HashCollidesKey


def _hostile_key(form, text, target):
    return {"plain": _PlainKey, "eq-raises": _EqRaisesKey, "hash-collides": _colliding(target)}[
        form
    ](text)


class _LyingMap(dict):
    """A dict subclass whose own accessors log and lie about its content."""

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

    def __len__(self):
        HOSTILE.append("len")
        return 0


def _plain(value):
    """VALUE rebuilt through dict's own methods, never a subclass override."""
    if isinstance(value, dict):
        return {k: _plain(dict.__getitem__(value, k)) for k in dict.keys(value)}
    return value


def _hostile_receipt(spec, full):
    """A receipt carrying one hostile type, built in the running process."""
    kind, _, arg = spec.partition(":")
    if kind == "dict-subclass":
        return _Map(full)
    if kind == "lying-dict":
        return _LyingMap(full)
    if kind == "key":
        return {(_hostile_key(arg, k, "head") if k == "bundle" else k): v for k, v in full.items()}
    if kind == "value":
        return {**full, arg: _PlainKey(full[arg])}
    raise AssertionError(spec)


def _parser(module, spec, calls, forged, held):
    def parse(bundle):
        calls.append(bundle)
        kind, _, arg = spec.partition(":")
        if kind == "honest":
            return module.parse_bundle(bundle)
        if kind == "aliasing":  # honest output whose records the parser keeps
            out = module.parse_bundle(bundle)
            held.extend(out.values())
            return out
        if kind == "raise":
            raise {"ValueError": ValueError, "Base": _Base, "KeyboardInterrupt": KeyboardInterrupt}[
                arg
            ](arg)
        if kind == "forge-restore":
            forged[0] = restore.RestoreError(arg, restore.FAILURE_MAPPING[arg])
            raise forged[0]
        if kind == "forge-backup":
            forged[0] = backup.BackupError(arg, backup.FAILURE_MAPPING[arg])
            raise forged[0]
        honest = module.parse_bundle(bundle)
        if kind == "subclass":
            return _Map(honest)
        if kind == "items":
            return list(honest.items())
        if kind == "non-str-key":
            return {k.encode(): rec for k, rec in honest.items()}
        if kind == "non-dict-record":
            return {k: list(v.items()) for k, v in honest.items()}
        if kind == "extra-record":
            return {**honest, IDENTITIES[6]: dict(RECORDS[6])}
        if kind == "dropped-record":
            return dict(list(honest.items())[1:])
        if kind == "state-key":  # an honest state whose first identity key is a str subclass
            keys = list(honest)
            out = {_hostile_key(arg, keys[0], keys[-1]): honest[keys[0]]}
            out.update({k: honest[k] for k in keys[1:]})
            HOSTILE.clear()  # building the dict may hash/compare; only restore counts
            return out
        if kind == "state-lying-dict":
            out = _LyingMap(honest)
            HOSTILE.clear()
            return out
        if kind in ("record-dict-subclass", "record-lying-dict", "record-value"):
            first = next(iter(honest))
            rec = honest[first]
            if kind == "record-dict-subclass":
                rec = _Map(rec)
            elif kind == "record-lying-dict":
                rec = _LyingMap(rec)
            else:
                rec = {**rec, arg: _PlainKey(rec[arg])}
            out = {**honest, first: rec}
            HOSTILE.clear()
            return out
        if kind == "record-key":  # every record's "digest" field key is a str subclass
            out = {
                k: {
                    (_hostile_key(arg, f, "variant") if f == "digest" else f): v
                    for f, v in rec.items()
                }
                for k, rec in honest.items()
            }
            HOSTILE.clear()
            return out
        return None

    return parse


def _run(module, steps):
    out = []
    for step in steps:
        calls, forged, held = [], [None], []
        receipt = step["receipt"]
        if "hostile" in step:
            receipt = _hostile_receipt(step["hostile"], receipt)
        before = json.dumps(_plain(receipt))
        engine = module.RestoreEngine(
            _parser(module, step.get("parser", "honest"), calls, forged, held)
        )
        HOSTILE.clear()  # building the receipt may hash/compare; only restore counts
        try:
            got = engine.restore(receipt)
            for rec in held:  # scribble on everything the parser kept
                rec["variant"] = "poison"
            res = {"ok": got, "fields": list(got) == list(RESULT_FIELDS)}
        except restore.RestoreError as error:
            res = {
                "err": error.failure_class,
                "code": error.code,
                "typed": error.code == restore.FAILURE_MAPPING.get(error.failure_class),
                "cause": error.__cause__ is not None,
                "forged": error is forged[0],
            }
        except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
            res = {"raw": type(error).__name__}
        res["calls"] = calls
        if "hostile" in step or step.get("parser", "").startswith(_HOSTILE_PARSERS):
            res["hostile"] = list(HOSTILE)
        res["unchanged"] = json.dumps(_plain(receipt)) == before
        out.append(res)
    return out


def _source_mutant(name, edits):
    source = RESTORE_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0236_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "restore.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.RestoreError = restore.RestoreError

    def _fail(failure_class):
        raise restore.RestoreError(failure_class, restore.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _child_main():
    request = json.load(sys.stdin)
    name = request.get("mutant")
    module = restore if name is None else _source_mutant(name, MUTANTS[name][0])
    sys.stdout.write(json.dumps([_run(module, job) for job in request["jobs"]], sort_keys=True))


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0236_restore_integration_restart import _child_main; _child_main()"
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


def _local(jobs, module=restore):
    return json.loads(json.dumps([_run(module, job) for job in jobs], sort_keys=True))


def _ok(receipt, state):
    return {
        "ok": _restored(receipt, state),
        "fields": True,
        "calls": [receipt["bundle"]],
        "unchanged": True,
    }


def _rej(cls, calls):
    return {
        "err": cls,
        "code": restore.FAILURE_MAPPING[cls],
        "typed": True,
        "cause": False,
        "forged": False,
        "calls": calls,
        "unchanged": True,
    }


# -- scenarios: (jobs, expected, labels) ---------------------------------------------------


def _prefix_jobs():
    jobs, want, labels = [], [], []
    for k in range(len(BASE_OPS) + 1):
        r = _receipt(BASE_OPS[:k])
        jobs.append([{"receipt": r}, {"receipt": r}])
        want.append([_ok(r, _fold(BASE_OPS[:k]))] * 2)
        labels.append(f"prefix:{k}")
    r = _receipt(LONG_OPS)
    jobs.append([{"receipt": r}])
    want.append([_ok(r, _fold(LONG_OPS))])
    labels.append("long")
    # the entry_count ceiling: verify accepts a self-consistent receipt and
    # restore never reads the count
    r = _forge(_receipt(BASE_OPS), entry_count=COUNT_MAX)
    jobs.append([{"receipt": r}])
    want.append([_ok(r, _fold(BASE_OPS))])
    labels.append("count-at-max")
    return jobs, want, labels


def _unverified_rows():
    full, empty = _receipt(BASE_OPS), _receipt(())
    return {
        "not-a-dict": (list(full.items()), MRR),
        "missing-field": ({k: v for k, v in full.items() if k != "bundle"}, MRR),
        "extra-field": ({**full, "note": "x"}, MRR),
        "backup-id-grammar": ({**full, "backup_id": "bck2:" + full["backup_id"][5:]}, MRR),
        "head-trailing-newline": (_forge(full, head=full["head"] + "\n"), MRR),
        "count-over-max": (_forge(full, entry_count=COUNT_MAX + 1), MRR),
        "count-bool": (_forge(empty, entry_count=False), MRR),
        "bundle-none": ({**full, "bundle": None}, MRR),
        "bundle-surrogate": ({**full, "bundle": "\ud800"}, MRR),
        "bundle-edited": ({**full, "bundle": full["bundle"] + " "}, UB),
        "state-id-edited": ({**full, "state_id": _receipt(BASE_OPS[:3])["state_id"]}, UB),
        "forged-empty-count-nongenesis-head": (_forge(full, entry_count=0), UB),
        "forged-genesis-head-nonzero-count": (_forge(empty, entry_count=3), UB),
    }


def _content_rows():
    """Self-consistent receipts the linked verify accepts; only restore's
    own checks can reject them. (receipt, class)."""
    full = _receipt(BASE_OPS)
    state = _fold(BASE_OPS)
    keys = sorted(state)
    k0 = keys[0]
    bad_digest = {**state, k0: {**state[k0], "digest": RECORDS[5]["digest"]}}
    rekeyed = {k: v for k, v in state.items() if k != k0}
    rekeyed[k0 + "x"] = state[k0]
    other = _fold(BASE_OPS[:3])
    extra_field = {**state, k0: {**state[k0], "note": "x"}}
    dup = _bundle(state) + f"{k0}\n{_body(state[k0])}\n"
    return {
        # state id and bundle agree with each other, but a record is invalid
        "record-wrong-digest": (
            _forge(full, bundle=_bundle(bad_digest), state_id=_sid(bad_digest)),
            DS,
        ),
        "record-key-not-identity": (
            _forge(full, bundle=_bundle(rekeyed), state_id=_sid(rekeyed)),
            DS,
        ),
        "record-extra-field": (
            _forge(full, bundle=_bundle(extra_field), state_id=_sid(extra_field)),
            DS,
        ),
        # a valid bundle with another state's id
        "state-id-of-other-state": (_forge(full, state_id=_sid(other)), DS),
        "empty-bundle-nonempty-state-id": (_forge(full, bundle="", state_id=full["state_id"]), DS),
        # valid state, non-canonical bytes: only the round trip catches it
        "bundle-unsorted": (_forge(full, bundle=_bundle(state, order=keys[::-1])), DP),
        "bundle-duplicate-record": (_forge(full, bundle=dup), DP),
        "bundle-no-trailing-newline": (_forge(full, bundle=_bundle(state)[:-1]), DP),
    }


def _malformed_jobs():
    jobs, want, labels = [], [], []
    for name, (r, cls) in _unverified_rows().items():
        jobs.append([{"receipt": r}])
        want.append([_rej(cls, [])])
        labels.append(f"unverified:{name}")
    for name, (r, cls) in _content_rows().items():
        jobs.append([{"receipt": r}])
        want.append([_rej(cls, [r["bundle"]])])
        labels.append(f"content:{name}")
    return jobs, want, labels


PARSER_FAULTS = (
    [f"raise:{e}" for e in ("ValueError", "Base", "KeyboardInterrupt")]
    + ["none", "subclass", "items"]
    + [f"forge-restore:{c}" for c in sorted(restore.FAILURE_MAPPING)]
    + [f"forge-backup:{c}" for c in sorted(backup.FAILURE_MAPPING)]
)
PARSER_STATE_FAULTS = ("non-str-key", "non-dict-record", "extra-record", "dropped-record")
HOSTILE_FORMS = ("plain", "eq-raises", "hash-collides")
PARSER_KEY_FAULTS = tuple(
    f"{where}:{form}" for where in ("state-key", "record-key") for form in HOSTILE_FORMS
)
# hostile types in the untrusted parser's output: the state mapping itself,
# one record mapping, and each record string value
PARSER_HOSTILE_FAULTS = [
    ("state-lying-dict", DP),
    ("record-dict-subclass", DS),
    ("record-lying-dict", DS),
] + [(f"record-value:{f}", DS) for f in ("variant", "digest", "snapshot_fen")]
_HOSTILE_PARSERS = ("state-key", "record-key", "state-lying-dict", "record-")
RECEIPT_HOSTILE = (
    ["dict-subclass", "lying-dict"]
    + [f"key:{form}" for form in HOSTILE_FORMS]
    + [f"value:{f}" for f in ("backup_id", "head", "state_id", "bundle")]
)


def _fault_jobs():
    jobs, want, labels = [], [], []
    for ops in ((), BASE_OPS):
        r = _receipt(ops)
        faults = [(f, DP) for f in PARSER_FAULTS]
        if ops:  # an empty backup has no record to drop or rekey
            faults += [(f, DS) for f in PARSER_STATE_FAULTS]
        for fault, cls in faults:
            jobs.append([{"receipt": r, "parser": fault}, {"receipt": r}])
            want.append([_rej(cls, [r["bundle"]]), _ok(r, _fold(ops))])
            labels.append(f"{fault}@{len(ops)}")
        if ops:  # str-subclass keys in the parsed state: divergent, no hostile method runs
            for fault in PARSER_KEY_FAULTS:
                jobs.append([{"receipt": r, "parser": fault}, {"receipt": r}])
                want.append([{**_rej(DS, [r["bundle"]]), "hostile": []}, _ok(r, _fold(ops))])
                labels.append(f"{fault}@{len(ops)}")
            for fault, cls in PARSER_HOSTILE_FAULTS:
                jobs.append([{"receipt": r, "parser": fault}, {"receipt": r}])
                want.append([{**_rej(cls, [r["bundle"]]), "hostile": []}, _ok(r, _fold(ops))])
                labels.append(f"{fault}@{len(ops)}")
    r = _receipt(BASE_OPS)
    jobs.append([{"receipt": r, "parser": "aliasing"}])
    want.append([_ok(r, _fold(BASE_OPS))])
    labels.append("aliasing-parser")
    return jobs, want, labels


def _hostile_receipt_jobs():
    """Hostile types at the receipt boundary: malformed, before the parser
    or any user dunder runs, the receipt untouched; a clean restore after."""
    jobs, want, labels = [], [], []
    for ops in ((), BASE_OPS):
        r = _receipt(ops)
        for spec in RECEIPT_HOSTILE:
            jobs.append([{"receipt": r, "hostile": spec}, {"receipt": r}])
            want.append([{**_rej(MRR, []), "hostile": []}, _ok(r, _fold(ops))])
            labels.append(f"receipt:{spec}@{len(ops)}")
    return jobs, want, labels


# -- tests ----------------------------------------------------------------------------


def _bad(builder, got):
    jobs, want, labels = builder()
    return [label for label, g, w in zip(labels, got, want, strict=True) if g != w]


def test_restore_after_restart_at_every_prefix_matches_the_model():
    jobs, want, labels = _prefix_jobs()
    got = _restart(jobs)
    assert _bad(_prefix_jobs, got) == []
    assert got == _local(jobs)
    for k in range(len(BASE_OPS) + 1):
        r = _receipt(BASE_OPS[:k])
        ref = _contract.RestoreEngine(_contract.parse_bundle).restore(copy.deepcopy(r))
        assert ref == _restored(r, _fold(BASE_OPS[:k]))


def test_backup_then_restore_across_process_restarts():
    # process 1 backs the durable log up through the production backup,
    # process 2 and 3 restore the persisted receipt
    for ops in (BASE_OPS[:5], BASE_OPS, LONG_OPS):
        code = (
            "import sys, json; sys.path.insert(0, sys.argv[1]); from store import backup; "
            "log = [json.loads(line) for line in sys.stdin.read().splitlines()]; "
            "print(json.dumps(backup.BackupEngine(backup.serialize_bundle).backup(log)))"
        )
        text = "".join(json.dumps(e) + "\n" for e in _model_log(ops))
        run = subprocess.run(
            [sys.executable, "-c", code, str(ROOT)],
            input=text,
            text=True,
            capture_output=True,
            cwd=ROOT,
            timeout=100,
            check=False,
        )
        assert run.returncode == 0, run.stderr
        receipt = json.loads(run.stdout)
        assert receipt == _receipt(ops)
        first, second = _restart([[{"receipt": receipt}]]), _restart([[{"receipt": receipt}]])
        assert first == second == [[_ok(receipt, _fold(ops))]]


def test_fresh_process_output_is_deterministic_across_hash_seeds():
    jobs, _want, _labels = _prefix_jobs()
    assert _restart(jobs, hash_seed="0", raw=True) == _restart(jobs, hash_seed="1", raw=True)


def test_empty_backup_restores_the_empty_state():
    r = _receipt(())
    [[res]] = _restart([[{"receipt": r}]])
    assert res == _ok(r, {})
    assert res["ok"]["state_id"] == backup.EMPTY_STATE_ID


def test_malformed_and_forged_receipts_are_rejected_typed_and_untouched():
    jobs, _want, _labels = _malformed_jobs()
    got = _restart(jobs)
    assert _bad(_malformed_jobs, got) == []
    assert got == _local(jobs)


def test_forged_content_rows_pass_the_linked_backup_verify():
    engine = backup.BackupEngine(backup.serialize_bundle)
    for r, _cls in _content_rows().values():
        engine.verify(r)


def test_parser_faults_roll_back_then_restore_cleanly_before_and_after_restart():
    jobs, _want, _labels = _fault_jobs()
    got = _restart(jobs)
    assert _bad(_fault_jobs, got) == []
    assert got == _local(jobs)
    r = _receipt(BASE_OPS)
    assert _restart([[{"receipt": r}]]) == [[_ok(r, _fold(BASE_OPS))]]


def test_hostile_receipts_are_rejected_typed_and_inert_after_restart():
    jobs, _want, _labels = _hostile_receipt_jobs()
    got = _restart(jobs)
    assert _bad(_hostile_receipt_jobs, got) == []
    assert got == _local(jobs)


def test_every_restore_and_backup_error_class_is_forged():
    assert {f.split(":")[1] for f in PARSER_FAULTS if f.startswith("forge-restore")} == set(
        restore.FAILURE_MAPPING
    )
    assert {f.split(":")[1] for f in PARSER_FAULTS if f.startswith("forge-backup")} == set(
        backup.FAILURE_MAPPING
    )


# -- in-file mutants: each is RED on its own scenario -----------------------------------------


def _red(builder):
    def red(module):
        jobs, want, _labels = builder()
        return _local(jobs, module) != want

    return red


_BOUNDARY = '        except BaseException:\n            _fail("divergent_parse")\n'

MUTANTS = {
    "record-dict-isinstance": (
        # both record-type guards: the loop's exact check and _record_identity's
        [
            (
                "    if type(record) is not dict or \\\n",
                "    if not isinstance(record, dict) or \\\n",
            ),
            (
                "            if type(key) is not str or type(record) is not dict:",
                "            if type(key) is not str:",
            ),
        ],
        _red(_fault_jobs),
    ),
    "state-dict-isinstance": (
        [("        if type(out) is not dict:", "        if not isinstance(out, dict):")],
        _red(_fault_jobs),
    ),
    "state-key-type-dropped": (
        [
            (
                "            if type(key) is not str or type(record) is not dict:",
                "            if type(record) is not dict:",
            )
        ],
        _red(_fault_jobs),
    ),
    "record-key-type-dropped": (
        [("            not all(type(key) is str for key in dict.keys(record)) or \\\n", "")],
        _red(_fault_jobs),
    ),
    "verify-first-skipped": (
        [("            _BACKUP.verify(receipt)\n", "            pass\n")],
        _red(_malformed_jobs),
    ),
    "malformed-collapsed-to-unverified": (
        [
            (
                '            if err.failure_class == "malformed_backup_record":',
                "            if False:",
            )
        ],
        _red(_malformed_jobs),
    ),
    "except-exception": (
        [(_BOUNDARY, _BOUNDARY.replace("BaseException", "Exception"))],
        _red(_fault_jobs),
    ),
    "restore-error-passthrough": (
        [(_BOUNDARY, "        except RestoreError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "backup-error-passthrough": (
        [(_BOUNDARY, "        except _backup.BackupError:\n            raise\n" + _BOUNDARY)],
        _red(_fault_jobs),
    ),
    "parser-output-isinstance": (
        [("        if type(out) is not dict:", "        if not isinstance(out, dict):")],
        _red(_fault_jobs),
    ),
    "record-identity-unchecked": (
        [("            if _record_identity(record) != key:\n", "            if False:\n")],
        _red(_malformed_jobs),
    ),
    "state-id-unchecked": (
        [("        if state_id(state) != frozen_state_id:\n", "        if False:\n")],
        _red(_malformed_jobs),
    ),
    "round-trip-unchecked": (
        [("        if _backup.serialize_bundle(state) != frozen_bundle:\n", "        if False:\n")],
        _red(_malformed_jobs),
    ),
    "record-stored-by-reference": (
        [("            state[key] = dict(record)", "            state[key] = record")],
        _red(_fault_jobs),
    ),
    "restore-id-drops-state-id": (
        [
            (
                '        f"{backup_id}\\n{sid}".encode()).hexdigest()',
                '        f"{backup_id}".encode()).hexdigest()',
            )
        ],
        _red(_prefix_jobs),
    ),
}


def test_identity_mutant_is_green():
    module = _source_mutant("identity", [])
    for builder in (_prefix_jobs, _malformed_jobs, _fault_jobs, _hostile_receipt_jobs):
        jobs, want, _labels = builder()
        assert _local(jobs, module) == want, builder.__name__


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_scenario(name):
    edits, red = MUTANTS[name]
    assert red(_source_mutant(name, edits)), name


def test_a_mutant_is_red_in_a_fresh_process_too():
    jobs, want, _labels = _malformed_jobs()
    assert _restart(jobs, mutant="state-id-unchecked") != want
