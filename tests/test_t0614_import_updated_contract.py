"""T0614 updated-game pure reference contract; T0616 swaps binding."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from tests.test_t0537_import_pgn_contract import FULL_HEADER_PGN
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, reference_import

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0614")
NEW_PGN = FULL_HEADER_PGN.replace("2. Nf3 Nc6", "2. Nf3 d6")


def reference_updated(store, *, source_id="pgn-file"):
    if source_id not in SCENARIO["sources"]:
        raise ValueError("source outside update scenario")
    reference_import(FULL_HEADER_PGN, store, source_id=source_id, scenario=SCENARIO)
    old_index = copy.deepcopy(store.index)
    old_record = copy.deepcopy(store.records[old_index[0]["record"]])
    store.telemetry.clear()
    result = reference_import(NEW_PGN, store, source_id=source_id, scenario=SCENARIO)
    assert len(store.index) == 1 and store.index[0]["game_id"] == old_index[0]["game_id"]
    assert store.index[0]["record"] != old_index[0]["record"]
    assert store.records[old_index[0]["record"]] == old_record
    result["kind"] = SCENARIO["visible_output"]
    store.summary = copy.deepcopy(result)
    return result, old_index, old_record


PRODUCTION_BINDING = reference_updated  # T0616 replaces only this binding.


def test_scenario_pinned():
    assert SCENARIO == {"id": "import-updated", "chain_task": "T0614",
                        "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
                        "visible_output": "updated-summary",
                        "persisted_state": ["replaced-record", "provenance", "index-entries"],
                        "telemetry": ["import.started", "import.game_updated", "import.completed"],
                        "error_codes": []}
    assert CONTRACT["idempotency"]["updated"] == {
        "policy": "replace-recorded", "outcome": "updated",
        "precondition": "same-game_id-different-content_sha256"}


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_same_id_changed_content_replaces_indexed_record(source_id):
    store = ReferenceStore()
    result, old_index, old_record = PRODUCTION_BINDING(store, source_id=source_id)
    assert result == {"kind": "updated-summary", "source_id": source_id,
                      "games_imported": 0, "games_already_imported": 0, "games_updated": 1}
    assert store.summary == result and store.telemetry == SCENARIO["telemetry"]
    assert len(store.index) == 1 and len({e["game_id"] for e in store.index}) == 1
    new = store.records[store.index[0]["record"]]
    assert new["game_id"] == old_record["game_id"] == old_index[0]["game_id"]
    assert new["content_sha256"] != old_record["content_sha256"]
    assert new["movetext"] != old_record["movetext"]
    assert new["source_id"] == new["provenance"]["source_id"] == source_id
    assert new["provenance"]["rights_class"] == "user-own"
    assert store.records[old_index[0]["record"]] == old_record


def _wrong_kind(store, **kw):
    result, old_index, old_record = reference_updated(store, **kw)
    result["kind"] = "import-summary"
    return result, old_index, old_record


def _same_hash(store, **kw):
    result, old_index, old_record = reference_updated(store, **kw)
    store.records[store.index[0]["record"]]["content_sha256"] = old_record["content_sha256"]
    return result, old_index, old_record


def _drop_event(store, **kw):
    result, old_index, old_record = reference_updated(store, **kw)
    store.telemetry.remove("import.game_updated")
    return result, old_index, old_record


@pytest.mark.parametrize("mutant", [_wrong_kind, _same_hash, _drop_event])
def test_black_box_mutants_red(mutant):
    def probe(binding):
        store = ReferenceStore()
        result, _, old_record = binding(store)
        assert result["kind"] == "updated-summary"
        assert store.telemetry == SCENARIO["telemetry"]
        latest = store.records[store.index[0]["record"]]
        assert latest["content_sha256"] != old_record["content_sha256"]
    probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        probe(mutant)
