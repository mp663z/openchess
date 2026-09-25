"""T0592 import-illegal pure reference contract; T0594 swaps binding."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.import_refusal_model import reference_refusal
from tests.test_t0537_import_pgn_contract import VALID_PGN
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0592")
PRODUCTION_BINDING = reference_refusal  # T0594 replaces only this binding.

ILLEGAL = [("Ke3", VALID_PGN.replace("2. Nf3 Nc6", "2. Ke3 Nc6")),
           ("Qxh5", VALID_PGN.replace("2. Nf3 Nc6", "2. Qxh5 Nc6"))]


def test_scenario_pinned():
    assert SCENARIO == {"id": "import-illegal", "chain_task": "T0592",
                        "sources": ["pgn-file", "pgn-multi"],
                        "visible_output": "refusal-with-position",
                        "persisted_state": ["rejection-record"],
                        "telemetry": ["import.started", "import.game_rejected", "import.completed"],
                        "error_codes": ["illegal_move"]}


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
@pytest.mark.parametrize("san,fixture", ILLEGAL)
def test_illegal_san_refusal_has_position_record_and_events(source_id, san, fixture):
    store = ReferenceStore()
    result = PRODUCTION_BINDING(fixture, store, source_id=source_id, scenario=SCENARIO)
    assert result.kind == "refusal-with-position" and result.code == "illegal_move"
    assert result.marker["position"] == {"game_number": 1, "san": san}
    assert result.source_id == source_id and store.rejections == [result]
    assert store.index == [] and store.records == {} and store.summary is None
    assert store.telemetry == SCENARIO["telemetry"]


def _drop_position(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    result.marker = {}
    return result


def _wrong_code(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    result.code = "malformed_request"
    return result


def _drop_rejection(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    store.rejections = []
    return result


def _drop_event(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    store.telemetry.remove("import.game_rejected")
    return result


@pytest.mark.parametrize("mutant", [_drop_position, _wrong_code, _drop_rejection, _drop_event])
def test_black_box_mutant_red(mutant):
    def probe(binding):
        store = ReferenceStore()
        result = binding(ILLEGAL[0][1], store, scenario=SCENARIO)
        assert result.marker["position"] == {"game_number": 1, "san": "Ke3"}
        assert result.code == "illegal_move" and store.rejections == [result]
        assert store.telemetry == SCENARIO["telemetry"]
    probe(PRODUCTION_BINDING)
    with pytest.raises((AssertionError, KeyError)):
        probe(mutant)
