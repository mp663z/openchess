"""T0570 import-bad-tags pure reference contract; T0572 swaps binding."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.import_refusal_model import reference_refusal
from tests.test_t0537_import_pgn_contract import VALID_PGN
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0570")
PRODUCTION_BINDING = reference_refusal  # T0572 replaces only this line.

BAD_TAGS = [
    '[Event "x"\n[Result "1-0"]\n\n1. e4 e5 1-0\n',
    VALID_PGN.replace('[Site', '[Event "again"]\n[Site', 1),
    VALID_PGN.replace('[White "a"]', '[White a]', 1),
    VALID_PGN.replace('[Variant "Standard"]', '[Event "later"]\n[Variant "Standard"]', 1),
]


def test_contract_scenario_pinned():
    assert SCENARIO == {"id": "import-bad-tags", "chain_task": "T0570",
                        "sources": ["pgn-file", "pgn-multi"],
                        "visible_output": "refusal-with-location",
                        "persisted_state": ["rejection-record"],
                        "telemetry": ["import.started", "import.game_rejected", "import.completed"],
                        "error_codes": ["malformed_request"]}


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
@pytest.mark.parametrize("fixture", BAD_TAGS)
def test_malformed_tag_refusal_has_location_record_and_events(source_id, fixture):
    store = ReferenceStore()
    result = PRODUCTION_BINDING(fixture, store, source_id=source_id, scenario=SCENARIO)
    assert result.kind == "refusal-with-location" and result.code == "malformed_request"
    assert result.source_id == source_id and result.game_number == 1
    assert result.marker["location"]["game_number"] == 1
    assert result.marker["location"]["line"] == (
        3 if "[White a]" in fixture else 6 if "[Event \"later\"]" in fixture
        else 2 if "[Event \"again\"]" in fixture else 1)
    assert store.rejections == [result] and store.index == [] and store.records == {}
    assert store.telemetry == SCENARIO["telemetry"]
    assert store.summary is None


def _missing_record(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    store.rejections = []
    return result


def _missing_location(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    result.marker = {}
    return result


def _missing_event(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    store.telemetry.remove("import.game_rejected")
    return result


@pytest.mark.parametrize("mutant", [_missing_record, _missing_location, _missing_event])
def test_black_box_mutants_red(mutant):
    def probe(binding):
        store = ReferenceStore()
        result = binding(BAD_TAGS[0], store, scenario=SCENARIO)
        assert result.marker["location"]["line"] == 1
        assert store.rejections == [result]
        assert store.telemetry == SCENARIO["telemetry"]
    probe(PRODUCTION_BINDING)
    with pytest.raises((AssertionError, KeyError)):
        probe(mutant)


def test_second_game_bad_tag_has_game_relative_line():
    store = ReferenceStore()
    result = PRODUCTION_BINDING(VALID_PGN + "\n" + BAD_TAGS[0], store, scenario=SCENARIO)
    assert result.marker["location"] == {"game_number": 2, "line": 1}
    assert result.game_number == 2
    assert len(store.index) == 1 and store.summary is None
    record = store.records[store.index[0]["record"]]
    assert record["tags"]["White"] == "a"
    assert store.telemetry == ["import.started", "import.game_stored",
                               "import.game_rejected", "import.completed"]


def test_wrong_source_refused_without_effects():
    from tests.test_t0548_import_multi_pgn_contract import Refusal

    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(BAD_TAGS[0], store, source_id="lichess-public", scenario=SCENARIO)
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None


def test_duplicate_tag_location_ignores_other_tag_value_containing_name():
    text = ('[Event "Test"]\n[Other "Event mentioned"]\n'
            '[Site "x"]\n[Event "actual-duplicate"]\n'
            '[Result "1-0"]\n\n1. e4 e5 1-0\n')
    store = ReferenceStore()
    result = PRODUCTION_BINDING(text, store, scenario=SCENARIO)
    assert result.marker["location"] == {"game_number": 1, "line": 4}
