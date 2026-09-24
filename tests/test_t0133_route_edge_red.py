"""T0133: permanent red tests for concrete route-edge defects."""

from __future__ import annotations

import contextlib
import copy

from graph.route_edge import EdgeError, EdgeTable
from graph.route_edge import load_docs as _docs
from tests.test_t0132_route_edge_fixture import CASES, _StubTable


def _build_table(inserts):
    table = EdgeTable(_docs())
    for edge in inserts:
        table.insert(*edge)
    return table


def _case(section, name):
    return next(case for case in CASES[section] if case["name"] == name)


class AllowsConflictingTarget(EdgeTable):
    def insert(self, variant_id, move, from_fen, to_fen):
        try:
            return super().insert(variant_id, move, from_fen, to_fen)
        except EdgeError as exc:
            if exc.failure_class != "conflicting_edge":
                raise
            clean = EdgeTable(_docs(), self.digest_fn)
            rec = clean.insert(variant_id, move, from_fen, to_fen)
            key = (self.digest_fn(variant_id, rec["from_snapshot_fen"]), move)
            self.buckets.setdefault(key, []).append(rec)
            return rec


class PartialMergeCommit(EdgeTable):
    def merge(self, other):
        for record in other.records():
            self.insert(
                record["variant"],
                record["move"],
                record["from_snapshot_fen"],
                record["to_snapshot_fen"],
            )
        return self


class KeepsDuplicateEdge(EdgeTable):
    def insert(self, variant_id, move, from_fen, to_fen):
        rec = super().insert(variant_id, move, from_fen, to_fen)
        key = (self.digest_fn(variant_id, rec["from_snapshot_fen"]), move)
        self.buckets[key].append(copy.deepcopy(rec))
        return rec


def _probe_conflict(table_cls):
    case = _case("rollback", "rejected-conflicting-insert-then-valid-insert")
    table = table_cls(_docs())
    for edge in case["setup"]:
        table.insert(*edge)
    before = copy.deepcopy(table.buckets)
    rejected = case["rejected"]
    try:
        table.insert(
            rejected["variant"],
            rejected["move"],
            rejected["from_snapshot_fen"],
            rejected["to_snapshot_fen"],
        )
    except EdgeError as exc:
        return exc.failure_class == case["expect_failure"] and table.buckets == before
    return False


def _probe_atomic_merge(table_cls):
    case = _case("rollback", "rejected-atomic-merge-then-valid-merge")
    table = table_cls(_docs())
    for edge in case["setup"]:
        table.insert(*edge)
    before = copy.deepcopy(table.buckets)
    try:
        table.merge(_StubTable(copy.deepcopy(case["rejected_records"])))
    except EdgeError as exc:
        return exc.failure_class == case["expect_failure"] and table.buckets == before
    return False


def _probe_idempotence(table_cls):
    case = _case("happy", "insert-same-edge-twice-returns-existing")
    table = table_cls(_docs())
    first = table.insert(*case["inserts"][0])
    second = table.insert(*case["inserts"][1])
    return first is second and [list(row) for row in table.serialize()] == case["expect_serialized"]


def test_honest_route_edge_table_passes_all_probes():
    assert _probe_conflict(EdgeTable)
    assert _probe_atomic_merge(EdgeTable)
    assert _probe_idempotence(EdgeTable)


def test_conflicting_target_acceptance_mutant_is_red():
    assert not _probe_conflict(AllowsConflictingTarget)


def test_partial_merge_commit_mutant_is_red_with_nonvacuous_witness():
    case = _case("rollback", "rejected-atomic-merge-then-valid-merge")
    mutant = PartialMergeCommit(_docs())
    for edge in case["setup"]:
        mutant.insert(*edge)
    before = copy.deepcopy(mutant.buckets)
    with contextlib.suppress(EdgeError):
        mutant.merge(_StubTable(copy.deepcopy(case["rejected_records"])))
    assert mutant.buckets != before, "mutant must realize partial commit"
    assert not _probe_atomic_merge(PartialMergeCommit)


def test_duplicate_storage_mutant_is_red():
    assert not _probe_idempotence(KeepsDuplicateEdge)


def test_rejected_conflict_does_not_poison_following_valid_insert():
    case = _case("rollback", "rejected-conflicting-insert-then-valid-insert")
    table = _build_table(case["setup"])
    before = copy.deepcopy(table.buckets)
    rejected = case["rejected"]
    try:
        table.insert(
            rejected["variant"],
            rejected["move"],
            rejected["from_snapshot_fen"],
            rejected["to_snapshot_fen"],
        )
    except EdgeError as exc:
        assert exc.failure_class == case["expect_failure"]
    else:
        raise AssertionError("conflicting insert was accepted")
    assert table.buckets == before
    for edge in case["then"]:
        table.insert(*edge)
    assert [list(row) for row in table.serialize()] == case["expect_serialized"]
