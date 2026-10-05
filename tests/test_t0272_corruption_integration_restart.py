"""T0272 Store/corruption/integration/restart: the production corruption scan
(store/corruption.py) driven end to end across a REAL process restart.

Integration: a WAL log is built through the linked production WAL, then damaged
in the ways storage damages it: a flipped payload byte, two swapped entries, a
duplicated entry, a dropped middle entry, a non-entry in the log. A fresh process
loads the JSON, scans and must salvage to the longest verified prefix (the
position of the FIRST damage, with everything after it quarantined even when
later entries look valid on their own), remove exactly the corrupt suffix last,
and report receipt fields that match formulas written here from the contract.
The same process then re-appends the lost operations through the WAL and the final
log must be bit-identical to the log that was never damaged, at every damage
position.

A scan whose loss exceeds max_loss, a log outside the closed scan domain, a
malformed request and divergent quarantine sinks are refused IN THE FRESH PROCESS
with the declared class and mapped code and leave the log unchanged. One-edit
source mutants run in the fresh process; each must be killed by a semantic
deviation (a crash is an error, never a kill). Test-only: no production change.
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
from store import corruption, wal  # noqa: E402

SOURCE = (ROOT / "store" / "corruption.py").read_text()
CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "corruption.yaml").read_text())["contract"]
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


def build_log():
    engine = wal.WalEngine(wal.canonical_payload)
    log = []
    for request in REQUESTS:
        engine.append(log, copy.deepcopy(request))
    return log


REF_LOG = build_log()
N = len(REF_LOG)


def scan_id(verdict, head, count, lost, token):
    """Contract formula for the receipt id."""
    text = f"{verdict}\n{head}\n{count}\n{lost}\n{token if token is not None else '-'}"
    return "crp1:" + hashlib.sha256(text.encode()).hexdigest()


def damage(j):
    """name -> (damaged log, index of the first damaged entry)."""
    flipped = copy.deepcopy(REF_LOG)
    flipped[j]["payload"]["record"]["digest"] = "pdv1:" + "0" * 64
    swapped = copy.deepcopy(REF_LOG)
    out = {
        "payload-flipped": (flipped, j),
        "duplicated-entry": (
            [*REF_LOG[: j + 1], copy.deepcopy(REF_LOG[j]), *REF_LOG[j + 1 :]],
            j + 1,
        ),
        "non-entry": ([*REF_LOG[:j], "garbage", *REF_LOG[j:]], j),
    }
    if j + 1 < N:  # dropping the LAST entry leaves a shorter valid log: not corruption
        out["dropped-entry"] = ([*REF_LOG[:j], *REF_LOG[j + 1 :]], j)
        swapped[j], swapped[j + 1] = swapped[j + 1], swapped[j]
        out["swapped-entries"] = (swapped, j)
    return out


CHILD = r"""
import json, sys, types
root = sys.argv[1]
sys.path.insert(0, root)
payload = json.load(sys.stdin)
if payload.get("source"):
    mod = types.ModuleType("mutant_corruption")
    mod.__file__ = root + "/store/corruption.py"
    exec(compile(payload["source"], mod.__file__, "exec"), mod.__dict__)
else:
    from store import corruption as mod
from store import wal


def raising(suffix):
    raise RuntimeError("boom")


sink = {
    "honest": mod.quarantine_suffix,
    "wrong": lambda suffix: "qrn1:" + "0" * 64,
    "raise": raising,
    "nonstr": lambda suffix: 7,
}[payload["sink"]]
engine = mod.CorruptionEngine(sink)
log = payload["log"]
out = {}
try:
    out["receipt"] = engine.scan(log, payload["request"])
    out["after_scan"] = json.loads(json.dumps(log))
    wal_engine = wal.WalEngine(wal.canonical_payload)
    for request in payload["continue"]:
        wal_engine.append(log, request)
    out["ok"] = True
except mod.CorruptionError as err:
    out.update(ok=False, failure_class=err.failure_class, code=err.code)
except Exception as exc:
    out.update(ok=False, crash=type(exc).__name__ + ": " + str(exc))
out["log"] = log
print(json.dumps(out))
"""


def child_run(log, max_loss, cont=(), source=None, sink="honest", request=None):
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT)],
        input=json.dumps(
            {
                "log": log,
                "request": {"max_loss": max_loss} if request is None else request,
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


CASES = [(j, name) for j in range(N) for name in sorted(damage(j))]


@pytest.mark.parametrize("j,name", CASES)
def test_damaged_log_is_salvaged_to_the_first_damage_and_converges(j, name):
    raw, first = damage(j)[name]
    log = jround(raw)
    j, lost = first, len(log) - first
    child = child_run(log, lost, cont=REQUESTS[j:])
    assert child["ok"], child
    receipt = child["receipt"]
    assert child["after_scan"] == jround(REF_LOG[:j]), "exactly the corrupt suffix is removed"
    head = REF_LOG[j - 1]["entry_id"] if j else wal.GENESIS
    token = corruption.quarantine_suffix(jround(log[j:]))
    assert receipt == {
        "scan_id": scan_id("salvaged", head, j, lost, token),
        "verdict": "salvaged",
        "verified_head": head,
        "verified_count": j,
        "quarantined_count": lost,
        "quarantine_token": token,
    }
    assert child["log"] == jround(REF_LOG), "re-appended log must equal the undamaged log"


def test_quarantine_token_depends_on_the_whole_suffix():
    tokens = {
        child_run(jround(damage(2)[name][0]), 99)["receipt"]["quarantine_token"]
        for name in sorted(damage(2))
    }
    assert len(tokens) == len(damage(2))
    one = child_run(jround(damage(2)["payload-flipped"][0]), 99)["receipt"]["quarantine_token"]
    two = child_run(jround(damage(3)["payload-flipped"][0]), 99)["receipt"]["quarantine_token"]
    assert one != two


@pytest.mark.parametrize("n", range(N + 1))
def test_clean_log_scans_clean_with_no_token(n):
    log = jround(REF_LOG[:n])
    child = child_run(log, 0, cont=REQUESTS[n:])
    assert child["ok"], child
    head = REF_LOG[n - 1]["entry_id"] if n else wal.GENESIS
    assert child["receipt"] == {
        "scan_id": scan_id("clean", head, n, 0, None),
        "verdict": "clean",
        "verified_head": head,
        "verified_count": n,
        "quarantined_count": 0,
        "quarantine_token": None,
    }
    assert child["after_scan"] == log and child["log"] == jround(REF_LOG)


def test_loss_above_max_loss_is_refused_and_nothing_is_removed():
    for j in (0, 2, 4):
        log = jround(damage(j)["payload-flipped"][0])
        lost = len(log) - j
        for allowed in (0, lost - 1):
            child = child_run(log, allowed)
            assert child["ok"] is False and "crash" not in child, child
            assert child["failure_class"] == "excessive_loss"
            assert child["code"] == MAPPING["excessive_loss"]
            assert child["log"] == log
        exact = child_run(log, lost)
        assert exact["ok"] and exact["receipt"]["quarantined_count"] == lost


def test_malformed_requests_are_refused_after_restart():
    log = jround(REF_LOG[:3])
    bad = {
        "negative": {"max_loss": -1},
        "float": {"max_loss": 1.0},
        "bool": {"max_loss": True},
        "string": {"max_loss": "1"},
        "extra_member": {"max_loss": 1, "z": 1},
        "missing_member": {},
        "wrong_member": {"loss": 1},
    }
    for name, request in bad.items():
        child = child_run(log, 0, request=request)
        assert child["ok"] is False and "crash" not in child, (name, child)
        assert child["failure_class"] == "malformed_corruption_record", name
        assert child["code"] == MAPPING["malformed_corruption_record"], name
        assert child["log"] == log, name


def _deep(levels):
    node = []
    for _ in range(levels):
        node = [node]
    return node


def test_logs_outside_the_scan_domain_are_refused_after_restart():
    base = jround(REF_LOG[:2])
    cases = {
        "float_entry": [*base, {"x": 1.5}],
        "lone_surrogate": [*base, {"x": "\ud800"}],
        "too_deep": [*base, {"x": _deep(corruption.MAX_DEPTH + 2)}],
    }
    for name, log in cases.items():
        child = subprocess.run(
            [sys.executable, "-c", CHILD, str(ROOT)],
            input=json.dumps(
                {
                    "log": log,
                    "request": {"max_loss": 9},
                    "continue": [],
                    "source": None,
                    "sink": "honest",
                }
            ),
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
        )
        assert child.returncode == 0, (name, child.stderr[-300:])
        out = json.loads(child.stdout)
        assert out["ok"] is False and "crash" not in out, (name, out)
        assert out["failure_class"] == "malformed_corruption_record", name
        assert out["code"] == MAPPING["malformed_corruption_record"], name
        assert out["log"] == log, name


@pytest.mark.parametrize("sink", ["wrong", "raise", "nonstr"])
def test_divergent_sink_refused_and_the_suffix_is_not_removed(sink):
    log = jround(damage(3)["payload-flipped"][0])
    child = child_run(log, 99, sink=sink)
    assert child["ok"] is False and "crash" not in child, child
    assert child["failure_class"] == "divergent_quarantine"
    assert child["code"] == MAPPING["divergent_quarantine"]
    assert child["log"] == log


def test_a_clean_log_never_calls_the_sink():
    child = child_run(jround(REF_LOG), 0, sink="raise")
    assert child["ok"] and child["receipt"]["verdict"] == "clean"


def test_scan_is_idempotent_across_a_second_restart():
    log = jround(damage(3)["dropped-entry"][0])
    first = child_run(log, 99)
    again = child_run(first["log"], 0)
    assert again["ok"] and again["receipt"]["verdict"] == "clean"
    assert again["receipt"]["verified_head"] == first["receipt"]["verified_head"]
    assert again["log"] == first["log"]


# -- one-edit mutants of the runtime, executed in the fresh process ----------------


def _once(old, new):
    assert SOURCE.count(old) == 1, old
    return SOURCE.replace(old, new)


EDITS = {
    "suffix-not-removed": _once("        del log[count:]", "        pass"),
    "loss-bound-dropped": _once("        if lost > max_loss:", "        if False:"),
    "clean-not-detected": _once("        if lost == 0:", "        if lost == 0 and False:"),
    "prefix-one-entry-short": _once(
        "        return good, self._prefix_head(frozen, good)",
        "        return max(good - 1, 0), self._prefix_head(frozen, max(good - 1, 0))",
    ),
    "quarantine-unbound-to-suffix": _once(
        "            if token != quarantine_suffix(frozen_suffix):", "            if False:"
    ),
    "scan-id-ignores-loss": _once(
        'f"{verdict}\\n{head}\\n{count}\\n{lost}\\n"', 'f"{verdict}\\n{head}\\n{count}\\n"'
    ),
}
EXPECTED_KILL = {
    "suffix-not-removed": "salvage",
    "loss-bound-dropped": "excessive",
    "clean-not-detected": "clean",
    "prefix-one-entry-short": "salvage",
    "quarantine-unbound-to-suffix": "unbound-sink",
    "scan-id-ignores-loss": "salvage",
}


def _deviations(source):
    j = 3
    log = jround(damage(j)["payload-flipped"][0])
    lost = len(log) - j
    token = corruption.quarantine_suffix(jround(log[j:]))
    head = REF_LOG[j - 1]["entry_id"]
    runs = {
        "salvage": child_run(log, lost, source=source),
        "excessive": child_run(log, lost - 1, source=source),
        "clean": child_run(jround(REF_LOG), 0, source=source),
        "unbound-sink": child_run(log, lost, source=source, sink="wrong"),
    }
    for name, run in runs.items():
        assert "crash" not in run, f"{name}: mutant crashed instead of deviating: {run}"
    found = set()
    s = runs["salvage"]
    if not (
        s["ok"]
        and s["after_scan"] == jround(REF_LOG[:j])
        and s["receipt"]["verified_count"] == j
        and s["receipt"]["scan_id"] == scan_id("salvaged", head, j, lost, token)
    ):
        found.add("salvage")
    e = runs["excessive"]
    if not (e["ok"] is False and e.get("failure_class") == "excessive_loss" and e["log"] == log):
        found.add("excessive")
    c = runs["clean"]
    if not (
        c["ok"] and c["receipt"]["verdict"] == "clean" and c["receipt"]["quarantine_token"] is None
    ):
        found.add("clean")
    u = runs["unbound-sink"]
    if not (u["ok"] is False and u.get("failure_class") == "divergent_quarantine"):
        found.add("unbound-sink")
    return found


def test_unmutated_runtime_has_no_deviation():
    assert _deviations(None) == set()


@pytest.mark.parametrize("label", sorted(EDITS))
def test_runtime_mutant_is_killed_semantically(label):
    killed_by = _deviations(EDITS[label])
    assert EXPECTED_KILL[label] in killed_by, (label, killed_by)
