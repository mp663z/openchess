"""Shipped import-pgn scenario runner (T0536 import capability contract).

This is the real import path the T0537 contract test drives - not a
test helper. It imports a PGN file for a registry source, validating
every game as real chess (ingest.pgn), persisting externally
inspectable state under the given store root, and emitting exactly the
scenario's pinned telemetry sequence.

Contract surface (data/contracts/import.yaml, scenario import-pgn):
- visible output: an import-summary with a CLOSED key set
- persisted state: game-records (one JSON per game, exact record field
  set/order), provenance (inside each record, exact field set/order),
  index-entries (index.json, one entry per stored game, dedup-identity
  order)
- telemetry: import.started, import.game_stored (per newly stored
  game), import.game_duplicate (per already-imported no-op),
  import.game_updated (per recorded replacement), import.completed -
  emitted and persisted to telemetry.jsonl
- errors: malformed_request for malformed input, illegal_move for
  rule-violating movetext, unknown_rights for unverifiable sources
  (error shape per contract: code/message/retryable)
- idempotency: dedup identity game_id. Same id + same content hash is a
  visible already-imported no-op; same id + different hash is a
  recorded replacement; index membership stays unique - no import ever
  creates two index entries for one game_id.
- atomicity: the per-game COMMIT POINT is the single atomic rename of
  index.json (full new index content); the record file is renamed in
  first but only becomes visible once indexed. Every crash window reads
  as pre-commit or fully committed: a stale .tmp or an unindexed
  ("orphan") record is removed by recovery at the start of the next
  run, before any new telemetry. Telemetry is an event log appended
  after the commit point, never the source of truth for state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from ingest.pgn import IllegalMove, MalformedPGN, validate_pgn_game
from ingest.streaming import iter_pgn_games

ROOT = Path(__file__).resolve().parents[1]
IMPORT_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "import.yaml").read_text())
RIGHTS_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "rights_policy.yaml").read_text())
CONTRACT = IMPORT_DOC["contract"]
RIGHTS = RIGHTS_DOC["rights_policy"]

RECORD_FIELDS = list(CONTRACT["record"]["fields"])
PROVENANCE_FIELDS = list(CONTRACT["record"]["provenance_fields"])
SOURCES = {s["id"]: s for s in CONTRACT["sources"]["entries"]}
FAILURE_MAPPING = dict(CONTRACT["failure_mapping"])
SUMMARY_KIND = next(
    s for s in CONTRACT["scenarios"]["entries"] if s["id"] == "import-pgn"
)["visible_output"]


class ImportFailure(Exception):
    """Contract-shaped import error (code/message/retryable)."""

    def __init__(self, failure_class: str, detail: str) -> None:
        mapping = FAILURE_MAPPING[failure_class]
        self.code: str = mapping["error"]
        self.retryable = False
        super().__init__(f"{self.code}: {detail}")
        self.message = f"{self.code}: {detail}"

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def game_identity(tags: dict[str, str]) -> str:
    """Deterministic dedup identity (contract dedup_key game_id) from
    identity headers; never position-derived."""
    key = "|".join(tags.get(k, "") for k in
                   ("Site", "White", "Black", "UTCDate", "Date", "Round"))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _atomic_write(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    os.replace(tmp, path)


def run_import_pgn(pgn_path: str | Path, store_root: str | Path,
                   source_id: str = "pgn-file", *,
                   retrieved_at: str) -> dict:
    """Import one PGN file. Returns the import-summary (visible output).
    Persisted state and telemetry live under store_root; failures raise
    ImportFailure with the contract error code and leave no partial
    state for the failed game."""
    pgn_path = Path(pgn_path)
    store_root = Path(store_root)
    source = SOURCES.get(source_id)
    if source is None:
        raise ImportFailure("unknown_source", f"source id {source_id!r} not in registry")
    rights_class = source["rights_class"]
    if rights_class not in RIGHTS["classes"]:
        raise ImportFailure("unknown_rights", f"unverified rights class {rights_class!r}")

    games_dir = store_root / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path = store_root / "telemetry.jsonl"
    index_path = store_root / "index.json"

    telemetry: list[str] = []

    def emit(event: str) -> None:
        telemetry.append(event)
        with telemetry_path.open("a") as fh:
            fh.write(json.dumps({"event": event}) + "\n")

    index: list[str] = _recover_store(store_root, games_dir, index_path)
    index_set = set(index)
    emit("import.started")
    imported = already = updated = 0
    for _seq, game_text in iter_pgn_games(pgn_path):
        try:
            parsed = validate_pgn_game(game_text)
        except MalformedPGN as e:
            raise ImportFailure("malformed_input", str(e)) from e
        except IllegalMove as e:
            raise ImportFailure("illegal_movetext", str(e)) from e
        record = {
            "game_id": game_identity(parsed.tags),
            "source_id": source["id"],
            "provenance": {
                "source_id": source["id"],
                "rights_class": rights_class,
                "retrieved_at": retrieved_at,
                "retrieval_detail": str(pgn_path),
            },
            "content_sha256": hashlib.sha256(game_text.encode()).hexdigest(),
            "variant": "standard",
            "tags": parsed.tags,
            "movetext": parsed.movetext,
            "imported_at": retrieved_at,
        }
        assert list(record) == RECORD_FIELDS
        assert list(record["provenance"]) == PROVENANCE_FIELDS
        gid = record["game_id"]
        record_path = games_dir / f"{gid}.json"
        if gid in index_set and record_path.exists():
            existing = json.loads(record_path.read_text())
            if existing.get("content_sha256") == record["content_sha256"]:
                # duplicate: visible already-imported no-op
                emit("import.game_duplicate")
                already += 1
                continue
            # updated: recorded replacement, index membership unchanged
            _commit_game(record, index, games_dir, index_path)
            emit("import.game_updated")
            updated += 1
            continue
        # new game: commit = record rename, then the index rename is the
        # single atomic commit point making the record visible.
        if gid not in index_set:
            index = index + [gid]
            index_set.add(gid)
        _commit_game(record, index, games_dir, index_path)
        emit("import.game_stored")
        imported += 1
    if imported + already + updated == 0:
        raise ImportFailure("malformed_input", "no importable games in input")
    emit("import.completed")
    summary = {
        "kind": SUMMARY_KIND,
        "source_id": source["id"],
        "games_imported": imported,
        "games_already_imported": already,
        "games_updated": updated,
    }
    _atomic_write(store_root / "summary.json", json.dumps(summary, indent=1))
    return summary


def _commit_game(record: dict, index: list[str], games_dir: Path,
                 index_path: Path) -> None:
    """One game commit. The index rename is the atomic commit point: the
    record is visible exactly when its id is in the committed index."""
    _atomic_write(games_dir / f"{record['game_id']}.json",
                  json.dumps(record, indent=1))
    _atomic_write(index_path, json.dumps(index, indent=1))


def _recover_store(store_root: Path, games_dir: Path, index_path: Path) -> list[str]:
    """Crash recovery, run BEFORE any new telemetry: remove stale .tmp
    artifacts and orphan records so every prior crash window reads as
    pre-commit or fully committed. Returns the committed index."""
    for tmp in list(store_root.glob("*.tmp")) + list(games_dir.glob("*.tmp")):
        tmp.unlink()
    index: list[str] = []
    if index_path.exists():
        index = json.loads(index_path.read_text())
    wanted = set(index)
    for rec in games_dir.glob("*.json"):
        if rec.stem not in wanted:
            rec.unlink()  # orphan: renamed but never committed to the index
    return index
