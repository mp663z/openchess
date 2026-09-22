"""T0143 production opening-context runtime conformance."""

from __future__ import annotations

import copy

import pytest

from graph.opening_context import ContextError, ContextTable
from tests.test_t0141_opening_context_fixture import CASES


def test_fixture_happy_boundary_and_malformed_against_production():
    for section in ("happy", "boundary"):
        for case in CASES[section]:
            got = ContextTable().insert(case["variant"], case["path"])
            assert (got["opening_code"], got["opening_name"]) == (
                case["expect_code"],
                case["expect_name"],
            )
    for case in CASES["malformed"]:
        table = ContextTable()
        before = table.records
        with pytest.raises(ContextError) as caught:
            table.insert(case["variant"], case["path"])
        assert caught.value.failure_class == case["expect_failure"]
        assert table.records == before


def test_rollback_and_recovery_against_production():
    for case in CASES["rollback"]:
        table = ContextTable()
        for path in case["setup_paths"]:
            table.insert("standard", path)
        before = table.records
        with pytest.raises(ContextError):
            table.insert(case["variant"], case["rejected_path"])
        assert table.records == before
        table.insert("standard", case["then_path"])
        assert [list(row) for row in table.serialize()] == case["expect_serialized"]


def test_atomic_exact_record_merge_and_copies():
    left, right = ContextTable(), ContextTable()
    original = left.insert("standard", ["e2e4", "c7c5"])
    right.insert("standard", ["a2a3"])
    left.merge(right)
    assert len(left.records) == 2
    original["opening_name"] = "tamper"
    exposed = left.records
    exposed[0]["opening_name"] = "tamper"
    assert all(record["opening_name"] != "tamper" for record in left.records)

    corrupt = ContextTable()
    corrupt._records = copy.deepcopy(left._records)
    victim = next(iter(corrupt._records))
    corrupt._records[victim]["opening_name"] = "tamper"
    before = left.records
    with pytest.raises(ContextError):
        left.merge(corrupt)
    assert left.records == before


def test_public_boundaries_are_typed_total():
    table = ContextTable()
    for variant, path in (
        (None, []),
        ("standard", None),
        ("standard", [23]),
        ("standard", ["e2e2"]),
    ):
        before = table.records
        with pytest.raises(ContextError):
            table.insert(variant, path)
        assert table.records == before


def test_every_public_view_is_detached():
    table = ContextTable()
    table.insert("standard", ["e2e4", "c7c5"])
    baseline = table.serialize()
    views = [table.records, table.map, table.serialize()]
    views[0][0]["opening_name"] = "POISON"
    next(iter(views[1].values()))["opening_name"] = "POISON"
    views[2][0] = ("standard", "", "-", "POISON")
    assert table.serialize() == baseline


@pytest.mark.parametrize("location", ["destination-value", "destination-key", "incoming-key"])
def test_merge_rejects_key_or_record_corruption_atomically(location):
    destination = ContextTable()
    destination.insert("standard", ["e2e4", "c7c5"])
    incoming = ContextTable()
    incoming.insert("standard", ["d2d4", "d7d5", "c2c4"])
    if location == "destination-value":
        victim = next(iter(destination._records.values()))
        victim["opening_name"] = "POISON"
    elif location == "destination-key":
        key, record = destination._records.popitem()
        destination._records[(key[0], ("a2a3",))] = record
    else:
        key, record = incoming._records.popitem()
        incoming._records[(key[0], ("a2a3",))] = record
    before = copy.deepcopy(destination._records)
    with pytest.raises(ContextError):
        destination.merge(incoming)
    assert destination._records == before


def test_mixed_valid_prefix_corrupt_suffix_merge_rolls_back():
    destination = ContextTable()
    destination.insert("standard", ["e2e4", "c7c5"])
    incoming = ContextTable()
    incoming.insert("standard", ["a2a3"])
    incoming.insert("standard", ["d2d4", "d7d5", "c2c4"])
    last = list(incoming._records)[-1]
    incoming._records[last]["opening_name"] = "POISON"
    before = copy.deepcopy(destination._records)
    with pytest.raises(ContextError):
        destination.merge(incoming)
    assert destination._records == before
