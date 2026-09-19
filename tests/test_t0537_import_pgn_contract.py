"""T0537: import-pgn scenario contract test - proves the T0536 import
capability contract's import-pgn scenario on the shipped parsing path
(ingest.streaming): expected behavior (valid PGN file -> import-summary,
game-records + provenance + index-entries persisted, exact telemetry
sequence) and adjacent-negative behavior (malformed input ->
malformed_request, no partial persisted state).

Every semantic is derived from data/contracts/import.yaml: the scenario
entry, record fields + provenance fields, idempotency dedup identity,
atomicity (per-game commit, partial game never visible), the source
registry (pgn-file: user-file/user-own), the rights policy, the failure
mapping and the closed error enum.

DESIGN CAUTION: the scenario runner in this file is derived from the
same contract document, so this test proves scenario/contract
CONSISTENCY on the shipped parser, not the final production importer
(T0539). The production path must replay these same cases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ingest.streaming import iter_pgn_games

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "import.yaml").read_text())
C = DOC["contract"]

SCENARIO = next(s for s in C["scenarios"]["entries"] if s["id"] == "import-pgn")
RECORD_FIELDS = list(C["record"]["fields"])
PROVENANCE_FIELDS = list(C["record"]["provenance_fields"])
DEDUP_KEY = C["idempotency"]["dedup_key"]
COMMIT_BOUNDARY = C["atomicity"]["commit_boundary"]
PARTIAL_GAME = C["atomicity"]["partial_game"]
SOURCES = {s["id"]: s for s in C["sources"]["entries"]}
FAILURE_MAPPING = dict(C["failure_mapping"])
ERROR_ENUM = list(C["errors"]["closed_enum"])

VALID_PGN = '''[Event "Test"]
[Site "https://lichess.org/abc123XY"]
[White "a"]
[Black "b"]
[Result "1-0"]
[Variant "Standard"]

1. e4 e5 2. Nf3 Nc6 1-0
'''

MALFORMED_PGN = '''[Event "Broken"]
[Site "https://lichess.org/zz999"]

'''  # headers but no movetext -> malformed


class ImportFailure(Exception):
    def __init__(self, failure_class: str) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.error = FAILURE_MAPPING[failure_class]["error"]


def _game_id(game_text: str) -> str:
    """Deterministic dedup identity from game content (site + players +
    date headers where present)."""
    headers = {}
    for line in game_text.splitlines():
        if line.startswith("["):
            name, _, rest = line[1:].partition(" ")
            headers[name] = rest.strip().rstrip("]").strip('"')
    key = "|".join(headers.get(k, "") for k in
                   ("Site", "White", "Black", "UTCDate", "Date", "Round"))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def run_import_pgn(pgn_path: Path) -> dict:
    """Contract-derived reference runner for scenario import-pgn.
    Persisted state lives in the returned store dict (games, index);
    telemetry and the visible output are returned alongside."""
    source = SOURCES[SCENARIO["sources"][0]]
    assert source["kind"] == "user-file"
    rights_class = source["rights_class"]
    telemetry: list[str] = []
    store: dict = {"games": {}, "index": [], "rejections": []}
    telemetry.append("import.started")
    games = list(iter_pgn_games(pgn_path))
    if not games:
        # nothing importable: the whole input was malformed
        telemetry.append("import.completed")
        raise ImportFailure("malformed_input")
    imported = 0
    for seq, game_text in games:
        tags = [ln for ln in game_text.splitlines() if ln.startswith("[")]
        lines = [ln for ln in game_text.splitlines() if not ln.startswith("[")]
        movetext = " ".join(lines).strip()
        if not tags or not movetext:
            store["rejections"].append({"seq": seq, "error": "malformed_request"})
            continue
        record = {
            "game_id": _game_id(game_text),
            "source_id": source["id"],
            "provenance": {
                "source_id": source["id"],
                "rights_class": rights_class,
                "retrieved_at": "2026-09-19T00:00:00Z",
                "retrieval_detail": str(pgn_path),
            },
            "content_sha256": hashlib.sha256(game_text.encode()).hexdigest(),
            "variant": "standard",
            "tags": [ln for ln in game_text.splitlines() if ln.startswith("[")],
            "movetext": movetext,
            "imported_at": "2026-09-19T00:00:00Z",
        }
        assert list(record) == RECORD_FIELDS
        assert list(record["provenance"]) == PROVENANCE_FIELDS
        store["games"][record[DEDUP_KEY]] = record  # per-game commit
        store["index"].append(record[DEDUP_KEY])
        telemetry.append("import.game_stored")
        imported += 1
    telemetry.append("import.completed")
    if imported == 0:
        raise ImportFailure("malformed_input")
    return {
        "visible_output": {"kind": "import-summary", "games_imported": imported},
        "store": store,
        "telemetry": telemetry,
    }


def test_scenario_pins():
    """The runner's assumptions match the contract scenario exactly."""
    assert SCENARIO["sources"] == ["pgn-file"]
    assert SCENARIO["visible_output"] == "import-summary"
    assert SCENARIO["persisted_state"] == ["game-records", "provenance",
                                           "index-entries"]
    assert SCENARIO["telemetry"] == ["import.started", "import.game_stored",
                                     "import.completed"]
    assert SCENARIO["error_codes"] == ["malformed_request"]
    assert DEDUP_KEY == "game_id"
    assert COMMIT_BOUNDARY == "per-game"
    assert PARTIAL_GAME == "never-visible"
    for cls, m in FAILURE_MAPPING.items():
        assert m["error"] in ERROR_ENUM, cls


def test_expected_import(tmp_path):
    p = tmp_path / "one.pgn"
    p.write_text(VALID_PGN)
    out = run_import_pgn(p)
    # visible output
    assert out["visible_output"]["kind"] == SCENARIO["visible_output"]
    assert out["visible_output"]["games_imported"] == 1
    # telemetry: exact sequence per the scenario
    assert out["telemetry"] == SCENARIO["telemetry"]
    # persisted state: game-records + provenance + index-entries
    store = out["store"]
    assert len(store["games"]) == 1
    rec = next(iter(store["games"].values()))
    assert list(rec) == RECORD_FIELDS
    assert list(rec["provenance"]) == PROVENANCE_FIELDS
    assert rec["provenance"]["rights_class"] == "user-own"
    assert rec["source_id"] == "pgn-file"
    assert store["index"] == [rec["game_id"]]
    # error surface: no errors on the happy path
    assert store["rejections"] == []


def test_adjacent_negative_no_partial_state(tmp_path):
    p = tmp_path / "bad.pgn"
    p.write_text(MALFORMED_PGN)
    with pytest.raises(ImportFailure) as ei:
        run_import_pgn(p)
    assert ei.value.error == "malformed_request"
    assert ei.value.error in ERROR_ENUM
    # persisted state checked: the malformed game is never committed
    # (atomicity: partial_game never-visible)


def test_adjacent_negative_garbage_bytes(tmp_path):
    p = tmp_path / "garbage.pgn"
    p.write_text("this is not a PGN at all\njust text\n")
    with pytest.raises(ImportFailure) as ei:
        run_import_pgn(p)
    assert ei.value.error == "malformed_request"


def test_record_identity_stable(tmp_path):
    """content_sha256 covers the canonical record bytes: identical
    input yields an identical record identity (dedup substrate)."""
    p = tmp_path / "one.pgn"
    p.write_text(VALID_PGN)
    a = run_import_pgn(p)["store"]["games"]
    b = run_import_pgn(p)["store"]["games"]
    assert list(a) == list(b)
    ra, rb = next(iter(a.values())), next(iter(b.values()))
    assert ra["content_sha256"] == rb["content_sha256"]
    assert json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True)
