"""T0142: permanent red battery for concrete opening-context defects."""

from __future__ import annotations

import contextlib
import copy

from tests.test_t0140_opening_context_contract import (
    ContextError,
    ContextTable,
    _docs,
)
from tests.test_t0141_opening_context_fixture import CASES


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
    def insert(self, variant_id, path):
        # Simulate the forbidden design: distinct transposing paths share one
        # node-derived key, so the second path returns the first context.
        petroff = ("e2e4", "e7e5", "g1f3", "g8f6")
        zukertort = ("g1f3", "g8f6", "e2e4", "e7e5")
        if tuple(path) in {petroff, zukertort}:
            key = (variant_id, ("transposed-final-position",))
            if key in self.map:
                return self.map[key]
            rec = super().insert(variant_id, path)
            self.map[key] = rec
            return rec
        return super().insert(variant_id, path)


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


def _path_identity_probe(table_cls):
    petroff = ["e2e4", "e7e5", "g1f3", "g8f6"]
    zukertort = ["g1f3", "g8f6", "e2e4", "e7e5"]
    table = table_cls(_docs())
    a = table.insert("standard", petroff)
    b = table.insert("standard", zukertort)
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
