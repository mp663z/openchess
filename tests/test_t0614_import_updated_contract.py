"""T0614 updated-game pure reference; T0616 swaps second-call binding."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from tests.test_t0537_import_pgn_contract import FULL_HEADER_PGN
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, Refusal, reference_import

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0614")
NEW_PGN = FULL_HEADER_PGN.replace("2. Nf3 Nc6", "2. Nf3 d6")


def reference_updated(text, store, *, source_id="pgn-file"):
    if source_id not in SCENARIO["sources"]:
        raise Refusal("unknown_rights")
    result = reference_import(text, store, source_id=source_id, scenario=SCENARIO)
    if result["games_updated"]:
        result["kind"] = SCENARIO["visible_output"]
        store.summary = copy.deepcopy(result)
    return result


PRODUCTION_BINDING = reference_updated  # T0616 swaps only the second call.


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


def _probe(binding, source_id="pgn-file"):
    store = ReferenceStore()
    reference_updated(FULL_HEADER_PGN, store, source_id=source_id)  # test-owned seed
    old_index = copy.deepcopy(store.index)
    old_records = copy.deepcopy(store.records)
    old_entry = old_index[0]
    old_record = copy.deepcopy(store.records[old_entry["record"]])
    before_events = len(store.telemetry)
    result = binding(NEW_PGN, store, source_id=source_id)
    assert result == {"kind": "updated-summary", "source_id": source_id,
                      "games_imported": 0, "games_already_imported": 0, "games_updated": 1}
    assert store.summary == result
    assert store.telemetry[before_events:] == SCENARIO["telemetry"]
    assert len(store.index) == 1 and store.index[0]["game_id"] == old_entry["game_id"]
    new_entry = store.index[0]
    assert new_entry["record"] != old_entry["record"]
    assert store.records[old_entry["record"]] == old_record == old_records[old_entry["record"]]
    new = store.records[new_entry["record"]]
    assert new["game_id"] == old_record["game_id"]
    assert new["content_sha256"] != old_record["content_sha256"]
    assert new["movetext"] != old_record["movetext"]
    assert new["source_id"] == new["provenance"]["source_id"] == source_id
    assert new["provenance"]["rights_class"] == "user-own"


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_same_id_changed_content_replaces_indexed_record(source_id):
    _probe(PRODUCTION_BINDING, source_id)


def _wrong_kind(text, store, **kw):
    result = reference_updated(text, store, **kw)
    result["kind"] = "import-summary"
    return result


def _same_hash(text, store, **kw):
    prior_hash = store.records[store.index[0]["record"]]["content_sha256"]
    result = reference_updated(text, store, **kw)
    store.records[store.index[0]["record"]]["content_sha256"] = prior_hash
    return result


def _drop_event(text, store, **kw):
    result = reference_updated(text, store, **kw)
    store.telemetry.remove("import.game_updated")
    return result


def _upd_fake(text, store, **kw):
    result = reference_updated(text, store, **kw)
    # Lie about the update while preserving the original indexed record.
    prior_entry = next(e for e in store.records if "Nc6" in store.records[e]["movetext"])
    store.index[0]["record"] = prior_entry
    return result


@pytest.mark.parametrize("mutant", [_wrong_kind, _same_hash, _drop_event, _upd_fake])
def test_black_box_mutants_red(mutant):
    _probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe(mutant)


def test_wrong_source_refused_before_any_state():
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(NEW_PGN, store, source_id="lichess-public")
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None
