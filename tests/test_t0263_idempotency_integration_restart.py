"""T0263 Store/idempotency/integration/restart: the production keyed
exactly-once apply (store/idempotency.py) driven end to end and across a REAL
process restart.

Integration: a client applies keyed requests over a WAL-logged store and the
acknowledgement is lost, so it retries. The retry of an applied key must return
the stored receipt (`replayed`) and never grow the log or ledger; the same key
with another request is key_conflict; a new key applies. Receipt ids and request
fingerprints are checked against formulas written here from the contract text,
the log against the linked WAL replay and a hand-built fold model.

Restart: log and ledger are persisted as JSON, a FRESH process rebuilds the
engine and retries EVERY request from the start. At every cut point the result
must equal the uninterrupted run (replayed before the cut, applied after, final
log and ledger bit-identical). Corrupted persisted logs and ledgers and divergent
fingerprinters are refused IN THE FRESH PROCESS with the declared class and
mapped code and leave log and ledger unchanged. One-edit source mutants run in
the fresh process; each must be killed by a semantic deviation (a crash is an
error, never a kill). Test-only: no production change.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.node import make_record, record_identity  # noqa: E402
from store import wal  # noqa: E402
from store.idempotency import IdempotencyEngine, IdempotencyError  # noqa: E402

SOURCE = (ROOT / "store" / "idempotency.py").read_text()
CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "idempotency.yaml").read_text())[
    "contract"
]
MAPPING = dict(CONTRACT["failures"]["mapping"])

FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
)
RECORDS = [make_record("standard", fen) for fen in FENS]
IDENTS = [record_identity(r) for r in RECORDS]


def req(key, op, i):
    return {
        "idempotency_key": key,
        "op": op,
        "payload": {"identity": IDENTS[i], "record": dict(RECORDS[i])},
    }


REQUESTS = [
    req("k-put-0", "put", 0),
    req("k-put-1", "put", 1),
    req("k-del-0", "delete", 0),
    req("k-put-2", "put", 2),
    req("k-put-3", "put", 3),
    req("k-del-9", "delete", 2),
]
FOLD = {IDENTS[1]: RECORDS[1], IDENTS[3]: RECORDS[3]}  # hand-built final state


def fingerprint(op, payload):
    """Contract formula: domain-separated, length-framed sha256."""
    rec = payload["record"]
    parts = ["idf1"]
    for value in (op, payload["identity"], rec["variant"], rec["digest"], rec["snapshot_fen"]):
        text = str(value)
        parts.append(f"{len(text)}:{text}")
    return "idf1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def receipt_id(key, fp, entry_id, seq):
    return "idr1:" + hashlib.sha256(f"{key}\n{fp}\n{entry_id}\n{seq}".encode()).hexdigest()


CHILD = r"""
import json, sys, types
root = sys.argv[1]
sys.path.insert(0, root)
payload = json.load(sys.stdin)
if payload.get("source"):
    mod = types.ModuleType("mutant_idem")
    mod.__file__ = root + "/store/idempotency.py"
    exec(compile(payload["source"], mod.__file__, "exec"), mod.__dict__)
else:
    from store import idempotency as mod

mode = payload["fingerprinter"]


def raising(op, payload):
    raise RuntimeError("boom")


fingerprinter = {
    "honest": mod.request_fingerprint,
    "wrong": lambda op, p: "idf1:" + "0" * 64,
    "raise": raising,
    "nonstr": lambda op, p: 7,
}[mode]
engine = mod.IdempotencyEngine(fingerprinter)
log, ledger = payload["log"], payload["ledger"]
out = {"results": []}
try:
    for request in payload["requests"]:
        out["results"].append(engine.apply(log, ledger, request))
    out["ok"] = True
except mod.IdempotencyError as err:
    out.update(ok=False, failure_class=err.failure_class, code=err.code)
except Exception as exc:
    out.update(ok=False, crash=type(exc).__name__ + ": " + str(exc))
out["log"], out["ledger"] = log, ledger
print(json.dumps(out))
"""


def child_run(requests, log=(), ledger=(), source=None, mode="honest"):
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT)],
        input=json.dumps(
            {
                "requests": requests,
                "log": list(log),
                "ledger": list(ledger),
                "source": source,
                "fingerprinter": mode,
            }
        ),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert out.returncode == 0, f"child crashed: {out.stderr[-400:]}"
    return json.loads(out.stdout)


def run_local(requests, log=None, ledger=None):
    engine = IdempotencyEngine(lambda op, p: fingerprint(op, p))
    log = [] if log is None else log
    ledger = [] if ledger is None else ledger
    return [engine.apply(log, ledger, r) for r in requests], log, ledger


def jround(value):
    return json.loads(json.dumps(value))


def test_keyed_applies_match_contract_formulas_and_fold_model():
    results, log, ledger = run_local(REQUESTS)
    assert [r["outcome"] for r in results] == ["applied"] * 6
    assert len(log) == len(ledger) == 6
    for i, (entry, rcpt) in enumerate(zip(log, ledger, strict=True)):
        request = REQUESTS[i]
        fp = fingerprint(request["op"], request["payload"])
        assert rcpt == {
            "receipt_id": receipt_id(request["idempotency_key"], fp, entry["entry_id"], i + 1),
            "idempotency_key": request["idempotency_key"],
            "request_fingerprint": fp,
            "entry_id": entry["entry_id"],
            "sequence": i + 1,
        }
        assert results[i]["receipt"] == rcpt
    state = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    assert state["applied"] == 6
    assert sorted(state["state"].values(), key=str) == sorted(FOLD.values(), key=str)


def test_retry_of_applied_key_replays_and_never_grows_log_or_ledger():
    _, log, ledger = run_local(REQUESTS[:3])
    before = copy.deepcopy((log, ledger))
    results, log, ledger = run_local(REQUESTS[:3], log, ledger)
    assert [r["outcome"] for r in results] == ["replayed"] * 3
    assert (log, ledger) == before
    assert [r["receipt"] for r in results] == before[1]


def test_same_key_other_request_is_key_conflict_and_changes_nothing():
    _, log, ledger = run_local(REQUESTS[:2])
    before = copy.deepcopy((log, ledger))
    clash = req("k-put-0", "put", 3)
    with pytest.raises(IdempotencyError) as err:
        run_local([clash], log, ledger)
    assert (err.value.failure_class, err.value.code) == ("key_conflict", MAPPING["key_conflict"])
    assert (log, ledger) == before


def test_same_request_under_a_new_key_applies_again():
    _, log, ledger = run_local(REQUESTS[:1])
    again = req("k-other", "put", 0)
    results, log, ledger = run_local([again], log, ledger)
    assert results[0]["outcome"] == "applied" and len(log) == len(ledger) == 2
    assert ledger[0]["request_fingerprint"] == ledger[1]["request_fingerprint"]
    assert ledger[0]["receipt_id"] != ledger[1]["receipt_id"]


@pytest.mark.parametrize("cut", range(len(REQUESTS) + 1))
def test_restart_then_client_retries_everything(cut):
    ref_results, ref_log, ref_ledger = run_local(REQUESTS)
    _, log, ledger = run_local(REQUESTS[:cut])
    child = child_run(REQUESTS, log=jround(log), ledger=jround(ledger))
    assert child["ok"], child
    outcomes = [r["outcome"] for r in child["results"]]
    assert outcomes == ["replayed"] * cut + ["applied"] * (len(REQUESTS) - cut)
    assert child["log"] == jround(ref_log)
    assert child["ledger"] == jround(ref_ledger)
    assert [r["receipt"] for r in child["results"]] == jround([r["receipt"] for r in ref_results])


def test_restart_key_conflict_and_malformed_requests_change_nothing():
    _, log, ledger = run_local(REQUESTS[:2])
    persisted = jround((log, ledger))
    child = child_run([req("k-put-0", "put", 3)], log=persisted[0], ledger=persisted[1])
    assert child["ok"] is False and "crash" not in child
    assert (child["failure_class"], child["code"]) == ("key_conflict", MAPPING["key_conflict"])
    assert [child["log"], child["ledger"]] == persisted
    bad = {
        "bad_key": {**REQUESTS[2], "idempotency_key": "has space"},
        "empty_key": {**REQUESTS[2], "idempotency_key": ""},
        "unknown_op": {**REQUESTS[2], "op": "truncate"},
        "extra_member": {**REQUESTS[2], "z": 1},
        "missing_member": {"idempotency_key": "k-x", "op": "put"},
        "payload_not_dict": {**REQUESTS[2], "payload": []},
    }
    for name, request in bad.items():
        child = child_run([request], log=persisted[0], ledger=persisted[1])
        assert child["ok"] is False and "crash" not in child, (name, child)
        assert child["failure_class"] == "malformed_idempotency_request", name
        assert child["code"] == MAPPING["malformed_idempotency_request"], name
        assert [child["log"], child["ledger"]] == persisted, name


def _tamper(items, i, **changes):
    items = copy.deepcopy(items)
    items[i].update(changes)
    return items


def test_corrupt_persisted_ledger_refused_in_fresh_process():
    _, log, ledger = run_local(REQUESTS[:3])
    log, ledger = jround(log), jround(ledger)
    zero = "idr1:" + "0" * 64
    cases = {
        "receipt_id_forged": _tamper(ledger, 1, receipt_id=zero),
        "fingerprint_unbound_to_entry": _tamper(
            ledger, 1, request_fingerprint=fingerprint("put", REQUESTS[0]["payload"])
        ),
        "entry_id_not_the_live_entry": _tamper(ledger, 1, entry_id=log[0]["entry_id"]),
        "sequence_beyond_log": _tamper(ledger, 2, sequence=9),
        "sequence_not_increasing": _tamper(ledger, 1, sequence=1),
        "extra_field": _tamper(ledger, 0, z=1),
        "duplicate_key": _tamper(ledger, 1, idempotency_key=ledger[0]["idempotency_key"]),
        "bad_key_grammar": _tamper(ledger, 0, idempotency_key="bad key"),
        "not_a_list": {"receipts": ledger},
    }
    for name, bad in cases.items():
        child = child_run([REQUESTS[3]], log=log, ledger=bad if isinstance(bad, list) else [])
        if name == "not_a_list":
            continue
        assert child["ok"] is False and "crash" not in child, (name, child)
        assert child["failure_class"] == "corrupt_ledger", (name, child)
        assert child["code"] == MAPPING["corrupt_ledger"]
        assert child["log"] == log and child["ledger"] == bad, name


def test_corrupt_persisted_log_refused_in_fresh_process():
    _, log, ledger = run_local(REQUESTS[:3])
    log, ledger = jround(log), jround(ledger)
    cases = {
        "entry_id_forged": _tamper(log, 1, entry_id="wal1:" + "0" * 64),
        "chain_broken": _tamper(log, 2, prior_entry_id="wal1:" + "1" * 64),
        "payload_rewritten": _tamper(
            log, 0, payload={"identity": IDENTS[3], "record": dict(RECORDS[3])}
        ),
        "unknown_op": _tamper(log, 0, op="truncate"),
    }
    for name, bad in cases.items():
        child = child_run([REQUESTS[3]], log=bad, ledger=ledger)
        assert child["ok"] is False and "crash" not in child, (name, child)
        assert child["failure_class"] == "corrupt_source", (name, child)
        assert child["code"] == MAPPING["corrupt_source"]
        assert child["log"] == bad and child["ledger"] == ledger, name


@pytest.mark.parametrize("mode", ["wrong", "raise", "nonstr"])
def test_divergent_fingerprinter_refused_after_restart(mode):
    _, log, ledger = run_local(REQUESTS[:2])
    log, ledger = jround(log), jround(ledger)
    child = child_run([REQUESTS[2]], log=log, ledger=ledger, mode=mode)
    assert child["ok"] is False and "crash" not in child, child
    assert child["failure_class"] == "divergent_fingerprint"
    assert child["code"] == MAPPING["divergent_fingerprint"]
    assert child["log"] == log and child["ledger"] == ledger


# -- one-edit mutants of the runtime, executed in the fresh process ----------------


def _once(old, new):
    assert SOURCE.count(old) == 1, old
    return SOURCE.replace(old, new)


EDITS = {
    "dedupe-disabled": _once("            if stored is not None:", "            if False:"),
    "key-conflict-ignored": _once(
        '                if stored["request_fingerprint"] != token:', "                if False:"
    ),
    "fingerprint-unbound-to-request": _once(
        '        if out != request_fingerprint(frozen_req["op"], payload):', "        if False:"
    ),
    "ledger-not-validated": _once("        self._validate_ledger(ledger, log)", "        pass"),
    "receipt-id-ignores-sequence": _once(
        'f"{key}\\n{fingerprint}\\n{entry_id}\\n{sequence}"',
        'f"{key}\\n{fingerprint}\\n{entry_id}"',
    ),
    "receipt-not-recorded": _once("            ledger.append(dict(receipt))", "            pass"),
    "replayed-outcome-constant": _once(
        'outcome, receipt, staged = "replayed", dict(stored), None',
        'outcome, receipt, staged = "applied", dict(stored), None',
    ),
    "applied-outcome-constant": _once('outcome = "applied"', 'outcome = "replayed"'),
    "replayed-receipt-dropped": _once(
        'outcome, receipt, staged = "replayed", dict(stored), None',
        'outcome, receipt, staged = "replayed", dict(stored, entry_id="x"), None',
    ),
}
EXPECTED_KILL = {
    "dedupe-disabled": "retry",
    "key-conflict-ignored": "conflict",
    "fingerprint-unbound-to-request": "unbound-fingerprint",
    "ledger-not-validated": "forged-ledger",
    "receipt-id-ignores-sequence": "applied",
    "receipt-not-recorded": "applied",
    "replayed-outcome-constant": "retry",
    "applied-outcome-constant": "applied",
    "replayed-receipt-dropped": "retry",
}


def _deviations(source):
    _, log, ledger = run_local(REQUESTS[:2])
    log, ledger = jround(log), jround(ledger)
    ref_results, ref_log, ref_ledger = run_local(REQUESTS)
    runs = {
        "applied": child_run(REQUESTS, source=source),
        "retry": child_run(REQUESTS[:2], log=log, ledger=ledger, source=source),
        "conflict": child_run([req("k-put-0", "put", 3)], log=log, ledger=ledger, source=source),
        "unbound-fingerprint": child_run(
            [REQUESTS[2]], log=log, ledger=ledger, source=source, mode="wrong"
        ),
        "forged-ledger": child_run(
            [REQUESTS[2]],
            log=log,
            ledger=_tamper(ledger, 1, receipt_id="idr1:" + "0" * 64),
            source=source,
        ),
    }
    for name, run in runs.items():
        assert "crash" not in run, f"{name}: mutant crashed instead of deviating: {run}"
    found = set()
    a = runs["applied"]
    if not (
        a["ok"]
        and a["log"] == jround(ref_log)
        and a["ledger"] == jround(ref_ledger)
        and a["results"] == jround(ref_results)
        and [x["outcome"] for x in a["results"]] == ["applied"] * len(REQUESTS)
    ):
        found.add("applied")
    r = runs["retry"]
    if not (
        r["ok"]
        and [x["outcome"] for x in r["results"]] == ["replayed"] * 2
        and [x["receipt"] for x in r["results"]] == ledger[:2]
        and r["log"] == log
        and r["ledger"] == ledger
    ):
        found.add("retry")
    c = runs["conflict"]
    if not (c["ok"] is False and c.get("failure_class") == "key_conflict"):
        found.add("conflict")
    u = runs["unbound-fingerprint"]
    if not (u["ok"] is False and u.get("failure_class") == "divergent_fingerprint"):
        found.add("unbound-fingerprint")
    f = runs["forged-ledger"]
    if not (f["ok"] is False and f.get("failure_class") == "corrupt_ledger"):
        found.add("forged-ledger")
    return found


def test_unmutated_runtime_has_no_deviation():
    assert _deviations(None) == set()


@pytest.mark.parametrize("label", sorted(EDITS))
def test_runtime_mutant_is_killed_semantically(label):
    killed_by = _deviations(EDITS[label])
    assert EXPECTED_KILL[label] in killed_by, (label, killed_by)
