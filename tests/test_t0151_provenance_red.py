"""T0151: production provenance red suite.

This file deliberately imports the production surface owned by T0152.
On T0151's parent main that module does not exist, so collection is red
for exactly one reason: ``ModuleNotFoundError: graph.provenance``.
The fixture/reference oracle is used only for vectors and differential
expectations. No test-local table is a production substitute.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from graph.provenance import ProvenanceError, ProvenanceTable, validate_record

from tests.test_t0149_provenance_contract import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/provenance/cases.json").read_text())


def _table():
    return ProvenanceTable()


def _repair(case):
    out = copy.deepcopy(case["record"])
    repair = case["minimal_repair"]
    node = out
    for part in repair["path"][:-1]:
        node = node[part]
    leaf = repair["path"][-1]
    if repair["op"] == "set":
        node[leaf] = copy.deepcopy(repair["value"])
    elif repair["op"] == "append":
        node[leaf].append(copy.deepcopy(repair["value"]))
    else:
        del node[leaf]
    return out


@pytest.mark.parametrize("case", CASES["happy"], ids=lambda case: case["name"])
def test_production_happy(case):
    table = _table()
    record = table.insert(copy.deepcopy(case["record"]))
    assert record == case["expect_record"]
    assert validate_record(record) == record
    assert len(table.records()) == 1


@pytest.mark.parametrize("case", CASES["boundary"], ids=lambda case: case["name"])
def test_production_boundary(case):
    table = _table()
    if case["kind"] == "insert":
        record = table.insert(copy.deepcopy(case["record"]))
        assert len(record["sources"]) == case["expect_source_count"]
        return
    returned = [table.insert(copy.deepcopy(record)) for record in case["records"]]
    assert len(table.records()) == case["expect_record_count"]
    if "expect_record" in case:
        assert returned[-1] == case["expect_record"]
    reverse = _table()
    for record in reversed(case["records"]):
        reverse.insert(copy.deepcopy(record))
    assert reverse.serialize() == table.serialize()


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda case: case["name"])
def test_production_malformed_and_minimal_neighbor(case):
    table = _table()
    before = table.serialize()
    original = copy.deepcopy(case["record"])
    with pytest.raises(ProvenanceError) as caught:
        table.insert(case["record"])
    assert caught.value.failure_class == case["expect_failure"]
    assert caught.value.code == FAILURE_MAPPING[case["expect_failure"]]
    assert case["record"] == original and table.serialize() == before
    accepted = table.insert(_repair(case))
    assert validate_record(accepted) == accepted


@pytest.mark.parametrize("case", CASES["rollback"], ids=lambda case: case["name"])
def test_production_rollback_and_recovery(case):
    table = _table()
    for record in case["setup"]:
        table.insert(copy.deepcopy(record))
    before = table.serialize()
    with pytest.raises(ProvenanceError) as caught:
        table.insert(copy.deepcopy(case["reject_record"]))
    assert caught.value.failure_class == case["expect_failure"]
    assert table.serialize() == before
    table.insert(copy.deepcopy(case["then_record"]))
    assert len(table.records()) == case["expect_record_count"]


class _AcceptAll:
    def __init__(self):
        self._records = []

    def insert(self, record):
        self._records.append(record)
        return record

    def serialize(self):
        return self._records


class _PartialCommit:
    def __init__(self, real):
        self.real = real

    def insert(self, record):
        try:
            return self.real.insert(record)
        except ProvenanceError:
            self.real.by_key[("injected",)] = copy.deepcopy(record)
            raise

    def serialize(self):
        return self.real.serialize()


def test_accept_all_mutant_is_killed_by_every_malformed_case():
    for case in CASES["malformed"]:
        mutant = _AcceptAll()
        mutant.insert(copy.deepcopy(case["record"]))
        assert mutant.serialize(), "mutant witness must accept and store"
        with pytest.raises(ProvenanceError):
            _table().insert(copy.deepcopy(case["record"]))


def test_partial_commit_mutant_is_killed_with_nonvacuous_witness():
    case = CASES["rollback"][0]
    real = _table()
    for record in case["setup"]:
        real.insert(copy.deepcopy(record))
    mutant = _PartialCommit(real)
    before = mutant.serialize()
    with pytest.raises(ProvenanceError):
        mutant.insert(copy.deepcopy(case["reject_record"]))
    assert mutant.serialize() != before, "mutant must realize partial commit"

    honest = _table()
    for record in case["setup"]:
        honest.insert(copy.deepcopy(record))
    honest_before = honest.serialize()
    with pytest.raises(ProvenanceError):
        honest.insert(copy.deepcopy(case["reject_record"]))
    assert honest.serialize() == honest_before
