"""T0173 integration/restart battery for the production graph version store."""

from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from graph.version_store import VersionError, VersionStore

ROOT = Path(__file__).resolve().parents[1]
TS = "2026-09-21T12:00:00Z"

CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from graph.version_store import VersionError, VersionStore
p=json.load(sys.stdin); s=VersionStore()
try:
    pending=p["records"]
    while pending:
        ready=[r for r in pending if all(x in s.records for x in r["parent_ids"])]
        if not ready: s.insert(pending[0])
        for r in ready: s.insert(r); pending.remove(r)
    for item in p.get("append", []):
        r=s.make_record(item["parents"], item["digest"], item["created_at"], item["label"])
        s.insert(r)
    print(json.dumps({"ok":True,"records":s.records,"root_id":s.root_id,
                      "canonical":s.canonical_view()}, sort_keys=True))
except VersionError as e:
    print(json.dumps({"ok":False,"failure_class":e.failure_class,"code":e.code}, sort_keys=True))
except BaseException as e:
    print(json.dumps({"ok":False,"crash":type(e).__name__}, sort_keys=True))
"""


def _digest(n: int) -> str:
    return "gdv1:" + f"{n:064x}"


def _fixture() -> VersionStore:
    s = VersionStore()
    root = s.make_record([], _digest(1), TS, "root")
    s.insert(root)
    left = s.make_record([root["version_id"]], _digest(2), TS, "left")
    right = s.make_record([root["version_id"]], _digest(3), TS, "right")
    s.insert(left)
    s.insert(right)
    merge = s.make_record([right["version_id"], left["version_id"]], _digest(4), TS, "merge")
    s.insert(merge)
    return s


def _child(records, append=()):
    run = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT)],
        input=json.dumps({"records": records, "append": list(append)}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_fresh_process_restart_is_bit_identical_from_shuffled_snapshot():
    s = _fixture()
    records = list(s.records.values())
    random.Random(173).shuffle(records)
    out = _child(records)
    assert out["ok"]
    assert out["records"] == s.records
    assert out["root_id"] == s.root_id
    assert out["canonical"] == s.canonical_view()


def test_restart_continuation_matches_uninterrupted_multi_parent_history():
    base = _fixture()
    tip = base.canonical_view()[-1]
    additions = [
        {"parents": [tip], "digest": _digest(5), "created_at": TS, "label": "continued"},
    ]
    restarted = _child(list(base.records.values()), additions)
    assert restarted["ok"]
    direct = _fixture()
    r = direct.make_record([tip], _digest(5), TS, "continued")
    direct.insert(r)
    assert restarted["records"] == direct.records
    assert restarted["canonical"] == direct.canonical_view()
    assert restarted["root_id"] == direct.root_id


@pytest.mark.parametrize(
    "mutation, failure",
    [
        (lambda rs: rs + [{**rs[0], "version_id": "gv1:" + "0" * 64}], "malformed_version_record"),
        (
            lambda rs: [{**rs[1], "parent_ids": ["gv1:" + "f" * 64]}] + rs[2:],
            "malformed_version_record",
        ),
        (lambda rs: [{**rs[0], "label": "bad\nlabel"}] + rs[1:], "malformed_version_record"),
        (
            lambda rs: [rs[0], {**rs[1], "created_at": "2026-09-20T12:00:00Z"}] + rs[2:],
            "nonmonotonic_version",
        ),
    ],
)
def test_corrupt_persisted_snapshot_rejected_typed_in_fresh_process(mutation, failure):
    records = list(_fixture().records.values())
    out = _child(mutation(copy.deepcopy(records)))
    assert not out["ok"]
    assert "crash" not in out
    assert out["failure_class"] == failure
    assert type(out["code"]) is str and out["code"]


def test_rejected_pre_restart_insert_leaves_serialized_history_unchanged():
    s = _fixture()
    before = s.records
    bad = copy.deepcopy(next(iter(before.values())))
    bad["label"] = "bad\nlabel"
    with pytest.raises(VersionError):
        s.insert(bad)
    assert s.records == before
    out = _child(list(s.records.values()))
    assert out["ok"] and out["records"] == before


def test_snapshot_and_child_outputs_do_not_alias_live_store():
    s = _fixture()
    snapshot = s.records
    first = next(iter(snapshot))
    snapshot[first]["label"] = "caller mutation"
    assert s.records[first]["label"] != "caller mutation"
    out = _child(list(s.records.values()))
    out["records"][first]["label"] = "output mutation"
    assert s.records[first]["label"] != "output mutation"
