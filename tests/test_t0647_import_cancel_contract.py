"""T0647 import-cancel reference contract; T0649 swaps only the binding."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ingest.pgn import IllegalMove, MalformedPGN, validate_pgn_game
from tests.import_refusal_model import reference_refusal
from tests.test_t0537_import_pgn_contract import FULL_HEADER_PGN
from tests.test_t0548_import_multi_pgn_contract import (
    ReferenceStore,
    Refusal,
    _split,
    reference_import,
)
from tests.test_t0636_import_folder_contract import FolderRejection, reference_folder

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0647")


def _game(number):
    return FULL_HEADER_PGN.replace('[Round "1"]', f'[Round "{number}"]')


def _input(source_id):
    if source_id == "pgn-folder":
        return {"a.pgn": _game(1) + "\n" + _game(2), "b.pgn": _game(3)}
    return _game(1) + "\n" + _game(2) + "\n" + _game(3)


def reference_cancel(payload, store, *, source_id="pgn-multi", cancel_at=2):
    """Cancel before committing the one-based in-flight game ordinal.

    A deterministic boundary is test-owned; it does not prescribe a runtime
    callback/API shape or depend on wall-clock races. Earlier games commit.
    """
    if source_id not in SCENARIO["sources"]:
        raise Refusal("unknown_rights")
    if type(cancel_at) is not int or cancel_at < 1:
        raise Refusal("malformed_request")
    if source_id == "pgn-folder":
        if type(payload) is not dict or not payload or any(
            type(name) is not str or not name.endswith(".pgn")
            or name in (".pgn", "..pgn") or "/" in name or "\\" in name
            or type(text) is not str
            for name, text in payload.items()
        ):
            raise Refusal("malformed_request")
        games = ((game, name) for name, text in payload.items() for game in _split(text))
    else:
        if type(payload) is not str:
            raise Refusal("malformed_request")
        games = ((game, "in.pgn") for game in _split(payload))
    # Non-folder refusal ends the run before cancellation; delegate its
    # complete outcome to the landed source-specific reference model.
    if source_id != "pgn-folder":
        split_games = list(_split(payload))
        for number, game in enumerate(split_games[:cancel_at - 1], start=1):
            try:
                validate_pgn_game(game)
            except (MalformedPGN, IllegalMove) as error:
                if isinstance(error, IllegalMove):
                    refusal_id = "import-illegal"
                elif number == len(split_games) and str(error) in (
                    "unterminated comment", "unbalanced variation open",
                    "movetext missing terminal result token (truncated?)"
                ):
                    # T0581's EOF reading only applies at the end of input;
                    # earlier unmatched openers use T0570's opener line.
                    refusal_id = "import-truncated"
                else:
                    refusal_id = "import-bad-tags"
                refusal_scenario = next(entry for entry in CONTRACT["scenarios"]["entries"]
                                        if entry["id"] == refusal_id)
                return reference_refusal(payload, store, source_id=source_id,
                                         scenario=refusal_scenario)
    if source_id == "pgn-folder":
        # T0636's illegal-game refusal owns the whole prefix. Detect it
        # before any effects here, then delegate on the original store.
        folder_games = [(game, name) for name, text in payload.items()
                        for game in _split(text)]
        for game, _name in folder_games[:cancel_at - 1]:
            try:
                validate_pgn_game(game)
            except MalformedPGN:
                continue  # T0636 rejects malformed games and keeps going.
            except IllegalMove:
                return reference_folder(payload, store, source_id=source_id)
        games = iter(folder_games)
    else:
        games = ((game, "in.pgn") for game in _split(payload))
    store.telemetry.append("import.started")
    counts = {"games_imported": 0, "games_already_imported": 0, "games_updated": 0}
    file_games = {}
    for number, (game, name) in enumerate(games, start=1):
        if number == cancel_at:
            store.telemetry.append("import.cancelled")
            store.summary = {"kind": SCENARIO["visible_output"], "source_id": source_id,
                             **counts}
            return dict(store.summary)
        if source_id == "pgn-folder":
            file_games[name] = file_games.get(name, 0) + 1
            try:
                validate_pgn_game(game)
            except MalformedPGN:
                # T0636's rejection behavior for this exact game; its
                # scratch run must not finish the overall cancelled import.
                scratch = ReferenceStore()
                reference_folder({name: game}, scratch, source_id=source_id)
                if not hasattr(store, "rejections"):
                    store.rejections = []
                loc = scratch.rejections[0].location
                store.rejections.append(FolderRejection(
                    name, file_games[name], "malformed_request",
                    {"file": name, "game_number": file_games[name], "line": loc["line"]},
                ))
                continue
        start = len(store.telemetry)
        result = reference_import(game, store, source_id=source_id,
                                  retrieval_detail=name, scenario=SCENARIO)
        for field in counts:
            counts[field] += result[field]
        # The successful cancelled run has only started/cancelled. The
        # helper's own events and summary belong to its private call.
        del store.telemetry[start:]
        store.summary = None
    # A request arriving after the last game is not a cancellation. Leave
    # behavior unpinned rather than inventing a completed cancel outcome.
    raise Refusal("no_inflight_game")


PRODUCTION_BINDING = reference_cancel  # T0649 replaces only this line.


def test_contract_scenario_pinned():
    assert SCENARIO == {
        "id": "import-cancel", "chain_task": "T0647",
        "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
        "visible_output": "cancelled-summary",
        "persisted_state": ["committed-games-only"],
        "telemetry": ["import.started", "import.cancelled"],
        "error_codes": [],
    }
    assert CONTRACT["atomicity"]["commit_boundary"] == "per-game"
    assert CONTRACT["atomicity"]["partial_game"] == "never-visible"
    assert CONTRACT["atomicity"]["cancel"] == "committed-stay-in-flight-discarded"


def _probe(binding, source_id="pgn-multi", cancel_at=2):
    store = ReferenceStore()
    result = binding(_input(source_id), store, source_id=source_id, cancel_at=cancel_at)
    assert result == {"kind": "cancelled-summary", "source_id": source_id,
                      "games_imported": cancel_at - 1,
                      "games_already_imported": 0, "games_updated": 0}
    assert store.summary == result
    assert store.telemetry == SCENARIO["telemetry"]
    assert len(store.index) == cancel_at - 1
    assert len({entry["game_id"] for entry in store.index}) == cancel_at - 1
    assert [store.records[e["record"]]["tags"]["Round"] for e in store.index] == [
        str(n) for n in range(1, cancel_at)]
    assert all(list(e) == ["game_id", "record"] for e in store.index)
    for entry in store.index:
        record = store.records[entry["record"]]
        assert list(record) == CONTRACT["record"]["fields"]
        assert list(record["provenance"]) == CONTRACT["record"]["provenance_fields"]
        assert record["source_id"] == record["provenance"]["source_id"] == source_id
        assert record["provenance"]["rights_class"] == "user-own"
        assert entry["record"] == (
            f'{record["game_id"]}--{record["content_sha256"][:16]}.json')
    assert set(store.records) == {e["record"] for e in store.index}
    return result, store


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
@pytest.mark.parametrize("cancel_at", [1, 2, 3])
def test_cancel_keeps_only_games_committed_before_inflight(source_id, cancel_at):
    _probe(PRODUCTION_BINDING, source_id, cancel_at)


def test_folder_cancel_in_second_file_keeps_first_file_games():
    result, store = _probe(PRODUCTION_BINDING, "pgn-folder", 3)
    assert result["games_imported"] == 2
    assert all(store.records[e["record"]]["provenance"]["retrieval_detail"] == "a.pgn"
               for e in store.index)


@pytest.mark.parametrize("failure", ["illegal", "malformed"])
def test_cancel_does_not_validate_inflight_file_game(failure):
    store = ReferenceStore()
    game3 = _game(3)
    if failure == "illegal":
        game3 = game3.replace("2. Nf3 Nc6", "2. Ke3 Nc6")
    else:
        game3 = game3.replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 } e5 1-0")
    payload = _game(1) + "\n" + _game(2) + "\n" + game3
    result = PRODUCTION_BINDING(payload, store, source_id="pgn-multi", cancel_at=3)
    assert result == {"kind": "cancelled-summary", "source_id": "pgn-multi",
                      "games_imported": 2, "games_already_imported": 0,
                      "games_updated": 0}
    assert store.summary == result and store.telemetry == SCENARIO["telemetry"]
    assert [store.records[e["record"]]["tags"]["Round"] for e in store.index] == [
        "1", "2"]
    assert set(store.records) == {e["record"] for e in store.index}
    assert not hasattr(store, "rejections")


def test_cancel_does_not_validate_inflight_folder_game():
    store = ReferenceStore()
    illegal = _game(2).replace("2. Nf3 Nc6", "2. Ke3 Nc6")
    payload = {"a.pgn": _game(1), "b.pgn": illegal}
    result = PRODUCTION_BINDING(payload, store, source_id="pgn-folder", cancel_at=2)
    assert result == {"kind": "cancelled-summary", "source_id": "pgn-folder",
                      "games_imported": 1, "games_already_imported": 0,
                      "games_updated": 0}
    assert store.summary == result and store.telemetry == SCENARIO["telemetry"]
    assert [store.records[e["record"]]["tags"]["Round"] for e in store.index] == ["1"]
    assert set(store.records) == {e["record"] for e in store.index}
    assert "import.completed" not in store.telemetry


def test_rerun_after_cancel_counts_duplicates_not_new_imports():
    store = ReferenceStore()
    first = PRODUCTION_BINDING(_input("pgn-multi"), store, cancel_at=3)
    assert first["games_imported"] == 2
    original_index = [dict(entry) for entry in store.index]
    original_records = set(store.records)
    result = PRODUCTION_BINDING(_input("pgn-multi"), store, cancel_at=3)
    assert result == {"kind": "cancelled-summary", "source_id": "pgn-multi",
                      "games_imported": 0, "games_already_imported": 2,
                      "games_updated": 0}
    assert store.summary == result and store.index == original_index
    assert set(store.records) == original_records
    assert store.telemetry == 2 * SCENARIO["telemetry"]


def _probe_duplicate_input(binding):
    store = ReferenceStore()
    payload = _game(1) + "\n" + _game(1) + "\n" + _game(3)
    result = binding(payload, store, cancel_at=3)
    assert result == {"kind": "cancelled-summary", "source_id": "pgn-multi",
                      "games_imported": 1, "games_already_imported": 1,
                      "games_updated": 0}
    assert store.summary == result and store.telemetry == SCENARIO["telemetry"]
    assert len(store.index) == len(store.records) == 1
    assert store.records[store.index[0]["record"]]["tags"]["Round"] == "1"


def test_duplicate_in_input_counts_one_import_and_one_already_imported():
    _probe_duplicate_input(PRODUCTION_BINDING)


def _probe_updated_content_before_cancel(binding):
    store = ReferenceStore()
    original = _game(1)
    reference_import(original, store, scenario=SCENARIO)
    old_entry = dict(store.index[0])
    seed_records = set(store.records)
    old_content = store.records[old_entry["record"]]["content_sha256"]
    store.telemetry.clear()
    store.summary = None
    changed = original.replace("2. Nf3 Nc6", "2. Nf3 d6")
    assert changed != original
    result = binding(changed + "\n" + _game(2), store, cancel_at=2)
    assert result == {"kind": "cancelled-summary", "source_id": "pgn-multi",
                      "games_imported": 0, "games_already_imported": 0,
                      "games_updated": 1}
    assert store.summary == result and store.telemetry == SCENARIO["telemetry"]
    assert len(store.index) == 1 and store.index[0]["game_id"] == old_entry["game_id"]
    updated = store.records[store.index[0]["record"]]
    assert updated["content_sha256"] != old_content
    assert updated["movetext"] == changed.strip().split("\n\n", 1)[1]
    assert set(store.records) == seed_records | {store.index[0]["record"]}
    assert not any(record["tags"]["Round"] == "2" for record in store.records.values())


def test_updated_content_before_cancel_counts_update():
    _probe_updated_content_before_cancel(PRODUCTION_BINDING)


@pytest.mark.parametrize("source_id", ["pgn-file", "pgn-multi"])
@pytest.mark.parametrize("failure,scenario_id,code,marker", [
    ("stray", "import-bad-tags", "malformed_request", "location"),
    ("unclosed-before-next-game", "import-bad-tags", "malformed_request", "location"),
    ("open-variation-before-next-game", "import-bad-tags", "malformed_request", "location"),
    ("illegal", "import-illegal", "illegal_move", "position"),
])
def test_refusal_before_cancel_uses_landed_refusal_model(
        source_id, failure, scenario_id, code, marker):
    store = ReferenceStore()
    if failure == "stray":
        bad = _game(2).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 } e5 1-0")
    elif failure == "unclosed-before-next-game":
        bad = _game(2).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 {unclosed\n2. Nf3")
    elif failure == "open-variation-before-next-game":
        bad = _game(2).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 (1. d4\n2. Nf3")
    else:
        bad = _game(2).replace("2. Nf3 Nc6", "2. Ke3 Nc6")
    payload = _game(1) + "\n" + bad + "\n" + _game(3)
    result = PRODUCTION_BINDING(payload, store, source_id=source_id, cancel_at=3)
    refusal_scenario = next(entry for entry in CONTRACT["scenarios"]["entries"]
                            if entry["id"] == scenario_id)
    expected = ReferenceStore()
    expected_result = reference_refusal(payload, expected, source_id=source_id,
                                        scenario=refusal_scenario)
    assert result == expected_result and result.code == code
    assert marker in result.marker and result.game_number == 2
    if failure in ("unclosed-before-next-game", "open-variation-before-next-game"):
        # _split separates game 3 at the next blank-line tag block;
        # game 2's opener is line 11, while its last line is 12.
        assert len(list(_split(payload))) == 3
        assert len(bad.splitlines()) == 12
        assert result.marker["location"] == {"game_number": 2, "line": 11}
    assert store.rejections == expected.rejections == [result]
    assert store.telemetry == expected.telemetry == [
        "import.started", "import.game_stored", "import.game_rejected", "import.completed"]
    assert store.index == expected.index and store.records == expected.records
    assert len(store.index) == 1 and store.summary is None
    assert "import.cancelled" not in store.telemetry


@pytest.mark.parametrize("failure,expected_line", [
    ("1. e4 {unclosed\n2. Nf3", 12),
    ("1. e4 (1. d4\n2. Nf3", 12),
])
def test_final_game_unterminated_construct_uses_truncated_eof(failure, expected_line):
    store = ReferenceStore()
    bad = _game(2).replace("1. e4 e5 2. Nf3 Nc6 1-0", failure)
    payload = _game(1) + "\n" + bad
    result = PRODUCTION_BINDING(payload, store, cancel_at=3)
    scenario = next(entry for entry in CONTRACT["scenarios"]["entries"]
                    if entry["id"] == "import-truncated")
    expected = ReferenceStore()
    expected_result = reference_refusal(payload, expected, source_id="pgn-multi",
                                        scenario=scenario)
    assert result == expected_result and result.marker["location"] == {
        "game_number": 2, "line": expected_line}
    assert store.rejections == expected.rejections
    assert store.telemetry == expected.telemetry
    assert store.index == expected.index and store.records == expected.records
    assert store.summary is None


def test_final_game_missing_terminal_result_uses_truncated_refusal():
    store = ReferenceStore()
    bad = _game(2).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 e5\n2. Nf3 Nc6")
    payload = _game(1) + "\n" + bad
    result = PRODUCTION_BINDING(payload, store, cancel_at=3)
    scenario = next(entry for entry in CONTRACT["scenarios"]["entries"]
                    if entry["id"] == "import-truncated")
    expected = ReferenceStore()
    expected_result = reference_refusal(payload, expected, source_id="pgn-multi",
                                        scenario=scenario)
    assert result == expected_result
    assert result.detail == "movetext missing terminal result token (truncated?)"
    assert result.marker["location"] == {"game_number": 2, "line": 12}
    assert store.rejections == expected.rejections
    assert store.telemetry == expected.telemetry
    assert store.index == expected.index and store.records == expected.records
    assert store.summary is None


def test_folder_rejection_before_cancel_does_not_process_later_games():
    store = ReferenceStore()
    bad = _game(1).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 } e5 1-0")
    payload = {"a.pgn": bad, "b.pgn": _game(2) + "\n" + _game(3)}
    result = PRODUCTION_BINDING(payload, store, source_id="pgn-folder", cancel_at=2)
    assert result == {"kind": "cancelled-summary", "source_id": "pgn-folder",
                      "games_imported": 0, "games_already_imported": 0,
                      "games_updated": 0}
    assert store.summary == result
    assert store.index == [] and store.records == {}
    assert store.telemetry[0] == "import.started"
    assert store.telemetry[-1] == "import.cancelled"
    assert "import.completed" not in store.telemetry
    # Whether the folder rejection record/event survives a later cancel
    # remains open; this row deliberately does not inspect either.


def test_folder_illegal_before_cancel_inherits_t0636_refusal():
    store = ReferenceStore()
    bad = _game(1).replace("2. Nf3 Nc6", "2. Ke3 Nc6")
    payload = {"a.pgn": bad, "b.pgn": _game(2)}
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(payload, store, source_id="pgn-folder", cancel_at=2)
    expected = ReferenceStore()
    with pytest.raises(Refusal) as expected_error:
        reference_folder(payload, expected)
    assert error.value.code == expected_error.value.code == "illegal_move"
    assert store.telemetry == expected.telemetry == ["import.started"]
    assert store.index == expected.index == [] and store.records == expected.records == {}
    assert store.summary is None and "import.cancelled" not in store.telemetry


def test_folder_valid_prefix_then_illegal_matches_t0636_effects():
    store = ReferenceStore()
    bad = _game(2).replace("2. Nf3 Nc6", "2. Ke3 Nc6")
    payload = {"a.pgn": _game(1) + "\n" + bad, "b.pgn": _game(3)}
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(payload, store, source_id="pgn-folder", cancel_at=3)
    expected = ReferenceStore()
    with pytest.raises(Refusal) as expected_error:
        reference_folder(payload, expected)
    assert error.value.code == expected_error.value.code == "illegal_move"
    assert store.telemetry == expected.telemetry == ["import.started", "import.game_stored"]
    assert store.index == expected.index and store.records == expected.records
    assert len(store.index) == 1
    assert store.records[store.index[0]["record"]]["tags"]["Round"] == "1"
    assert store.summary == expected.summary is None
    assert "import.cancelled" not in store.telemetry


def test_folder_malformed_then_illegal_delegates_in_t0636_order():
    store = ReferenceStore()
    malformed = _game(1).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 } e5 1-0")
    illegal = _game(2).replace("2. Nf3 Nc6", "2. Ke3 Nc6")
    payload = {"a.pgn": malformed + "\n" + illegal, "b.pgn": _game(3)}
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(payload, store, source_id="pgn-folder", cancel_at=3)
    expected = ReferenceStore()
    with pytest.raises(Refusal) as expected_error:
        reference_folder(payload, expected)
    assert error.value.code == expected_error.value.code == "illegal_move"
    assert store.telemetry == expected.telemetry == ["import.started", "import.game_rejected"]
    assert store.rejections == expected.rejections
    assert store.index == expected.index == [] and store.records == expected.records == {}
    assert store.summary == expected.summary is None
    assert "import.cancelled" not in store.telemetry


def _wrong_kind(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    result["kind"] = "import-summary"
    return result


def _discard_prefix(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    store.index.clear()
    return result


def _commit_inflight(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    source_id = kwargs.get("source_id", "pgn-multi")
    game = _game(kwargs.get("cancel_at", 2))
    reference_import(game, store, source_id=source_id, scenario=SCENARIO)
    return result


def _orphan_inflight_record(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    source_id = kwargs.get("source_id", "pgn-multi")
    scratch = ReferenceStore()
    reference_import(_game(kwargs.get("cancel_at", 2)), scratch,
                     source_id=source_id, scenario=SCENARIO)
    store.records.update(scratch.records)
    return result


def _process_later_game(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    source_id = kwargs.get("source_id", "pgn-multi")
    reference_import(_game(kwargs.get("cancel_at", 2) + 1), store,
                     source_id=source_id, scenario=SCENARIO)
    return result


def _drop_cancel_event(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    store.telemetry.remove("import.cancelled")
    return result


def _fake_count(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    result["games_imported"] += 1
    return result


def _orphan_on_update_mutant(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    store.records["orphan.json"] = {"partial": "in-flight"}
    return result


def test_orphan_on_update_mutant_red():
    _probe_updated_content_before_cancel(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe_updated_content_before_cancel(_orphan_on_update_mutant)


def _ordinal_count_mutant(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    result["games_imported"] = kwargs.get("cancel_at", 2) - 1
    result["games_already_imported"] = result["games_updated"] = 0
    store.summary = dict(result)
    return result


def test_ordinal_count_mutant_red_on_duplicate_input():
    _probe_duplicate_input(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe_duplicate_input(_ordinal_count_mutant)


@pytest.mark.parametrize("mutant", [_wrong_kind, _discard_prefix, _commit_inflight,
                                     _orphan_inflight_record, _process_later_game,
                                     _drop_cancel_event, _fake_count])
def test_black_box_mutants_red(mutant):
    _probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe(mutant)


@pytest.mark.parametrize("folder", [
    {"../evil.pgn": _game(1) + "\n" + _game(2)},
    {"nested/file.pgn": _game(1) + "\n" + _game(2)},
    {"nested\\file.pgn": _game(1) + "\n" + _game(2)},
    {".pgn": _game(1) + "\n" + _game(2)},
    {"..pgn": _game(1) + "\n" + _game(2)},
    {"a.txt": _game(1) + "\n" + _game(2)},
    {"a.pgn": 1},
    {},
    ["a.pgn"],
])
def test_folder_bad_shape_or_path_refused_before_any_effect(folder):
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(folder, store, source_id="pgn-folder", cancel_at=2)
    assert error.value.code == "malformed_request"
    assert store.telemetry == [] and store.index == [] and store.records == {}
    assert store.summary is None and not hasattr(store, "rejections")


def test_out_of_scenario_source_refused_before_any_effect():
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(_input("pgn-multi"), store, source_id="pgn-watch")
    assert error.value.code == "unknown_rights"
    assert store.telemetry == [] and store.index == [] and store.records == {}
    assert store.summary is None
