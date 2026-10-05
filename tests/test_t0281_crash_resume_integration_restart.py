"""T0281 Store/crash resume/integration/restart: the production torn-tail
recovery (store/crash_resume.py) driven end to end across a REAL process restart.

Integration: a WAL log is built through the linked production WAL. A crash is
modelled as durable bytes whose tail is damaged: a half-written entry, a mangled
entry id, a junk entry, or a corrupt entry followed by entries that would be
valid on their own. A fresh process loads the JSON, resumes at the checkpoint
and must keep exactly the longest verified prefix, quarantine exactly the torn
tail (token checked against a formula written here from the contract) and remove
it last. The same process then re-appends the lost operations through the WAL and
the final log must be bit-identical to the log that was never interrupted, at
every cut point.

Acknowledged data that the crash destroyed (checkpoint beyond the verified prefix
or beyond the log) is refused with corrupt_source and nothing is removed.
Malformed requests, a negative checkpoint and divergent quarantine sinks are
refused with the declared class and mapped code and leave the log unchanged.
One-edit source mutants run in the fresh process and each must be killed by a
semantic deviation (a crash is an error, never a kill). Test-only: no production
change.
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
from tools.crash_resume_contract_lint import MAX_DEPTH  # noqa: E402

SOURCE = (ROOT / "store" / "crash_resume.py").read_text()
CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "crash_resume.yaml").read_text())[
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
OPS = [("put", 0), ("put", 1), ("delete", 0), ("put", 2), ("put", 3), ("delete", 2)]
REQUESTS = [
    {"op": op, "payload": {"identity": IDENTS[i], "record": dict(RECORDS[i])}} for op, i in OPS
]


def build_log(count=None):
    engine = wal.WalEngine(wal.canonical_payload)
    log = []
    for request in REQUESTS[:count]:
        engine.append(log, copy.deepcopy(request))
    return log


REF_LOG = build_log()


def token_of(tail):
    """Contract formula: domain-separated, length-framed canonical JSON."""
    parts = ["qtn1"]
    for entry in tail:
        text = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        parts.append(f"{len(text)}:{text}")
    return "qtn1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def resume_id(head, state_id, resumed, discarded, token):
    return (
        "rsm1:"
        + hashlib.sha256(
            f"{head}\n{state_id}\n{resumed}\n{discarded}\n{token}".encode()
        ).hexdigest()
    )


CHILD = r"""
import json, sys, types
root = sys.argv[1]
sys.path.insert(0, root)
payload = json.load(sys.stdin)
if payload.get("source"):
    mod = types.ModuleType("mutant_resume")
    mod.__file__ = root + "/store/crash_resume.py"
    exec(compile(payload["source"], mod.__file__, "exec"), mod.__dict__)
else:
    from store import crash_resume as mod
from store import wal


def raising(tail):
    raise RuntimeError("boom")


sink = {
    "honest": mod.quarantine_tail,
    "wrong": lambda tail: "qtn1:" + "0" * 64,
    "raise": raising,
    "nonstr": lambda tail: 7,
}[payload["sink"]]
engine = mod.ResumeEngine(sink)
log = payload["log"]
out = {}
try:
    out["receipt"] = engine.resume(log, payload["request"])
    out["after_resume"] = json.loads(json.dumps(log))
    wal_engine = wal.WalEngine(wal.canonical_payload)
    for request in payload["continue"]:
        wal_engine.append(log, request)
    out["ok"] = True
except mod.ResumeError as err:
    out.update(ok=False, failure_class=err.failure_class, code=err.code)
except Exception as exc:
    out.update(ok=False, crash=type(exc).__name__ + ": " + str(exc))
out["log"] = log
print(json.dumps(out))
"""


def child_run(log, checkpoint, cont=(), source=None, sink="honest", request=None):
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT)],
        input=json.dumps(
            {
                "log": log,
                "request": {"checkpoint_sequence": checkpoint} if request is None else request,
                "continue": list(cont),
                "source": source,
                "sink": sink,
            }
        ),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert out.returncode == 0, f"child crashed: {out.stderr[-400:]}"
    return json.loads(out.stdout)


def jround(value):
    return json.loads(json.dumps(value))


def tears(k):
    """Damaged tails that follow a verified prefix of length k (k < len)."""
    nxt = copy.deepcopy(REF_LOG[k])
    half = {key: value for key, value in nxt.items() if key != "prior_entry_id"}
    mangled = {**nxt, "entry_id": "wal1:" + "0" * 64}
    later = copy.deepcopy(REF_LOG[k + 1 :])
    return {
        "half-written-entry": [half],
        "mangled-entry-id": [mangled],
        "junk-entry": [{"junk": 1}],
        "corrupt-then-valid-looking": [mangled, *later],
    }


CASES = [(k, name) for k in range(len(REF_LOG)) for name in sorted(tears(k))]


@pytest.mark.parametrize("k,name", CASES)
def test_torn_tail_is_resumed_and_the_log_converges_after_restart(k, name):
    tail = tears(k)[name]
    log = jround([*REF_LOG[:k], *tail])
    child = child_run(log, k, cont=REQUESTS[k:])
    assert child["ok"], child
    receipt = child["receipt"]
    assert child["after_resume"] == jround(REF_LOG[:k]), "exactly the torn tail is removed"
    assert receipt["resumed_count"] == k
    assert receipt["discarded_count"] == len(tail)
    assert receipt["quarantine_token"] == token_of(tail)
    head = REF_LOG[k - 1]["entry_id"] if k else wal.GENESIS
    assert receipt["head"] == head
    state_id = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(REF_LOG[:k]))["state_id"]
    assert receipt["state_id"] == state_id
    assert receipt["resume_id"] == resume_id(
        head, state_id, k, len(tail), receipt["quarantine_token"]
    )
    assert child["log"] == jround(REF_LOG), "re-appended log must equal the uninterrupted log"


@pytest.mark.parametrize("n", range(len(REF_LOG) + 1))
def test_clean_log_resumes_with_an_empty_quarantine(n):
    child = child_run(jround(REF_LOG[:n]), n, cont=REQUESTS[n:])
    assert child["ok"], child
    receipt = child["receipt"]
    assert (receipt["resumed_count"], receipt["discarded_count"]) == (n, 0)
    assert (
        receipt["quarantine_token"] == token_of([]) == "qtn1:" + hashlib.sha256(b"qtn1").hexdigest()
    )
    assert child["after_resume"] == jround(REF_LOG[:n])
    assert child["log"] == jround(REF_LOG)


def test_checkpoint_below_the_valid_prefix_keeps_the_whole_valid_prefix():
    log = jround([*REF_LOG[:4], {"junk": 1}])
    child = child_run(log, 1)
    assert child["ok"], child
    assert child["receipt"]["resumed_count"] == 4 and child["receipt"]["discarded_count"] == 1
    assert child["log"] == jround(REF_LOG[:4])


def test_destroyed_acknowledged_entries_are_refused_and_nothing_is_removed():
    cases = {
        "checkpoint_inside_the_torn_tail": (jround([*REF_LOG[:3], {"junk": 1}]), 4),
        "acknowledged_entry_mangled": (
            jround(
                [
                    *REF_LOG[:2],
                    {**REF_LOG[2], "entry_id": "wal1:" + "0" * 64},
                    *REF_LOG[3:],
                ]
            ),
            5,
        ),
        "checkpoint_beyond_the_log": (jround(REF_LOG[:3]), 4),
    }
    for name, (log, checkpoint) in cases.items():
        child = child_run(log, checkpoint)
        assert child["ok"] is False and "crash" not in child, (name, child)
        assert child["failure_class"] == "corrupt_source", name
        assert child["code"] == MAPPING["corrupt_source"], name
        assert child["log"] == log, f"{name}: a refused resume must not touch the log"


def test_malformed_requests_and_negative_checkpoint_are_refused_after_restart():
    log = jround(REF_LOG[:3])
    bad = {
        "negative": ({"checkpoint_sequence": -1}, "unknown_checkpoint"),
        "float": ({"checkpoint_sequence": 1.0}, "malformed_resume_record"),
        "bool": ({"checkpoint_sequence": True}, "malformed_resume_record"),
        "string": ({"checkpoint_sequence": "1"}, "malformed_resume_record"),
        "extra_member": ({"checkpoint_sequence": 1, "z": 1}, "malformed_resume_record"),
        "missing_member": ({}, "malformed_resume_record"),
    }
    for name, (request, cls) in bad.items():
        child = child_run(log, 0, request=request)
        assert child["ok"] is False and "crash" not in child, (name, child)
        assert (child["failure_class"], child["code"]) == (cls, MAPPING[cls]), name
        assert child["log"] == log, name


@pytest.mark.parametrize("sink", ["wrong", "raise", "nonstr"])
def test_divergent_sink_refused_and_the_tail_is_not_removed(sink):
    log = jround([*REF_LOG[:3], {"junk": 1}])
    child = child_run(log, 3, sink=sink)
    assert child["ok"] is False and "crash" not in child, child
    assert child["failure_class"] == "divergent_quarantine"
    assert child["code"] == MAPPING["divergent_quarantine"]
    assert child["log"] == log


def test_resume_is_idempotent_across_a_second_restart():
    log = jround([*REF_LOG[:3], {"junk": 1}])
    first = child_run(log, 3)
    again = child_run(first["log"], 3)
    assert again["ok"] and again["receipt"]["discarded_count"] == 0
    assert again["receipt"]["head"] == first["receipt"]["head"]
    assert again["log"] == first["log"]


def _nest_list(levels):
    node = []
    for _ in range(levels):
        node = [node]
    return node


def _nest_dict(levels):
    node = {}
    for _ in range(levels):
        node = {"k": node}
    return node


def _admission_rows():
    """(name, tail entry, admitted): the closed-domain boundary of a discarded
    entry. The entry itself is depth 1, so a nest of n levels reaches depth n + 1."""
    limit = MAX_DEPTH
    return [
        ("list-at-limit", {"x": _nest_list(limit - 2)}, True),
        ("list-over-limit", {"x": _nest_list(limit - 1)}, False),
        ("dict-at-limit", {"x": _nest_dict(limit - 2)}, True),
        ("dict-over-limit", {"x": _nest_dict(limit - 1)}, False),
        ("dict-nesting-300", {"x": _nest_dict(300)}, False),
        ("list-nesting-300", {"x": _nest_list(300)}, False),
        ("none-and-bool-scalars", {"a": None, "b": True, "c": False}, True),
        ("none-in-list", {"a": [None, True, False]}, True),
        ("lone-surrogate-value", {"x": "\ud800"}, False),
        ("lone-surrogate-key", {"\ud800": 1}, False),
        ("float-nan-free", {"x": 1.5}, True),
    ]


def _admission_failure(source):
    base = jround(REF_LOG[:3])
    for name, entry, admitted in _admission_rows():
        log = [*base, entry]
        run = child_run(log, 3, source=source)
        assert "crash" not in run, f"{name}: crashed instead of deviating: {run}"
        if admitted:
            ok = run["ok"] and run["receipt"]["discarded_count"] == 1 and run["log"] == base
        else:
            ok = (
                run["ok"] is False
                and run.get("failure_class") == "malformed_resume_record"
                and run["log"] == log
            )
        if not ok:
            return name
    return None


def test_tail_admission_boundary_after_restart():
    assert _admission_failure(None) is None


# -- one-edit mutants of the runtime, executed in the fresh process ----------------


def _once(old, new):
    assert SOURCE.count(old) == 1, old
    return SOURCE.replace(old, new)


EDITS = {
    "tail-not-removed": _once("        del log[k:]", "        pass"),
    "acknowledged-guard-dropped": _once("        if k < checkpoint:", "        if False:"),
    "prefix-not-longest": _once("for k in range(cap, -1, -1):", "for k in range(cap - 1, -1, -1):"),
    "quarantine-unbound-to-tail": _once(
        "            if token != quarantine_tail(frozen_tail):", "            if False:"
    ),
    "resume-id-ignores-discarded": _once(
        "{resumed}\\n{discarded}\\n{token}", "{resumed}\\n{token}"
    ),
    "depth-bound-off-by-one": _once("if depth > MAX_DEPTH or", "if depth >= MAX_DEPTH or"),
    "dict-depth-not-counted": _once(
        "                    stack.append((value, depth + 1))\n            else:",
        "                    stack.append((value, depth))\n            else:",
    ),
    "list-depth-not-counted": _once(
        "                for value in node:\n                    stack.append((value, depth + 1))",
        "                for value in node:\n                    stack.append((value, depth))",
    ),
    "none-bool-inadmissible": _once(
        "    if obj is None or kind is bool:\n        return True",
        "    if False:\n        return True",
    ),
    "surrogate-admitted": _once(
        '        try:\n            obj.encode("utf-8")\n        except UnicodeEncodeError:\n'
        "            return False\n        return True",
        "        return True",
    ),
    "key-scalar-unchecked": _once(
        "if type(key) is not str or not _scalar_ok(key):", "if type(key) is not str:"
    ),
    "tail-truncated-to-first-entry": _once(
        "        tail = log[k:]", "        tail = log[k : k + 1]"
    ),
}
EXPECTED_KILL = {
    "tail-not-removed": "torn",
    "acknowledged-guard-dropped": "destroyed",
    "prefix-not-longest": "clean",
    "quarantine-unbound-to-tail": "unbound-sink",
    "resume-id-ignores-discarded": "torn",
    "tail-truncated-to-first-entry": "multi-entry-tail",
    "depth-bound-off-by-one": "admit",
    "dict-depth-not-counted": "admit",
    "list-depth-not-counted": "admit",
    "none-bool-inadmissible": "admit",
    "surrogate-admitted": "admit",
    "key-scalar-unchecked": "admit",
}


def _deviations(source):
    k = 3
    tail = tears(k)["corrupt-then-valid-looking"]
    torn_log = jround([*REF_LOG[:k], *tail])
    head = REF_LOG[k - 1]["entry_id"]
    state_id = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(REF_LOG[:k]))["state_id"]
    runs = {
        "torn": child_run(torn_log, k, source=source),
        "multi-entry-tail": child_run(torn_log, k, source=source),
        "clean": child_run(jround(REF_LOG), len(REF_LOG), source=source),
        "destroyed": child_run(jround([*REF_LOG[:3], {"junk": 1}]), 4, source=source),
        "unbound-sink": child_run(torn_log, k, source=source, sink="wrong"),
    }
    for name, run in runs.items():
        assert "crash" not in run, f"{name}: mutant crashed instead of deviating: {run}"
    found = set()
    t = runs["torn"]
    expected_receipt_id = resume_id(head, state_id, k, len(tail), token_of(tail))
    if not (
        t["ok"]
        and t["after_resume"] == jround(REF_LOG[:k])
        and t["receipt"]["resume_id"] == expected_receipt_id
    ):
        found.add("torn")
    m = runs["multi-entry-tail"]
    if not (
        m["ok"]
        and m["receipt"]["discarded_count"] == len(tail)
        and m["receipt"]["quarantine_token"] == token_of(tail)
    ):
        found.add("multi-entry-tail")
    c = runs["clean"]
    if not (
        c["ok"]
        and c["receipt"]["resumed_count"] == len(REF_LOG)
        and c["after_resume"] == jround(REF_LOG)
    ):
        found.add("clean")
    d = runs["destroyed"]
    if not (
        d["ok"] is False
        and d.get("failure_class") == "corrupt_source"
        and d["log"] == jround([*REF_LOG[:3], {"junk": 1}])
    ):
        found.add("destroyed")
    u = runs["unbound-sink"]
    if not (u["ok"] is False and u.get("failure_class") == "divergent_quarantine"):
        found.add("unbound-sink")
    if _admission_failure(source) is not None:
        found.add("admit")
    return found


def test_unmutated_runtime_has_no_deviation():
    assert _deviations(None) == set()


@pytest.mark.parametrize("label", sorted(EDITS))
def test_runtime_mutant_is_killed_semantically(label):
    killed_by = _deviations(EDITS[label])
    assert EXPECTED_KILL[label] in killed_by, (label, killed_by)


def test_pinned_reference_log_shape():
    assert [e["sequence"] for e in REF_LOG] == [1, 2, 3, 4, 5, 6]
    assert REF_LOG[0]["prior_entry_id"] == wal.GENESIS
    assert all(REF_LOG[i]["prior_entry_id"] == REF_LOG[i - 1]["entry_id"] for i in range(1, 6))
