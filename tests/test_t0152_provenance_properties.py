"""T0152 production provenance requirements and independence battery."""

from __future__ import annotations

import copy

import pytest

from graph.provenance import FAILURE_MAPPING, ProvenanceError, ProvenanceTable, validate_record
from tools.production_test_dependency_lint import lint, t0151_switch_findings

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
SOURCE = {
    "source_id": "lichess-public",
    "game_id": "lichess:abcdefgh",
    "first_observed_at": "2026-09-01T12:00:00Z",
}
OTHER_SOURCE = {
    "source_id": "pgn-file",
    "game_id": "pgn:local-game-1",
    "first_observed_at": "2026-09-02T08:30:00Z",
}
NODE = {"variant": "standard", "snapshot_fen": START}
EDGE = {
    "variant": "standard",
    "move": "e2e4",
    "from_snapshot_fen": START,
    "to_snapshot_fen": AFTER_E4,
}
CONTEXT = {"variant": "standard", "path_moves": ["e2e4", "c7c5"]}


def _record(target=NODE, sources=None, kind="transposition_node"):
    return {
        "target_kind": kind,
        "target": copy.deepcopy(target),
        "sources": copy.deepcopy([SOURCE] if sources is None else sources),
    }


@pytest.mark.parametrize(
    "record,failure",
    [
        (_record(None, [{**SOURCE, "source_id": "shadow"}]), "unknown_source"),
        (_record(None, [{"source_id": "lichess-public"}]), "malformed_provenance_record"),
        (_record(None, [{**SOURCE, "source_id": "shadow"}], "future_kind"), "unknown_target_kind"),
        (_record(None, [{"source_id": "lichess-public"}], "future_kind"), "unknown_target_kind"),
        (_record(NODE, [{**SOURCE, "source_id": "shadow"}]), "unknown_source"),
        (_record(NODE, [{"source_id": "lichess-public"}]), "malformed_provenance_record"),
    ],
)
def test_r2_pairwise_multi_invalid_precedence(record, failure):
    before = copy.deepcopy(record)
    with pytest.raises(ProvenanceError) as caught:
        validate_record(record)
    assert (caught.value.failure_class, caught.value.code) == (
        failure,
        FAILURE_MAPPING[failure],
    )
    assert record == before


@pytest.mark.parametrize(
    "record",
    [
        _record(NODE),
        _record(EDGE, kind="route_edge"),
        _record(CONTEXT, kind="opening_context"),
    ],
)
def test_r3_all_sibling_target_kinds_have_stable_exact_identity(record):
    left, right = ProvenanceTable(), ProvenanceTable()
    first = left.insert(record)
    second = left.insert(copy.deepcopy(record))
    right.insert(copy.deepcopy(record))
    assert first == second == record
    assert len(left.records()) == 1
    assert left.serialize() == right.serialize()


def test_r3_source_union_dedup_order_and_stored_shape_closure():
    left, right = ProvenanceTable(), ProvenanceTable()
    one = _record(sources=[SOURCE, SOURCE])
    two = _record(sources=[OTHER_SOURCE])
    left.insert(one)
    merged = left.insert(two)
    right.insert(two)
    right.insert(one)
    assert merged["sources"] == [SOURCE, OTHER_SOURCE]
    assert left.serialize() == right.serialize()
    assert set(merged) == {"target_kind", "target", "sources"}
    assert all(set(source) == {"source_id", "game_id", "first_observed_at"}
               for source in merged["sources"])


def test_r4_insert_records_and_serialize_are_detached():
    supplied = _record()
    pristine = copy.deepcopy(supplied)
    table = ProvenanceTable()
    returned = table.insert(supplied)
    serialized = table.serialize()
    returned["sources"].clear()
    supplied["target"]["snapshot_fen"] = "poison"
    rows = table.records()
    rows[0]["sources"].clear()
    serialized.append(("poison",))
    assert table.records() == [pristine]
    assert table.serialize() != serialized


class _InvalidPrefix:
    def records(self):
        return [_record(EDGE, kind="route_edge"), _record(None)]


class _GeneratorEscape:
    def records(self):
        def rows():
            yield _record(EDGE, kind="route_edge")
            raise GeneratorExit("stop")
        return rows()


@pytest.mark.parametrize("hostile", [_InvalidPrefix(), _GeneratorEscape()])
def test_r4_hostile_batches_are_atomic_even_on_baseexception(hostile):
    table = ProvenanceTable()
    table.insert(_record())
    before = table.serialize()
    with pytest.raises((ProvenanceError, GeneratorExit)):
        table.merge(hostile)
    assert table.serialize() == before


def test_r5_exact_t0151_switch_changes_only_three_bindings():
    assert t0151_switch_findings() == []


def test_r1_r6_property_file_is_externally_dependency_linted():
    lint(__file__)
