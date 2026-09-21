"""T0174 behavior-neutral diagnostics for graph version operations."""

from __future__ import annotations

import copy
import hashlib

import pytest

from graph.version_instrument import VersionTracer
from graph.version_store import VersionError, VersionStore

TS = "2026-09-21T12:00:00Z"


def _digest(n):
    return "gdv1:" + f"{n:064x}"


def _record(store, parents, n, label):
    return store.make_record(parents, _digest(n), TS, label)


def test_accept_trace_is_behavior_neutral_and_diagnostic():
    traced_store = VersionStore()
    tracer = VersionTracer(traced_store)
    bare = VersionStore()
    root = _record(traced_store, [], 1, "root")
    bare_root = _record(bare, [], 1, "root")
    assert tracer.insert(root) == bare.insert(bare_root)
    child = _record(traced_store, [root["version_id"]], 2, "child")
    bare_child = _record(bare, [bare_root["version_id"]], 2, "child")
    assert tracer.insert(child) == bare.insert(bare_child)
    assert traced_store.records == bare.records
    assert [(r["outcome"], r["versions_before"], r["versions_after"]) for r in tracer.records] == [
        ("accept", 0, 1),
        ("accept", 1, 2),
    ]
    assert tracer.records[1]["root_before"] == root["version_id"]
    assert tracer.records[1]["root_after"] == root["version_id"]


def test_reject_trace_maps_failure_and_preserves_rollback():
    tracer = VersionTracer()
    root = _record(tracer.store, [], 1, "root")
    tracer.insert(root)
    before = tracer.store.records
    bad = copy.deepcopy(root)
    bad["label"] = "bad\nlabel"
    with pytest.raises(VersionError) as caught:
        tracer.insert(bad)
    rec = tracer.records[-1]
    assert rec["outcome"] == "reject"
    assert rec["failure_class"] == caught.value.failure_class == "malformed_version_record"
    assert rec["code"] == caught.value.code
    assert rec["versions_before"] == 1
    assert tracer.store.records == before


def test_merge_trace_reports_delta_and_returns_exact_store():
    left = VersionStore()
    root = _record(left, [], 1, "root")
    left.insert(root)
    right = VersionStore()
    right.insert(root)
    child = _record(right, [root["version_id"]], 2, "child")
    right.insert(child)
    tracer = VersionTracer(left)
    assert tracer.merge(right) is left
    rec = tracer.records[-1]
    assert rec["operation"] == "merge" and rec["outcome"] == "accept"
    assert (rec["versions_before"], rec["versions_after"]) == (1, 2)
    assert left.records == right.records


def test_crash_records_type_and_reraises_same_object():
    sentinel = KeyboardInterrupt("stop")

    store = VersionStore()

    def crash(record):
        raise sentinel

    tracer = VersionTracer(store, insert_fn=crash)
    with pytest.raises(KeyboardInterrupt) as caught:
        tracer.insert({})
    assert caught.value is sentinel
    assert tracer.records[-1]["outcome"] == "crash"
    assert tracer.records[-1]["error_type"] == "KeyboardInterrupt"


def test_argument_snapshot_never_dispatches_copy_hooks():
    class Bomb(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("copy hook")

    tracer = VersionTracer()
    with pytest.raises(VersionError):
        tracer.insert(Bomb())
    assert tracer.records[-1]["argument"] == {"__opaque__": "Bomb"}


def test_records_are_append_only_isolated_and_jsonl_deterministic():
    def session():
        t = VersionTracer()
        root = _record(t.store, [], 1, "root")
        t.insert(root)
        with pytest.raises(VersionError):
            t.insert({})
        return t

    first = session()
    second = session()
    assert first.to_jsonl() == second.to_jsonl()
    assert (
        hashlib.sha256(first.to_jsonl().encode()).hexdigest()
        == hashlib.sha256(second.to_jsonl().encode()).hexdigest()
    )
    view = first.records
    view[0]["outcome"] = "tampered"
    assert first.records[0]["outcome"] == "accept"
    assert [r["seq"] for r in first.records] == [0, 1]
