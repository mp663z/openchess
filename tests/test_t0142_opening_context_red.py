"""T0142: permanent red battery for opening-context contract defects.

The battery was authored against the T0140 reference oracle, then rebound by
T0143 to the shipped runtime below. The T0141 fixture remains the pinned data
source; every mutant now subclasses the production ContextTable.
"""

from __future__ import annotations

import contextlib
import copy

from graph.opening_context import ContextError, ContextTable
from tests.test_t0122_transposition_node_contract import _docs as _node_docs
from tests.test_t0122_transposition_node_contract import _table as _node_table
from tests.test_t0140_opening_context_contract import (
    PETROFF_PATH,
    ZUKERTORT_TRANSPOSITION_PATH,
    _apply_path_from_start,
)
from tests.test_t0141_opening_context_fixture import CASES


def _docs():
    return None


def _case(section, name):
    return next(case for case in CASES[section] if case["name"] == name)


class FirstPrefixWins(ContextTable):
    def insert(self, variant_id, path):
        matching = [
            entry for entry in self.reg["entries"] if path[: len(entry["moves"])] == entry["moves"]
        ]
        original = self.reg["entries"]
        if matching:
            self.reg["entries"] = [min(matching, key=lambda entry: len(entry["moves"]))]
        try:
            return super().insert(variant_id, path)
        finally:
            self.reg["entries"] = original


class UnmatchedFallsBackToFirst(ContextTable):
    def insert(self, variant_id, path):
        rec = super().insert(variant_id, path)
        if rec["opening_code"] == "-":
            first = self.reg["entries"][0]
            rec["opening_code"] = first["code"]
            rec["opening_name"] = first["name"]
        return rec


class PartialCommitOnReject(ContextTable):
    def insert(self, variant_id, path):
        try:
            return super().insert(variant_id, path)
        except ContextError:
            self.map[("standard", ("a2a3",))] = {
                "variant": "standard",
                "path_moves": ["a2a3"],
                "opening_code": "-",
                "opening_name": "-",
            }
            raise


class NodeIdentityKeyed(ContextTable):
    """Forbidden mutant: key contexts by a real canonical T0122 node."""

    def __init__(self, docs):
        super().__init__(docs)
        self.node_table = _node_table()
        _nc, self.node_vc, _dc, _epc, self.node_fc = _node_docs()
        self.by_node = {}

    def insert(self, variant_id, path):
        fen = _apply_path_from_start(self.node_vc, self.node_fc, path)
        node = self.node_table.insert(variant_id, fen)
        node_key = (node["variant"], node["digest"], node["snapshot_fen"])
        existing = self.by_node.get(node_key)
        if existing is not None:
            return existing
        record = super().insert(variant_id, path)
        self.by_node[node_key] = record
        return record


def _resolve_probe(table_cls):
    checks = []
    for section in ("happy", "boundary"):
        for case in CASES[section]:
            table = table_cls(_docs())
            record = table.insert(case["variant"], case["path"])
            checks.append(
                (record["opening_code"], record["opening_name"])
                == (case["expect_code"], case["expect_name"])
            )
    return all(checks)


def _rollback_probe(table_cls):
    case = _case("rollback", "malformed-after-sicilian")
    table = table_cls(_docs())
    for path in case["setup_paths"]:
        table.insert("standard", path)
    before = copy.deepcopy(table.map)
    try:
        table.insert(case["variant"], case["rejected_path"])
    except ContextError as exc:
        return exc.failure_class == case["expect_failure"] and table.map == before
    return False


def _transposition_nodes():
    _nc, vc, _dc, _epc, fc = _node_docs()
    fen_a = _apply_path_from_start(vc, fc, PETROFF_PATH)
    fen_b = _apply_path_from_start(vc, fc, ZUKERTORT_TRANSPOSITION_PATH)
    nodes = _node_table()
    a = nodes.insert("standard", fen_a)
    b = nodes.insert("standard", fen_b.replace(" 0 1", " 0 9"))
    return a, b


def _path_identity_probe(table_cls):
    node_a, node_b = _transposition_nodes()
    assert node_a is node_b  # premise is load-bearing and independently real
    table = table_cls(_docs())
    a = table.insert("standard", PETROFF_PATH)
    b = table.insert("standard", ZUKERTORT_TRANSPOSITION_PATH)
    return a["opening_code"] == "C20" and b["opening_code"] == "A04"


def test_honest_context_table_passes_all_probes():
    assert _resolve_probe(ContextTable)
    assert _rollback_probe(ContextTable)
    assert _path_identity_probe(ContextTable)


def test_first_prefix_instead_of_longest_prefix_is_red():
    assert not _resolve_probe(FirstPrefixWins)


def test_unmatched_fallback_instead_of_none_sentinel_is_red():
    assert not _resolve_probe(UnmatchedFallsBackToFirst)


def test_partial_commit_on_rejection_is_red_with_nonvacuous_witness():
    case = _case("rollback", "malformed-after-sicilian")
    mutant = PartialCommitOnReject(_docs())
    for path in case["setup_paths"]:
        mutant.insert("standard", path)
    before = copy.deepcopy(mutant.map)
    with contextlib.suppress(ContextError):
        mutant.insert(case["variant"], case["rejected_path"])
    assert mutant.map != before, "mutant must realize rejection corruption"
    assert not _rollback_probe(PartialCommitOnReject)


def test_node_identity_keying_cannot_collapse_path_attribution():
    assert not _path_identity_probe(NodeIdentityKeyed)


def test_rejected_path_does_not_poison_following_valid_context():
    case = _case("rollback", "malformed-after-sicilian")
    table = ContextTable(_docs())
    for path in case["setup_paths"]:
        table.insert("standard", path)
    before = copy.deepcopy(table.map)
    try:
        table.insert(case["variant"], case["rejected_path"])
    except ContextError as exc:
        assert exc.failure_class == case["expect_failure"]
    else:
        raise AssertionError("malformed path accepted")
    assert table.map == before
    table.insert("standard", case["then_path"])
    assert [list(record) for record in table.serialize()] == case["expect_serialized"]
