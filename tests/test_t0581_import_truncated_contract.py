"""T0581 import-truncated pure reference contract; T0583 swaps binding."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.import_refusal_model import reference_refusal
from tests.test_t0537_import_pgn_contract import VALID_PGN
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0581")
PRODUCTION_BINDING = reference_refusal  # T0583 replaces only this binding.

TRUNCATED = [
    VALID_PGN.replace(" 1-0\n", "\n"),
    '[Event "x"]\n[Result "1-0"]\n\n1. e4 (1. d4 d5 1-0\n',
    '[Event "x"]\n[Result "1-0"]\n\n1. e4 {unclosed\n',
]


def test_scenario_pinned():
    assert SCENARIO == {"id": "import-truncated", "chain_task": "T0581",
                        "sources": ["pgn-file", "pgn-multi"],
                        "visible_output": "refusal-with-location",
                        "persisted_state": ["rejection-record"],
                        "telemetry": ["import.started", "import.game_rejected", "import.completed"],
                        "error_codes": ["malformed_request"]}


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
@pytest.mark.parametrize("fixture", TRUNCATED)
def test_truncated_refuses_at_last_line_without_game_state(source_id, fixture):
    store = ReferenceStore()
    result = PRODUCTION_BINDING(fixture, store, source_id=source_id, scenario=SCENARIO)
    assert result.kind == "refusal-with-location" and result.code == "malformed_request"
    assert result.marker["location"] == {"game_number": 1,
                                          "line": len(fixture.strip().splitlines())}
    assert result.source_id == source_id and store.rejections == [result]
    assert store.index == [] and store.records == {} and store.summary is None
    assert store.telemetry == SCENARIO["telemetry"]


def _drop_marker(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    result.marker = {}
    return result


def _drop_rejection(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    store.rejections = []
    return result


def _drop_completed(text, store, **kw):
    result = reference_refusal(text, store, **kw)
    store.telemetry.pop()
    return result


@pytest.mark.parametrize("mutant", [_drop_marker, _drop_rejection, _drop_completed])
def test_black_box_mutant_red(mutant):
    def probe(binding):
        store = ReferenceStore()
        result = binding(TRUNCATED[0], store, scenario=SCENARIO)
        assert result.marker["location"]["line"] == len(TRUNCATED[0].strip().splitlines())
        assert store.rejections == [result]
        assert store.telemetry == SCENARIO["telemetry"]
    probe(PRODUCTION_BINDING)
    with pytest.raises((AssertionError, KeyError)):
        probe(mutant)
