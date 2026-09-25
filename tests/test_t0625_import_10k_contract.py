"""T0625 import/10k pure reference battery; T0627 swaps only the binding."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.test_t0537_import_pgn_contract import FULL_HEADER_PGN
from tests.test_t0548_import_multi_pgn_contract import (
    ReferenceStore,
    Refusal,
    _split,
    reference_import,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0625")


def _games(count):
    """Test-owned corpus: one legal mainline, 10k distinct six-header identities."""
    compact = FULL_HEADER_PGN.replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 1-0")
    return "\n".join(compact.replace('[Round "1"]', f'[Round "{i}"]')
                     for i in range(1, count + 1))


def reference_10k(text, store, *, source_id="pgn-multi"):
    if source_id not in SCENARIO["sources"]:
        raise Refusal("unknown_rights")
    result = reference_import(text, store, source_id=source_id, scenario=SCENARIO)
    # import.yaml requires progress but specifies no cadence, payload, or
    # interleaving. This reference emits one event without pinning its slot.
    store.telemetry.insert(-1, "import.progress")
    result["kind"] = SCENARIO["visible_output"]
    store.summary = dict(result)
    return result


PRODUCTION_BINDING = reference_10k  # T0627 replaces only this binding.


def test_scenario_pinned():
    assert SCENARIO == {
        "id": "import-10k", "chain_task": "T0625",
        "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
        "visible_output": "progress-and-summary",
        "persisted_state": ["game-records", "provenance", "index-entries"],
        "telemetry": ["import.started", "import.progress", "import.game_stored",
                      "import.completed"],
        "error_codes": ["malformed_request"],
    }
    assert CONTRACT["atomicity"]["commit_boundary"] == "per-game"
    assert CONTRACT["atomicity"]["partial_game"] == "never-visible"


def _probe(binding, count=3, source_id="pgn-multi"):
    store = ReferenceStore()
    result = binding(_games(count), store, source_id=source_id)
    assert result == {"kind": "progress-and-summary", "source_id": source_id,
                      "games_imported": count, "games_already_imported": 0,
                      "games_updated": 0}
    assert store.summary == result
    events = store.telemetry
    assert events[0] == "import.started" and events[-1] == "import.completed"
    assert events.count("import.started") == events.count("import.completed") == 1
    assert events.count("import.progress") >= 1
    assert events.count("import.game_stored") == count
    assert set(events) == set(SCENARIO["telemetry"])
    last_stored = max(i for i, event in enumerate(events) if event == "import.game_stored")
    assert events.index("import.completed") > last_stored
    assert not hasattr(store, "rejections")
    assert len(store.index) == count
    assert len({entry["game_id"] for entry in store.index}) == count
    assert len({entry["record"] for entry in store.index}) == count
    assert set(store.records) == {entry["record"] for entry in store.index}
    assert [store.records[entry["record"]]["tags"]["Round"] for entry in store.index] == [
        str(i) for i in range(1, count + 1)]
    for entry in store.index:
        assert list(entry) == ["game_id", "record"]
        record = store.records[entry["record"]]
        assert list(record) == CONTRACT["record"]["fields"]
        assert list(record["provenance"]) == CONTRACT["record"]["provenance_fields"]
        assert record["game_id"] == entry["game_id"]
        assert record["source_id"] == record["provenance"]["source_id"] == source_id
        assert record["provenance"]["rights_class"] == "user-own"
        assert entry["record"] == f'{entry["game_id"]}--{record["content_sha256"][:16]}.json'
    assert {record["tags"]["Round"] for record in store.records.values()} == {
        str(i) for i in range(1, count + 1)}


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_allowed_sources_store_records_with_progress(source_id):
    _probe(PRODUCTION_BINDING, source_id=source_id)


def test_deterministic_ten_thousand_game_import():
    _probe(PRODUCTION_BINDING, count=10_000)


def _wrong_kind(text, store, **kwargs):
    result = reference_10k(text, store, **kwargs)
    result["kind"] = "import-summary"
    return result


def _drop_progress(text, store, **kwargs):
    result = reference_10k(text, store, **kwargs)
    store.telemetry.remove("import.progress")
    return result


def _drop_last_record(text, store, **kwargs):
    result = reference_10k(text, store, **kwargs)
    store.records.pop(store.index[-1]["record"])
    return result


def _fake_count(text, store, **kwargs):
    result = reference_10k(text, store, **kwargs)
    result["games_imported"] -= 1
    store.summary = dict(result)
    return result


def _premature_complete(text, store, **kwargs):
    result = reference_10k(text, store, **kwargs)
    store.telemetry.remove("import.completed")
    store.telemetry.insert(1, "import.completed")
    return result


@pytest.mark.parametrize("mutant", [_wrong_kind, _drop_progress, _drop_last_record,
                                     _fake_count, _premature_complete])
def test_black_box_mutants_red(mutant):
    _probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe(mutant)


def test_unknown_source_refused_before_any_state():
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(_games(1), store, source_id="lichess-public")
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None and not hasattr(store, "rejections")


def _malformed_at(count, position):
    games = list(_split(_games(count)))
    games[position - 1] = games[position - 1].replace("1. e4 1-0", "1. e4 {unclosed")
    return "\n\n".join(games)


def _probe_malformed_after_prefix(binding, count=5, position=3):
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        binding(_malformed_at(count, position), store)
    assert error.value.code == "malformed_request"
    assert len(store.index) == position - 1
    assert len(store.records) == position - 1
    assert set(store.records) == {entry["record"] for entry in store.index}
    assert [store.records[entry["record"]]["tags"]["Round"]
            for entry in store.index] == [str(i) for i in range(1, position)]
    assert all(list(store.records[entry["record"]]) == CONTRACT["record"]["fields"]
               for entry in store.index)
    assert all(store.records[entry["record"]]["provenance"]["source_id"] == "pgn-multi"
               for entry in store.index)
    assert store.telemetry.count("import.started") == 1
    assert store.telemetry.count("import.game_stored") == position - 1
    assert "import.completed" not in store.telemetry
    assert set(store.telemetry) <= {"import.started", "import.game_stored", "import.progress"}
    assert store.summary is None and not hasattr(store, "rejections")


@pytest.mark.parametrize("count,position", [(5, 3), (10_000, 5_000)])
def test_malformed_middle_game_refuses_without_partial_state(count, position):
    _probe_malformed_after_prefix(PRODUCTION_BINDING, count, position)


def _wrong_malformed_code(text, store, **kwargs):
    try:
        return reference_10k(text, store, **kwargs)
    except Refusal as error:
        if error.code == "malformed_request":
            raise Refusal("illegal_move") from None
        raise


def _skip_malformed(text, store, **kwargs):
    games = list(_split(text))
    repaired = "\n\n".join(game for game in games if "{unclosed" not in game)
    return reference_10k(repaired, store, **kwargs)


@pytest.mark.parametrize("mutant", [_wrong_malformed_code, _skip_malformed])
def test_malformed_input_mutants_red(mutant):
    _probe_malformed_after_prefix(PRODUCTION_BINDING)
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        _probe_malformed_after_prefix(mutant)
