"""T0636 folder import reference battery; T0638 swaps only the binding."""
from __future__ import annotations

from dataclasses import dataclass
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

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0636")


@dataclass(frozen=True)
class FolderRejection:
    file: str
    game_number: int
    code: str
    location: dict


def _valid(round_number):
    return FULL_HEADER_PGN.replace('[Round "1"]', f'[Round "{round_number}"]')


def _bad(round_number):
    return _valid(round_number).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 {unclosed")


def _files():
    # The fixture order is controlled here; import.yaml does not define
    # filesystem iteration order, so we do not assert an external sort rule.
    return {"a.pgn": _valid(1) + "\n" + _bad(2) + "\n" + _valid(3),
            "b.pgn": _valid(4) + "\n" + _bad(5),
            "c.pgn": _valid(6)}


def _counts(kind="import-summary"):
    return {"kind": kind, "source_id": "pgn-folder", "games_imported": 0,
            "games_already_imported": 0, "games_updated": 0}


def reference_folder(files, store, *, source_id="pgn-folder"):
    if source_id not in SCENARIO["sources"]:
        raise Refusal("unknown_rights")
    if type(files) is not dict or not files or any(
        type(path) is not str or not path.endswith(".pgn")
        or path in (".pgn", "..pgn") or "/" in path or "\\" in path
        or type(text) is not str for path, text in files.items()
    ):
        raise Refusal("malformed_request")
    store.telemetry.append("import.started")
    store.rejections = []
    per_file = []
    total = _counts()
    for path, text in files.items():
        count = _counts()
        for number, game in enumerate(_split(text), start=1):
            try:
                validate_pgn_game(game)
            except MalformedPGN:
                # T0570 locates malformed content within the game. Use the
                # same reference on a private scratch store; no bad game is
                # ever handed to the record importer.
                scratch = ReferenceStore()
                rejection = reference_refusal(game, scratch, source_id=source_id, scenario=SCENARIO)
                loc = rejection.marker["location"]
                store.rejections.append(FolderRejection(
                    path, number, "malformed_request",
                    {"file": path, "game_number": number, "line": loc["line"]},
                ))
                store.telemetry.append("import.game_rejected")
                continue
            except IllegalMove:
                # import.yaml's folder scenario does not list illegal_move.
                # Do not infer its disposition from the malformed-only row.
                raise Refusal("illegal_move") from None
            before = len(store.telemetry)
            result = reference_import(game, store, source_id=source_id,
                                      retrieval_detail=path, scenario=SCENARIO)
            store.telemetry[before:] = (["import.game_stored"] if result["games_imported"] else [])
            store.summary = None
            for field in ("games_imported", "games_already_imported", "games_updated"):
                count[field] += result[field]
                total[field] += result[field]
        store.telemetry.append("import.file_completed")
        per_file.append({"file": path, **count})
    store.telemetry.append("import.completed")
    store.summary = {"kind": SCENARIO["visible_output"], "files": per_file, "total": total}
    return store.summary


PRODUCTION_BINDING = reference_folder  # T0638 replaces only this binding.


def test_scenario_pinned():
    assert SCENARIO == {
        "id": "import-folder", "chain_task": "T0636", "sources": ["pgn-folder"],
        "visible_output": "per-file-and-total-summary",
        "persisted_state": ["game-records", "provenance", "index-entries", "rejection-records"],
        "telemetry": ["import.started", "import.file_completed", "import.game_stored",
                      "import.game_rejected", "import.completed"],
        "error_codes": ["malformed_request"],
    }
    assert CONTRACT["atomicity"]["commit_boundary"] == "per-game"
    assert CONTRACT["atomicity"]["partial_game"] == "never-visible"


def _probe(binding):
    store = ReferenceStore()
    result = binding(_files(), store)
    expected_files = [{"file": "a.pgn", **_counts(), "games_imported": 2},
                      {"file": "b.pgn", **_counts(), "games_imported": 1},
                      {"file": "c.pgn", **_counts(), "games_imported": 1}]
    assert result == {"kind": "per-file-and-total-summary", "files": expected_files,
                      "total": {**_counts(), "games_imported": 4}}
    assert store.summary == result
    for field in ("games_imported", "games_already_imported", "games_updated"):
        assert result["total"][field] == sum(f[field] for f in result["files"])
    assert len(store.index) == 4 and len({e["game_id"] for e in store.index}) == 4
    assert set(store.records) == {e["record"] for e in store.index}
    assert [store.records[e["record"]]["tags"]["Round"]
            for e in store.index] == ["1", "3", "4", "6"]
    assert [store.records[e["record"]]["provenance"]["retrieval_detail"]
            for e in store.index] == ["a.pgn", "a.pgn", "b.pgn", "c.pgn"]
    for entry in store.index:
        rec = store.records[entry["record"]]
        assert list(rec) == CONTRACT["record"]["fields"]
        assert list(rec["provenance"]) == CONTRACT["record"]["provenance_fields"]
        assert rec["source_id"] == rec["provenance"]["source_id"] == "pgn-folder"
        assert rec["provenance"]["rights_class"] == "user-own"
        assert entry == {"game_id": rec["game_id"],
                         "record": f'{rec["game_id"]}--{rec["content_sha256"][:16]}.json'}
    assert store.rejections == [
        FolderRejection("a.pgn", 2, "malformed_request",
                        {"file": "a.pgn", "game_number": 2, "line": 11}),
        FolderRejection("b.pgn", 2, "malformed_request",
                        {"file": "b.pgn", "game_number": 2, "line": 11}),
    ]
    assert store.telemetry == ["import.started", "import.game_stored",
                               "import.game_rejected", "import.game_stored",
                               "import.file_completed", "import.game_stored",
                               "import.game_rejected", "import.file_completed",
                               "import.game_stored", "import.file_completed", "import.completed"]


def test_folder_mixed_good_bad_games_and_two_file_totals():
    _probe(PRODUCTION_BINDING)


def test_multiline_unclosed_comment_uses_opener_line_in_second_file():
    files = {"a.pgn": _valid(1),
             "b.pgn": _valid(2) + "\n" + _bad(3).replace(
                 "1. e4 {unclosed", "1. e4 {unclosed\nmore comment text") + "\n" + _valid(4)}
    store = ReferenceStore()
    result = PRODUCTION_BINDING(files, store)
    assert [(r.file, r.game_number, r.code, r.location)
            for r in store.rejections] == [
        ("b.pgn", 2, "malformed_request",
         {"file": "b.pgn", "game_number": 2, "line": 11})]
    assert [store.records[e["record"]]["tags"]["Round"]
            for e in store.index] == ["1", "2", "4"]
    assert [f["games_imported"] for f in result["files"]] == [1, 2]
    assert result["total"]["games_imported"] == 3
    assert store.telemetry == ["import.started", "import.game_stored",
                               "import.file_completed", "import.game_stored",
                               "import.game_rejected", "import.game_stored",
                               "import.file_completed", "import.completed"]


def test_semicolon_inside_variation_keeps_later_unclosed_comment_location():
    bad = _valid(3).replace("1. e4 e5 2. Nf3 Nc6 1-0",
                            "1. e4 (1. d4 ; x) {unclosed\ne5 1-0")
    files = {"a.pgn": _valid(1),
             "b.pgn": _valid(2) + "\n" + bad + "\n" + _valid(4)}
    store = ReferenceStore()
    result = PRODUCTION_BINDING(files, store)
    assert store.rejections == [FolderRejection(
        "b.pgn", 2, "malformed_request",
        {"file": "b.pgn", "game_number": 2, "line": 11})]
    assert [store.records[e["record"]]["tags"]["Round"]
            for e in store.index] == ["1", "2", "4"]
    assert result["total"]["games_imported"] == 3
    assert store.telemetry.count("import.game_rejected") == 1
    assert store.telemetry[-1] == "import.completed"


def _probe_cross_file_identity(binding, updated):
    first = _valid(1)
    second = first.replace("2. Nf3 Nc6", "2. Nf3 d6") if updated else first
    store = ReferenceStore()
    result = binding({"a.pgn": first, "b.pgn": second}, store)
    first_count = {"file": "a.pgn", **_counts(), "games_imported": 1}
    second_count = {"file": "b.pgn", **_counts(),
                    "games_updated" if updated else "games_already_imported": 1}
    total_count = {**_counts(), "games_imported": 1,
                   "games_updated" if updated else "games_already_imported": 1}
    assert result == {"kind": "per-file-and-total-summary",
                      "files": [first_count, second_count], "total": total_count}
    assert store.summary == result and store.rejections == []
    assert len(store.index) == len({e["game_id"] for e in store.index}) == 1
    entry = store.index[0]
    record = store.records[entry["record"]]
    assert record["movetext"] == second.strip().split("\n\n", 1)[1]
    assert record["provenance"]["retrieval_detail"] == ("b.pgn" if updated else "a.pgn")
    assert len(store.records) == (2 if updated else 1)
    assert store.telemetry[0] == "import.started" and store.telemetry[-1] == "import.completed"
    assert store.telemetry.count("import.file_completed") == 2
    assert store.telemetry.count("import.game_stored") == 1
    assert set(store.telemetry) <= set(SCENARIO["telemetry"])
    assert not any(event == "import.game_rejected" for event in store.telemetry)
    return result, store


@pytest.mark.parametrize("updated", [False, True])
def test_duplicate_and_update_across_files_have_distinct_counts_without_false_store(updated):
    _probe_cross_file_identity(PRODUCTION_BINDING, updated)


def _drop_rejection(files, store, **kwargs):
    result = reference_folder(files, store, **kwargs)
    store.rejections.clear()
    return result


def _wrong_total(files, store, **kwargs):
    result = reference_folder(files, store, **kwargs)
    result["total"]["games_imported"] += 1
    return result


def _store_bad_game(files, store, **kwargs):
    result = reference_folder(files, store, **kwargs)
    store.index.append(store.index[0].copy())
    return result


def _drop_file_completed(files, store, **kwargs):
    result = reference_folder(files, store, **kwargs)
    store.telemetry.remove("import.file_completed")
    return result


def _wrong_location(files, store, **kwargs):
    result = reference_folder(files, store, **kwargs)
    old = store.rejections[0]
    store.rejections[0] = FolderRejection(old.file, old.game_number, old.code,
                                         {**old.location, "line": 1})
    return result


@pytest.mark.parametrize("mutant", [_drop_rejection, _wrong_total, _store_bad_game,
                                     _drop_file_completed, _wrong_location])
def test_black_box_mutants_red(mutant):
    _probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe(mutant)


@pytest.mark.parametrize("source_id,files,code", [
    ("pgn-file", {"a.pgn": _valid(1)}, "unknown_rights"),
    ("pgn-folder", {"readme.txt": "hello"}, "malformed_request"),
    ("pgn-folder", {"sub/a.pgn": _valid(1)}, "malformed_request"),
    ("pgn-folder", {"sub\\a.pgn": _valid(1)}, "malformed_request"),
    ("pgn-folder", "not a folder", "malformed_request"),
    ("pgn-folder", {"a.pgn": _valid(1), "notes.txt": "x"}, "malformed_request"),
])
def test_wrong_source_and_non_pgn_or_nested_paths_refused_before_state(source_id, files, code):
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(files, store, source_id=source_id)
    assert error.value.code == code
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None and not hasattr(store, "rejections")
