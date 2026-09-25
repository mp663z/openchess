"""T0603 duplicate pure reference battery; T0605 swaps second-call binding."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from tests.test_t0537_import_pgn_contract import VALID_PGN
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, Refusal, reference_import

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0603")


def reference_duplicate(text, store, *, source_id="pgn-file"):
    if source_id not in SCENARIO["sources"]:
        raise Refusal("unknown_rights")
    result = reference_import(text, store, source_id=source_id, scenario=SCENARIO)
    if result["games_already_imported"]:
        result["kind"] = SCENARIO["visible_output"]
        store.summary = copy.deepcopy(result)
    return result


PRODUCTION_BINDING = reference_duplicate  # T0605 swaps only the second call.


def test_scenario_pinned():
    assert SCENARIO == {"id": "import-duplicate", "chain_task": "T0603",
                        "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
                        "visible_output": "already-imported",
                        "persisted_state": ["unchanged-store"],
                        "telemetry": ["import.started", "import.game_duplicate",
                                      "import.completed"],
                        "error_codes": []}
    assert CONTRACT["idempotency"]["duplicate"] == {
        "policy": "no-op-visible", "outcome": "already-imported"}


def _probe(binding, source_id="pgn-file"):
    store = ReferenceStore()
    reference_duplicate(VALID_PGN, store, source_id=source_id)  # test-owned seed
    before_index = copy.deepcopy(store.index)
    before_records = copy.deepcopy(store.records)
    import json

    before_files = {name: json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
                    for name, record in store.records.items()}
    before_events = len(store.telemetry)
    result = binding(VALID_PGN, store, source_id=source_id)
    assert result == {"kind": "already-imported", "source_id": source_id,
                      "games_imported": 0, "games_already_imported": 1, "games_updated": 0}
    assert store.index == before_index and store.records == before_records
    assert {name: json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
            for name, record in store.records.items()} == before_files
    assert store.telemetry[before_events:] == SCENARIO["telemetry"]
    assert len(store.index) == 1 and len(store.records) == 1
    assert store.summary == result


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_reimport_is_visible_noop_with_unchanged_game_state(source_id):
    _probe(PRODUCTION_BINDING, source_id)


def _wrong_kind(text, store, **kw):
    result = reference_duplicate(text, store, **kw)
    result["kind"] = "import-summary"
    return result


def _write_duplicate(text, store, **kw):
    result = reference_duplicate(text, store, **kw)
    store.index.append(copy.deepcopy(store.index[0]))
    return result


def _drop_event(text, store, **kw):
    result = reference_duplicate(text, store, **kw)
    store.telemetry.remove("import.game_duplicate")
    return result


def _dup_rewrites(text, store, **kw):
    result = reference_duplicate(text, store, **kw)
    rec = store.records[store.index[0]["record"]]
    rec["imported_at"] = "changed-by-duplicate"
    return result


@pytest.mark.parametrize("mutant", [_wrong_kind, _write_duplicate, _drop_event, _dup_rewrites])
def test_black_box_mutants_red(mutant):
    _probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe(mutant)


def test_wrong_source_refused_before_any_state():
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(VALID_PGN, store, source_id="lichess-public")
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None
