"""T0180 deterministic unit/property battery for production graph diffs."""

from __future__ import annotations

import copy
import random

import pytest

from graph import diff
from graph.node import make_record, record_identity

FENS = [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
]


def _record(fen):
    return make_record("standard", fen)


def _key(record):
    return record_identity(record)


def _state(indices):
    records = [_record(FENS[i]) for i in indices]
    return {_key(record): record for record in records}


@pytest.mark.parametrize("seed", range(40))
def test_seeded_roundtrip_symmetry_and_canonical_sections(seed):
    rng = random.Random(seed)
    base_ids = rng.sample(range(len(FENS)), rng.randrange(len(FENS) + 1))
    target_ids = rng.sample(range(len(FENS)), rng.randrange(len(FENS) + 1))
    base, target = _state(base_ids), _state(target_ids)
    before = copy.deepcopy((base, target))
    forward = diff.compute(base, target)
    reverse = diff.compute(target, base)
    assert diff.apply(forward, base) == target
    assert diff.apply(reverse, target) == base
    assert forward["base_id"] == reverse["target_id"]
    assert forward["target_id"] == reverse["base_id"]
    assert forward["added"] == reverse["removed"]
    assert forward["removed"] == reverse["added"]
    assert forward["changed"] == reverse["changed"] == {}
    for section in ("added", "removed", "changed"):
        assert list(forward[section]) == sorted(forward[section])
    assert (base, target) == before


def test_noop_exactness_and_id_agreement():
    state = _state([0, 1, 2])
    record = diff.compute(state, state)
    assert record["base_id"] == record["target_id"] == diff.state_id(state)
    assert record["added"] == record["removed"] == record["changed"] == {}
    assert diff.apply(record, state) == state


@pytest.mark.parametrize("section", ["added", "removed"])
def test_section_record_semantic_digest_substitution_rejected(section):
    a, b = _record(FENS[0]), _record(FENS[3])
    base, target = ({}, {_key(a): a}) if section == "added" else ({_key(a): a}, {})
    record = diff.compute(base, target)
    record[section][_key(a)]["digest"] = b["digest"]
    pristine = copy.deepcopy((record, base))
    with pytest.raises(diff.DiffError) as caught:
        diff.apply(record, base)
    assert caught.value.failure_class == "malformed_diff_record"
    assert (record, base) == pristine


def test_whole_base_variations_reject_atomically():
    base, target = _state([0, 1]), _state([0, 2])
    record = diff.compute(base, target)
    variants = [
        _state([0]),
        _state([0, 1, 3]),
        {_key(_record(FENS[0])): _record(FENS[0]), _key(_record(FENS[3])): _record(FENS[3])},
    ]
    for candidate in variants:
        before = copy.deepcopy(candidate)
        with pytest.raises(diff.DiffError) as caught:
            diff.apply(record, candidate)
        assert caught.value.failure_class == "conflicting_base"
        assert candidate == before


def test_target_id_tampering_rejects_after_staging_without_input_mutation():
    base, target = _state([0]), _state([1, 2])
    record = diff.compute(base, target)
    record["target_id"] = "gs1:" + "f" * 64
    before = copy.deepcopy(base)
    with pytest.raises(diff.DiffError) as caught:
        diff.apply(record, base)
    assert caught.value.failure_class == "divergent_target"
    assert base == before


@pytest.mark.parametrize("hostile", [None, True, 1, 1.5, "state", [], ()])
def test_compute_hostile_inputs_total_typed(hostile):
    good = _state([0])
    for args in ((hostile, good), (good, hostile)):
        with pytest.raises(diff.DiffError) as caught:
            diff.compute(*args)
        assert caught.value.failure_class == "malformed_diff_record"


def test_property_file_has_no_tests_package_imports():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(__file__).read_text())
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tests"))
            or (
                isinstance(node, ast.Import) and any(a.name.startswith("tests") for a in node.names)
            )
        )
        for node in ast.walk(tree)
    )
