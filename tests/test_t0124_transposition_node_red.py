"""T0124: transposition-node red battery with closed mutant coverage."""
from __future__ import annotations

import copy

import pytest

from tests.test_t0122_transposition_node_contract import (
    NodeError,
    NodeTable,
    _docs,
    validate_record,
)
from tests.test_t0123_fixture import CASES, _repaired

DOCS = _docs()
NC, VC, DC, EC, FC = DOCS
EXPECTED_MANIFEST = {
    "unknown-variant-id": ("node-insert", "unknown_variant"),
    "fen-active-color-grammar": ("node-insert", "malformed_position"),
    "fen-impossible-two-white-kings": ("node-insert", "malformed_position"),
    "record-extra-field": ("record-validate", "malformed_node_record"),
    "record-missing-digest": ("record-validate", "malformed_node_record"),
    "record-digest-format": ("record-validate", "malformed_node_record"),
    "record-clocks-not-normalized": ("record-validate", "malformed_node_record"),
    "record-digest-inconsistent": ("record-validate", "malformed_node_record"),
}


def _execute(case, value=None):
    data = case if value is None else value
    if case["kind"] == "node-insert":
        table = NodeTable(DOCS)
        record = table.insert(data["variant"], data["input_fen"])
        return record, table.serialize()
    record = data["record"]
    return validate_record(NC, VC, DC, EC, FC, record), None


def test_mutation_manifest_is_closed_and_exhaustive():
    observed = {
        case["name"]: (case["kind"], case["expect_failure"])
        for case in CASES["malformed"]
    }
    assert observed == EXPECTED_MANIFEST


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda c: c["name"])
def test_each_permissive_repair_mutant_is_killed_by_exact_oracle(case):
    repaired = _repaired(case)
    record, serialized = _execute(case, repaired)
    assert set(record) == {"variant", "digest", "snapshot_fen"}
    if case["kind"] == "node-insert":
        assert serialized == [tuple(record[k] for k in ("variant", "digest", "snapshot_fen"))]

    with pytest.raises(NodeError) as caught:
        _execute(case)
    assert caught.value.failure_class == case["expect_failure"]
    assert caught.value.code == NC["failures"]["mapping"][case["expect_failure"]]


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda c: c["name"])
def test_rejection_is_bit_identical_and_repaired_neighbor_is_stable(case):
    before = copy.deepcopy(case)
    with pytest.raises(NodeError):
        _execute(case)
    assert case == before
    repaired = _repaired(case)
    assert _execute(case, repaired) == _execute(case, copy.deepcopy(repaired))


def test_insert_rejections_leave_a_prepopulated_table_unchanged():
    setup = CASES["happy"][0]
    for case in CASES["malformed"]:
        if case["kind"] != "node-insert":
            continue
        table = NodeTable(DOCS)
        table.insert(setup["variant"], setup["input_fen"])
        before = table.serialize()
        with pytest.raises(NodeError) as caught:
            table.insert(case["variant"], case["input_fen"])
        assert caught.value.failure_class == case["expect_failure"]
        assert table.serialize() == before


def test_transposition_and_distinction_positive_controls():
    for section in ("happy", "boundary"):
        for case in CASES[section]:
            if case["kind"] != "transposition":
                continue
            table = NodeTable(DOCS)
            returned = [table.insert(case["variant"], fen) for fen in case["input_fens"]]
            assert len(table.records()) == case["expect_node_count"]
            if case["expect_node_count"] == 1:
                assert all(record == returned[0] for record in returned)
            else:
                assert len({(r["variant"], r["snapshot_fen"]) for r in returned}) > 1


def test_rollback_manifest_rejects_then_recovers_without_trace():
    for case in CASES["rollback"]:
        if case["kind"] == "rollback-insert":
            table = NodeTable(DOCS)
            for fen in case["setup_fens"]:
                table.insert(case["variant"], fen)
            before = table.serialize()
            with pytest.raises(NodeError) as caught:
                table.insert(case["reject_variant"], case["reject_fen"])
            assert caught.value.failure_class == case["expect_failure"]
            assert table.serialize() == before
            assert table.insert(case["variant"], case["then_fen"]) == case["expect_record"]
        else:
            reject_before = copy.deepcopy(case["reject_record"])
            with pytest.raises(NodeError) as caught:
                validate_record(NC, VC, DC, EC, FC, case["reject_record"])
            assert caught.value.failure_class == case["expect_failure"]
            assert case["reject_record"] == reject_before
            assert validate_record(NC, VC, DC, EC, FC, case["then_record"]) == case["then_record"]
