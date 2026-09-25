"""T0603 duplicate pure reference battery; T0605 swaps binding."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from tests.test_t0537_import_pgn_contract import VALID_PGN
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, reference_import

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0603")


def reference_duplicate(text, store, *, source_id="pgn-file"):
    if source_id not in SCENARIO["sources"]:
        raise ValueError("source outside duplicate scenario")
    # Seed with the same game under the same source, as the T0537 merged
    # identity/hash reading requires for a duplicate; isolate this run's events.
    reference_import(text, store, source_id=source_id, scenario=SCENARIO)
    store.telemetry.clear()
    before = copy.deepcopy((store.index, store.records))
    result = reference_import(text, store, source_id=source_id, scenario=SCENARIO)
    assert (store.index, store.records) == before
    result["kind"] = SCENARIO["visible_output"]
    store.summary = copy.deepcopy(result)
    return result


PRODUCTION_BINDING = reference_duplicate  # T0605 replaces only this binding.


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


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_reimport_is_visible_noop_with_unchanged_game_state(source_id):
    store = ReferenceStore()
    result = PRODUCTION_BINDING(VALID_PGN, store, source_id=source_id)
    assert result == {"kind": "already-imported", "source_id": source_id,
                      "games_imported": 0, "games_already_imported": 1, "games_updated": 0}
    assert store.summary == result
    assert store.telemetry == SCENARIO["telemetry"]
    assert len(store.index) == 1 and len(store.records) == 1
    rec = store.records[store.index[0]["record"]]
    assert rec["source_id"] == rec["provenance"]["source_id"] == source_id
    assert rec["game_id"] == store.index[0]["game_id"]


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


@pytest.mark.parametrize("mutant", [_wrong_kind, _write_duplicate, _drop_event])
def test_black_box_mutants_red(mutant):
    def probe(binding):
        store = ReferenceStore()
        result = binding(VALID_PGN, store)
        assert result["kind"] == "already-imported"
        assert result["games_already_imported"] == 1
        assert len(store.index) == len(store.records) == 1
        assert store.telemetry == SCENARIO["telemetry"]
    probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        probe(mutant)
