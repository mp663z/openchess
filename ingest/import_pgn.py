"""Shipped import-pgn scenario runner (T0536 import capability contract).

This is the real import path the T0537 contract test drives - not a
test helper. It imports a PGN file for the scenario's pinned source,
validating every game as real chess (ingest.pgn), persisting externally
inspectable state under the given store root, and emitting exactly the
scenario's pinned telemetry sequence.

Contract surface (data/contracts/import.yaml, scenario import-pgn):
- sources: exactly the scenario's structured source list; every other
  source id (registry or not) is refused at intake BEFORE any store
  state exists. A rights-class label alone is not intake authorization:
  user-own-only provider data needs an ownership condition this
  scenario does not implement.
- visible output: an import-summary with a CLOSED key set
- persisted state: game-records (content-addressed JSON per game,
  exact record field set/order), provenance (inside each record),
  index-entries (index.json manifest: ordered unique entries mapping
  game_id -> record filename)
- telemetry: import.started, import.game_stored (per newly stored
  game), import.game_duplicate (per already-imported no-op),
  import.game_updated (per recorded replacement), import.completed -
  emitted and persisted to telemetry.jsonl
- errors: malformed_request for malformed input, illegal_move for
  rule-violating movetext, unknown_rights for refused sources
  (error shape per contract: code/message/retryable)
- idempotency: dedup identity game_id. Same id + same content hash is a
  visible already-imported no-op; same id + different hash is a
  recorded replacement; index membership stays unique.
- atomicity: records are content-addressed; the per-game COMMIT POINT
  is the single atomic rename of index.json pointing game_id at the new
  record version. Every crash window reads as pre-commit or fully
  committed: an unreferenced record version (stale .tmp or never-indexed
  candidate) is removed by recovery at the start of the next run,
  before any new telemetry; the previously indexed version stays
  readable until its replacement commits. Telemetry is an event log
  appended after the commit point, never the source of truth for state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from ingest.pgn import IllegalMove, MalformedPGN, validate_pgn_game
from ingest.rights import intake_decision
from ingest.streaming import iter_pgn_games

ROOT = Path(__file__).resolve().parents[1]
IMPORT_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "import.yaml").read_text())
RIGHTS_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "rights_policy.yaml").read_text())
CONTRACT = IMPORT_DOC["contract"]
RIGHTS = RIGHTS_DOC["rights_policy"]

SCENARIO = next(
    s for s in CONTRACT["scenarios"]["entries"] if s["id"] == "import-pgn"
)
SCENARIO_SOURCES = list(SCENARIO["sources"])
RECORD_FIELDS = list(CONTRACT["record"]["fields"])
PROVENANCE_FIELDS = list(CONTRACT["record"]["provenance_fields"])
SOURCES = {s["id"]: s for s in CONTRACT["sources"]["entries"]}
FAILURE_MAPPING = dict(CONTRACT["failure_mapping"])
SUMMARY_KIND = SCENARIO["visible_output"]


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


def _record_filename(record: dict) -> str:
    """Content-addressed record version: one filename per (id, content)."""
    return f"{record['game_id']}--{record['content_sha256'][:16]}.json"


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
    # strict input typing FIRST: a non-string or empty source id is a
    # structured refusal, never a crash inside registry lookups.
    if type(source_id) is not str or not source_id.strip():
        raise ImportFailure("unknown_source",
                            "source id must be a nonempty string")
    # scenario enforcement: refuse every source outside the
    # import-pgn scenario before any store state exists.
    if source_id not in SCENARIO_SOURCES:
        raise ImportFailure(
            "unknown_rights",
            f"source id {source_id!r} is outside the import-pgn scenario "
            f"{SCENARIO_SOURCES}")
    source = SOURCES.get(source_id)
    if source is None:
        raise ImportFailure("unknown_source", f"source id {source_id!r} not in registry")
    decision = intake_decision(source_id)
    if not decision.allowed:
        raise ImportFailure("unknown_rights",
                            f"intake refused for {source_id!r}: {decision.reason}")
    rights_class = decision.rights_class

    games_dir = store_root / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path = store_root / "telemetry.jsonl"
    index_path = store_root / "index.json"

    telemetry: list[str] = []

    def emit(event: str) -> None:
        telemetry.append(event)
        with telemetry_path.open("a") as fh:
            fh.write(json.dumps({"event": event}) + "\n")

    index: list[dict] = _recover_store(store_root, games_dir, index_path)
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
        entry = next((e for e in index if e["game_id"] == gid), None)
        existing = None
        if entry is not None:
            existing_path = games_dir / entry["record"]
            if existing_path.exists():
                existing = json.loads(existing_path.read_text())
        if entry is not None and existing is not None:
            if existing.get("content_sha256") == record["content_sha256"]:
                # duplicate: visible already-imported no-op
                emit("import.game_duplicate")
                already += 1
                continue
            # updated: recorded replacement. The candidate version is
            # written under its own content-addressed name; the index
            # rename is the single commit point. A crash before it leaves
            # the OLD indexed version readable and the candidate
            # unreferenced (recovered away on restart).
            new_index = [dict(e) for e in index]
            new_index[index.index(entry)] = {"game_id": gid,
                                             "record": _record_filename(record)}
            _commit_game(record, new_index, games_dir, index_path)
            index = new_index
            emit("import.game_updated")
            updated += 1
            continue
        # new game (or dangling index entry whose file is gone: the
        # candidate becomes the referenced version at the commit point)
        new_index = [e for e in index if e["game_id"] != gid]
        new_index.append({"game_id": gid, "record": _record_filename(record)})
        _commit_game(record, new_index, games_dir, index_path)
        index = new_index
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


def _commit_game(record: dict, index: list[dict], games_dir: Path,
                 index_path: Path) -> None:
    """One game commit: the candidate record version is renamed in, then
    the manifest rename is the single atomic commit point that makes
    exactly that version visible for the game_id."""
    _atomic_write(games_dir / _record_filename(record),
                  json.dumps(record, indent=1))
    _atomic_write(index_path, json.dumps(index, indent=1))


def _recover_store(store_root: Path, games_dir: Path,
                   index_path: Path) -> list[dict]:
    """Crash recovery, run BEFORE any new telemetry: remove stale .tmp
    artifacts and unreferenced record versions so every prior crash
    window reads as pre-commit or fully committed. Returns the committed
    manifest index (ordered unique entries)."""
    for tmp in list(store_root.glob("*.tmp")) + list(games_dir.glob("*.tmp")):
        tmp.unlink()
    index: list[dict] = []
    if index_path.exists():
        index = json.loads(index_path.read_text())
    referenced = {e["record"] for e in index}
    for rec in games_dir.glob("*.json"):
        if rec.name not in referenced:
            rec.unlink()  # never-committed candidate version
    return index
