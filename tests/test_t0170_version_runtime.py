"""T0170 production graph-version runtime conformance."""

from __future__ import annotations

import copy

import pytest

from graph.version_store import VersionError, VersionStore, version_id


def D(n):
    return "gdv1:" + format(n, "064x")


def T(n):
    return f"2026-01-{n:02d}T00:00:00Z"


def chain(s, n):
    out = []
    parents = []
    for i in range(1, n + 1):
        r = s.make_record(parents, D(i), T(i), f"v{i}")
        out.append(s.insert(r))
        parents = [r["version_id"]]
    return out


def test_linear_branch_merge_and_canonical_parents():
    s = VersionStore()
    root = s.insert(s.make_record([], D(1), T(1), "root"))
    left = s.insert(s.make_record([root["version_id"]], D(2), T(2), "left"))
    right = s.insert(s.make_record([root["version_id"]], D(3), T(2), "right"))
    supplied = [right["version_id"], left["version_id"], right["version_id"]]
    r = s.make_record(supplied, D(4), T(4), "merge")
    got = s.insert(r)
    assert got["parent_ids"] == sorted(set(supplied))
    assert got["version_id"] == version_id(supplied, D(4))
    assert s.records[got["version_id"]] == got


def test_returned_and_exposed_records_are_copies():
    s = VersionStore()
    r = s.insert(s.make_record([], D(1), T(1), "root"))
    r["label"] = "tamper"
    view = s.records
    view[next(iter(view))]["label"] = "tamper"
    assert next(iter(s.records.values()))["label"] == "root"


def test_failures_and_rollback():
    s = VersionStore()
    root = s.insert(s.make_record([], D(1), T(2), "root"))
    before = copy.deepcopy((s.records, s.root_id))
    attempts = [
        ("root_violation", s.make_record([], D(2), T(3), "second")),
        ("unknown_parent", s.make_record(["gv1:" + "9" * 64], D(2), T(3), "orphan")),
        ("nonmonotonic_version", s.make_record([root["version_id"]], D(2), T(1), "early")),
    ]
    for failure, r in attempts:
        with pytest.raises(VersionError) as exc:
            s.insert(r)
        assert exc.value.failure_class == failure
        assert (s.records, s.root_id) == before


def test_malformed_totality():
    for bad in (None, True, 0, [], {}, {"x": 1}):
        with pytest.raises(VersionError) as exc:
            VersionStore().insert(bad)
        assert exc.value.failure_class == "malformed_version_record"


def test_metadata_conflict_and_dedup():
    s = VersionStore()
    r = s.insert(s.make_record([], D(1), T(1), "root"))
    assert s.insert(copy.deepcopy(r)) == r
    with pytest.raises(VersionError) as exc:
        s.insert(s.make_record([], D(1), T(1), "renamed"))
    assert exc.value.failure_class == "conflicting_version"
    assert next(iter(s.records.values()))["label"] == "root"


def test_atomic_out_of_order_merge():
    src = VersionStore()
    chain(src, 3)
    dst = VersionStore()
    dst.merge(src)
    assert dst.canonical_view() == src.canonical_view()
    bad = VersionStore()
    bad._records = copy.deepcopy(src._records)
    victim = bad.canonical_view()[-1]
    bad._records[victim]["label"] = ""
    before = copy.deepcopy((dst.records, dst.root_id))
    with pytest.raises(VersionError):
        dst.merge(bad)
    assert (dst.records, dst.root_id) == before


class RaisingDict(dict):
    def __deepcopy__(self, memo):
        raise RuntimeError("hostile deepcopy")


class RaisingList(list):
    def __iter__(self):
        raise RuntimeError("hostile iteration")


def test_public_boundaries_are_typed_total_over_hostile_containers():
    store = VersionStore()
    before = (store.records, store.root_id)
    cases = [
        lambda: store.insert(RaisingDict()),
        lambda: store.make_record(RaisingList(), D(1), T(1), "x"),
        lambda: store.make_record([[]], D(1), T(1), "x"),
        lambda: version_id(RaisingList(), D(1)),
        lambda: version_id([[]], D(1)),
    ]
    for call in cases:
        with pytest.raises(VersionError) as exc:
            call()
        assert exc.value.failure_class == "malformed_version_record"
        assert (store.records, store.root_id) == before
