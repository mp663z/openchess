"""T0548 pgn-multi pure reference battery. T0550 replaces PRODUCTION_BINDING.

Merged executable reading pinned at lane/2 50eb51f:
  ingest/streaming.py@edca013da68f34bfad9325a18a9ced368e5c4a53
  ingest/import_pgn.py@ea0c9f94f8b87107e7a51e82d620e7ecd0d15097
  tests/test_t0537_import_pgn_contract.py@c4ebc6de658210f63f6772613322ed7ac467dcdd
Source identity and per-game atomicity come directly from import.yaml. This model
is not the current import-pgn dispatcher, which refuses pgn-multi by design.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ingest.pgn import IllegalMove, MalformedPGN, validate_pgn_game
from tests.test_t0537_import_pgn_contract import VALID_PGN

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0548")
RETRIEVED_AT = "2026-09-25T00:00:00Z"


class Refusal(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ReferenceStore:
    def __init__(self):
        self.index = []
        self.records = {}
        self.telemetry = []
        self.summary = None


def _split(text):
    """T0537 merged reading: next game starts at newline-newline-[.

    A single newline before the next tag is retained as part of the
    preceding game; the boundary itself removes the two newlines.
    """
    buf = text
    while (idx := buf.find("\n\n[", 1)) != -1:
        game, buf = buf[:idx], buf[idx + 2:]
        if game.strip():
            yield game.strip("\n")
    if buf.strip():
        yield buf.strip("\n")


def _identity(tags, movetext):
    headers = ("Site", "White", "Black", "UTCDate", "Date", "Round")
    if all(tags.get(h, "").strip() for h in headers):
        key = ["headers", [tags[h] for h in headers]]
    else:
        key = ["content", {"tags": {k: tags[k] for k in sorted(tags)}, "movetext": movetext}]
    digest_input = json.dumps(key, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(digest_input).hexdigest()[:16]


def reference_import(text, store, *, source_id="pgn-multi", retrieval_detail="in.pgn",
                     scenario=SCENARIO):
    if source_id not in scenario["sources"]:
        raise Refusal("unknown_rights")
    store.telemetry.append("import.started")
    imported = already = updated = 0
    for game in _split(text):
        try:
            parsed = validate_pgn_game(game)
        except MalformedPGN as exc:
            raise Refusal("malformed_request") from exc
        except IllegalMove as exc:
            raise Refusal("illegal_move") from exc
        record = {
            "game_id": _identity(parsed.tags, parsed.movetext),
            "source_id": source_id,
            "provenance": {"source_id": source_id, "rights_class": "user-own",
                           "retrieved_at": RETRIEVED_AT, "retrieval_detail": retrieval_detail},
            "variant": "standard", "tags": parsed.tags, "movetext": parsed.movetext,
            "imported_at": RETRIEVED_AT,
        }
        canonical = {k: record[k] for k in ("game_id", "source_id", "variant", "tags", "movetext")}
        canonical["tags"] = {k: canonical["tags"][k] for k in sorted(canonical["tags"])}
        record["content_sha256"] = hashlib.sha256(json.dumps(
            canonical, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        record = {k: record[k] for k in CONTRACT["record"]["fields"]}
        old = next((e for e in store.index if e["game_id"] == record["game_id"]), None)
        if old and store.records[old["record"]]["content_sha256"] == record["content_sha256"]:
            already += 1
            store.telemetry.append("import.game_duplicate")
            continue
        filename = f'{record["game_id"]}--{record["content_sha256"][:16]}.json'
        store.records[filename] = record
        entry = {"game_id": record["game_id"], "record": filename}
        if old:
            store.index[store.index.index(old)] = entry
            updated += 1
            store.telemetry.append("import.game_updated")
        else:
            store.index.append(entry)
            imported += 1
            store.telemetry.append("import.game_stored")
    if imported + already + updated == 0:
        raise Refusal("malformed_request")
    store.telemetry.append("import.completed")
    store.summary = {"kind": "import-summary", "source_id": source_id,
                     "games_imported": imported, "games_already_imported": already,
                     "games_updated": updated}
    return copy.deepcopy(store.summary)


# T0550 implementation task swaps only this binding, preserving row assertions.
PRODUCTION_BINDING = reference_import


def _other():
    return VALID_PGN.replace('"a"', '"c"').replace('"b"', '"d"')


def test_pinned_scenario_and_source():
    assert SCENARIO == {"id": "import-multi-pgn", "chain_task": "T0548",
                        "sources": ["pgn-multi"], "visible_output": "import-summary",
                        "persisted_state": ["game-records", "provenance", "index-entries"],
                        "telemetry": ["import.started", "import.game_stored", "import.completed"],
                        "error_codes": ["malformed_request"]}
    source = next(e for e in CONTRACT["sources"]["entries"] if e["id"] == "pgn-multi")
    assert source == {"id": "pgn-multi", "kind": "user-file", "rights_class": "user-own"}
    assert CONTRACT["atomicity"]["commit_boundary"] == "per-game"
    assert CONTRACT["atomicity"]["partial_game"] == "never-visible"


def test_two_games_have_exact_summary_records_index_and_telemetry():
    store = ReferenceStore()
    result = PRODUCTION_BINDING(VALID_PGN + "\n" + _other(), store)
    assert result == {"kind": "import-summary", "source_id": "pgn-multi",
                      "games_imported": 2, "games_already_imported": 0, "games_updated": 0}
    assert store.summary == result
    assert store.telemetry == [
        "import.started", "import.game_stored", "import.game_stored", "import.completed"]
    assert len(store.index) == len({e["game_id"] for e in store.index}) == 2
    assert all(list(e) == ["game_id", "record"] for e in store.index)
    for entry in store.index:
        rec = store.records[entry["record"]]
        assert list(rec) == CONTRACT["record"]["fields"]
        assert list(rec["provenance"]) == CONTRACT["record"]["provenance_fields"]
        assert rec["source_id"] == rec["provenance"]["source_id"] == "pgn-multi"
        assert rec["provenance"]["rights_class"] == "user-own"
        assert rec["game_id"] == entry["game_id"]


def test_duplicate_in_same_file_is_visible_noop():
    store = ReferenceStore()
    result = PRODUCTION_BINDING(VALID_PGN + "\n" + VALID_PGN, store)
    assert len(store.index) == 1
    assert result["games_imported"] == result["games_already_imported"] == 1
    assert store.telemetry == [
        "import.started", "import.game_stored", "import.game_duplicate", "import.completed"]


def test_later_malformed_game_keeps_committed_prefix_without_partial_game():
    store = ReferenceStore()
    bad = '[Event "bad"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3\n'
    with pytest.raises(Refusal) as err:
        PRODUCTION_BINDING(VALID_PGN + "\n" + bad, store)
    assert err.value.code == "malformed_request"
    assert len(store.index) == 1
    assert len(store.records) == 1
    assert store.summary is None
    assert store.telemetry == ["import.started", "import.game_stored"]


def test_malformed_first_game_keeps_no_partial_record():
    store = ReferenceStore()
    with pytest.raises(Refusal) as err:
        PRODUCTION_BINDING('[Event "bad"]\n\n1. e4 e5\n', store)
    assert err.value.code == "malformed_request"
    assert store.index == [] and store.records == {} and store.summary is None
    assert store.telemetry == ["import.started"]


def test_wrong_source_refused_before_any_effect():
    store = ReferenceStore()
    with pytest.raises(Refusal) as err:
        PRODUCTION_BINDING(VALID_PGN, store, source_id="pgn-file")
    assert err.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []


def test_splitter_merged_boundary_and_final_buffer():
    first, second = list(_split(VALID_PGN + "\n" + _other()))
    assert validate_pgn_game(first).tags["White"] == "a"
    assert validate_pgn_game(second).tags["White"] == "c"
    # T0537's splitter does not split at a single newline before a tag.
    assert len(list(_split(VALID_PGN.rstrip() + "\n" + _other()))) == 1


def test_two_games_content_hash_covers_canonical_fields():
    store = ReferenceStore()
    PRODUCTION_BINDING(VALID_PGN + "\n" + _other(), store)
    for entry in store.index:
        record = store.records[entry["record"]]
        canonical = {k: record[k] for k in ("game_id", "source_id", "variant", "tags", "movetext")}
        canonical["tags"] = {k: canonical["tags"][k] for k in sorted(canonical["tags"])}
        digest = hashlib.sha256(json.dumps(
            canonical, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        assert record["content_sha256"] == digest
        assert entry["record"] == f'{record["game_id"]}--{digest[:16]}.json'


def _probe_two_games(binding):
    store = ReferenceStore()
    result = binding(VALID_PGN + "\n" + _other(), store)
    assert result["source_id"] == "pgn-multi" and result["games_imported"] == 2
    assert store.telemetry == [
        "import.started", "import.game_stored", "import.game_stored", "import.completed"]
    assert len(store.index) == 2
    assert all(store.records[e["record"]]["provenance"]["source_id"] == "pgn-multi"
               for e in store.index)


def _probe_later_malformed(binding):
    store = ReferenceStore()
    text = VALID_PGN + '\n[Event "bad"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3\n'
    with pytest.raises(Refusal):
        binding(text, store)
    assert len(store.index) == 1 and store.summary is None
    assert store.telemetry == ["import.started", "import.game_stored"]


def _wrong_source_mutant(text, store, **kw):
    result = reference_import(text, store, **kw)
    for entry in store.index:
        store.records[entry["record"]]["provenance"]["source_id"] = "pgn-file"
    return result


def _wrong_count_mutant(text, store, **kw):
    result = reference_import(text, store, **kw)
    result["games_imported"] = 1
    return result


def _missing_event_mutant(text, store, **kw):
    result = reference_import(text, store, **kw)
    store.telemetry.remove("import.game_stored")
    return result


def _first_game_only_mutant(text, store, **kw):
    return reference_import(next(_split(text)), store, **kw)


def _rollback_prefix_mutant(text, store, **kw):
    try:
        return reference_import(text, store, **kw)
    except Refusal:
        store.index.clear()
        raise


MUTANTS = {
    "wrong-provenance": (_wrong_source_mutant, _probe_two_games),
    "wrong-count": (_wrong_count_mutant, _probe_two_games),
    "missing-per-game-event": (_missing_event_mutant, _probe_two_games),
    "first-game-only": (_first_game_only_mutant, _probe_two_games),
    "rollback-committed-prefix": (_rollback_prefix_mutant, _probe_later_malformed),
}


@pytest.mark.parametrize("name", MUTANTS)
def test_black_box_mutant_is_red(name):
    _probe = MUTANTS[name][1]
    _probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe(MUTANTS[name][0])
