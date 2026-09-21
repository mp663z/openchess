"""T0169 permanent red battery for graph-version implementations.

Each mutant violates one behavior pinned by the T0167 contract and T0168
fixture. The shared probe must reject every mutant and accept the honest store.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.test_t0167_version_contract import (  # noqa: E402
    D1,
    D2,
    D3,
    T1,
    T2,
    T3,
    VersionError,
    VersionStore,
    _real_hasher,
)


def _probe(cls):
    problems = []

    def check(name, fn):
        try:
            if not fn():
                problems.append(name)
        except BaseException as exc:
            problems.append(f"{name}: {type(exc).__name__}")

    check("parent-canonicalization", lambda: _parent_canonical(cls))
    check("rollback", lambda: _rollback(cls))
    check("metadata-conflict", lambda: _metadata_conflict(cls))
    check("unknown-parent", lambda: _unknown_parent(cls))
    check("nonmonotonic", lambda: _nonmonotonic(cls))
    return problems


def _base(cls):
    s = cls()
    root = s.insert(s.make_record([], D1, T1, "root"))
    return s, root


def _parent_canonical(cls):
    s, root = _base(cls)
    left = s.insert(s.make_record([root["version_id"]], D2, T2, "left"))
    supplied = [left["version_id"], root["version_id"], left["version_id"]]
    rec = s.make_record(supplied, D3, T3, "child")
    expected = sorted(set(supplied))
    out = s.insert(rec)
    return (
        out["parent_ids"] == expected
        and s.records[out["version_id"]]["parent_ids"] == expected
        and out["version_id"] == _real_hasher(expected, D3)
    )


def _rollback(cls):
    s, root = _base(cls)
    before = copy.deepcopy((s.records, s.root_id))
    ghost = VersionStore().make_record([], D3, T1, "ghost")
    try:
        s.insert(s.make_record([ghost["version_id"]], D2, T2, "bad"))
    except VersionError:
        return (s.records, s.root_id) == before
    return False


def _metadata_conflict(cls):
    s, _ = _base(cls)
    before = copy.deepcopy(s.records)
    try:
        s.insert(s.make_record([], D1, T1, "renamed"))
    except VersionError as exc:
        return exc.failure_class == "conflicting_version" and s.records == before
    return False


def _unknown_parent(cls):
    s, _ = _base(cls)
    ghost = VersionStore().make_record([], D3, T1, "ghost")
    try:
        s.insert(s.make_record([ghost["version_id"]], D2, T2, "bad"))
    except VersionError as exc:
        return exc.failure_class == "unknown_parent"
    return False


def _nonmonotonic(cls):
    s = cls()
    root = s.insert(s.make_record([], D1, T2, "root"))
    try:
        s.insert(s.make_record([root["version_id"]], D2, T1, "early"))
    except VersionError as exc:
        return exc.failure_class == "nonmonotonic_version"
    return False


class StoresCallerParents(VersionStore):
    def make_record(self, parent_ids, graph_digest, created_at, label):
        record = super().make_record(parent_ids, graph_digest, created_at, label)
        if isinstance(parent_ids, list):
            record["parent_ids"] = copy.deepcopy(parent_ids)
        return record

    def insert(self, record):
        supplied = copy.deepcopy(record.get("parent_ids")) if isinstance(record, dict) else None
        canonical = copy.deepcopy(record)
        if isinstance(canonical, dict) and isinstance(supplied, list):
            canonical["parent_ids"] = sorted(set(supplied))
        out = super().insert(canonical)
        if isinstance(supplied, list):
            out["parent_ids"] = supplied
            self.records[out["version_id"]]["parent_ids"] = supplied
        return out


class PartialCommitOnFailure(VersionStore):
    def insert(self, record):
        try:
            return super().insert(record)
        except VersionError:
            if isinstance(record, dict):
                self.records["gv1:" + "e" * 64] = copy.deepcopy(record)
            raise


class KeepsFirstMetadata(VersionStore):
    def insert(self, record):
        if isinstance(record, dict) and record.get("version_id") in self.records:
            return self.records[record["version_id"]]
        return super().insert(record)


class AcceptsUnknownParent(VersionStore):
    def insert(self, record):
        if isinstance(record, dict) and record.get("parent_ids"):
            saved = copy.deepcopy(self.records)
            for p in record["parent_ids"]:
                if p not in self.records:
                    self.records[p] = {"created_at": T1}
            try:
                return super().insert(record)
            finally:
                for p in list(self.records):
                    if p not in saved and p != record.get("version_id"):
                        self.records.pop(p, None)
        return super().insert(record)


class AcceptsTimeTravel(VersionStore):
    def insert(self, record):
        if isinstance(record, dict) and record.get("parent_ids"):
            for p in record["parent_ids"]:
                if p in self.records:
                    self.records[p]["created_at"] = "0001-01-01T00:00:00Z"
        return super().insert(record)


MUTANTS = {
    "stores-caller-parent-order": StoresCallerParents,
    "partial-commit": PartialCommitOnFailure,
    "metadata-last-wins": KeepsFirstMetadata,
    "unknown-parent-accepted": AcceptsUnknownParent,
    "time-travel-accepted": AcceptsTimeTravel,
}


def test_honest_store_passes_probe():
    assert _probe(VersionStore) == []


def test_every_mutant_is_red():
    for name, cls in MUTANTS.items():
        assert _probe(cls), f"{name}: mutant passed"
