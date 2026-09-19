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
import re
import sys
from datetime import datetime, timezone
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
_TAG_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_UTC_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# Content identity fields, in RECORD_FIELDS order: the canonical persisted
# GAME representation. Provenance timestamps/paths and imported_at are
# import-session data and never participate (identity-stability contract:
# the same game reimported from any path at any time is the same content).
CONTENT_IDENTITY_FIELDS = ["game_id", "source_id", "variant", "tags",
                           "movetext"]


def _canonical_record_bytes(record: dict) -> bytes:
    """The canonical persisted game representation whose digest is
    content_sha256 (contract: "content_sha256 covers the canonical
    record bytes"): CONTENT_IDENTITY_FIELDS in pinned RECORD_FIELDS
    order, compact JSON, UTF-8. Duplicate/updated detection is
    byte-exact over this representation, never the raw input text."""
    payload = {k: record[k] for k in CONTENT_IDENTITY_FIELDS}
    # normalized validated tags: key order is formatting, not content
    payload["tags"] = {k: payload["tags"][k] for k in sorted(payload["tags"])}
    return json.dumps(payload, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _valid_utc_ts(value: object) -> bool:
    if not (isinstance(value, str) and _UTC_TS_RE.match(value)):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True
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


IDENTITY_HEADERS = ("Site", "White", "Black", "UTCDate", "Date", "Round")


def game_identity(tags: dict[str, str], movetext: str) -> str:
    """Deterministic dedup identity (contract dedup_key game_id); never
    position-derived. Two-tier collision-safe rule (contract
    identity_rule): when ALL six identity headers are present and
    nonempty, identity is the header tuple, so an edited PGN of the
    same game classifies as an update; otherwise identity derives from
    the canonical game content (validated tags, key-normalized, plus
    movetext), so distinct header-sparse games can never silently
    replace each other - a content edit of a header-sparse game imports
    as a new game rather than guessing. The two preimages are
    domain-separated by construction."""
    if all(tags.get(k, "").strip() for k in IDENTITY_HEADERS):
        # injective tuple encoding: a canonical JSON array of the exact
        # strings, tier as a structured member - never delimiter-joined,
        # so no pair of distinct six-tuples can share a preimage
        key = json.dumps(["headers", [tags[k] for k in IDENTITY_HEADERS]],
                         separators=(",", ":"), ensure_ascii=False)
    else:
        key = json.dumps(["content",
                          {"tags": {k: tags[k] for k in sorted(tags)},
                           "movetext": movetext}],
                         separators=(",", ":"), ensure_ascii=False)
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
            "game_id": game_identity(parsed.tags, parsed.movetext),
            "source_id": source["id"],
            "provenance": {
                "source_id": source["id"],
                "rights_class": rights_class,
                "retrieved_at": retrieved_at,
                "retrieval_detail": str(pgn_path),
            },
            "variant": "standard",
            "tags": parsed.tags,
            "movetext": parsed.movetext,
            "imported_at": retrieved_at,
        }
        record["content_sha256"] = hashlib.sha256(
            _canonical_record_bytes(record)).hexdigest()
        record = {k: record[k] for k in RECORD_FIELDS}
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


_GAME_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_RECORD_NAME_RE = re.compile(r"^[0-9a-f]{16}--[0-9a-f]{16}\.json$")


def _validate_index(index: object, games_dir: Path) -> None:
    """Strict existing-store manifest validation, before any recovery or
    mutation (T0539): the persisted index must be exactly the
    content-addressed manifest grammar - a list of entries with the
    exact key set/order (game_id, record), strict hex game ids, unique
    ids and record names, record names in the content-addressed
    basename grammar with the game_id prefix matching the entry, and
    every referenced record file present and self-consistent (its own
    game_id and content hash prefix matching the manifest). Any
    contradiction refuses with partial_visibility and leaves the store
    untouched."""
    if not isinstance(index, list):
        raise ImportFailure("partial_visibility",
                            "store manifest contradiction: root is not a list")
    seen_ids: set[str] = set()
    seen_records: set[str] = set()
    for pos, entry in enumerate(index):
        if not isinstance(entry, dict) or list(entry) != ["game_id", "record"]:
            raise ImportFailure(
                "partial_visibility",
                f"store manifest contradiction: entry {pos} must be exactly "
                "(game_id, record)")
        gid, rec = entry["game_id"], entry["record"]
        if not (isinstance(gid, str) and _GAME_ID_RE.match(gid)):
            raise ImportFailure(
                "partial_visibility",
                f"store manifest contradiction: entry {pos} game_id invalid")
        if gid in seen_ids:
            raise ImportFailure(
                "partial_visibility",
                f"store manifest contradiction: duplicate game_id {gid}")
        if not (isinstance(rec, str) and _RECORD_NAME_RE.match(rec)
                and rec.split("--", 1)[0] == gid):
            raise ImportFailure(
                "partial_visibility",
                f"store manifest contradiction: entry {pos} record name "
                "outside the content-addressed grammar")
        if rec in seen_records:
            raise ImportFailure(
                "partial_visibility",
                f"store manifest contradiction: duplicate record {rec}")
        seen_ids.add(gid)
        seen_records.add(rec)
        record_path = games_dir / rec
        if not record_path.is_file():
            raise ImportFailure(
                "partial_visibility",
                f"store manifest contradiction: entry {pos} record file "
                "missing")
        try:
            stored = json.loads(record_path.read_text())
        except (OSError, ValueError) as e:
            raise ImportFailure(
                "partial_visibility",
                f"store manifest contradiction: entry {pos} record "
                f"unreadable: {e}") from e
        _validate_record(stored, gid, rec, pos)


def _reconstruct_pgn(tags: dict, movetext: str) -> str:
    """Rebuild PGN source text from stored tags + movetext so the
    SHIPPED parser can re-validate the stored game. Tag values are
    re-escaped exactly as the parser grammar expects."""
    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace('"', '\\"')
    lines = [f'[{k} "{esc(v)}"]' for k, v in tags.items()]
    return "\n".join(lines) + "\n\n" + movetext


def _validate_record(stored: object, gid: str, rec: str, pos: int) -> None:
    """Full stored-record contract validation (T0539): the referenced
    record must be exactly the declared field set/order with strict
    types, provenance agreeing with the top-level source and the
    registry rights class, UTC-grammar timestamps, a full 64-hex
    content_sha256 that RECOMPUTES from the canonical persisted record
    bytes, and a filename suffix equal to the verified digest's prefix.
    Any contradiction refuses; a 16-hex filename prefix is an address
    hint, never content integrity."""
    def refuse(detail: str) -> None:
        raise ImportFailure(
            "partial_visibility",
            f"store manifest contradiction: entry {pos} record {detail}")

    if not isinstance(stored, dict) or list(stored) != RECORD_FIELDS:
        refuse("must have exactly the declared fields in order")
    if stored["game_id"] != gid:
        refuse("game_id disagrees with the manifest")
    source_id = stored["source_id"]
    # scenario enforcement on STORED records, identical to the import
    # path: registry membership is not scenario authorization.
    if not (isinstance(source_id, str) and source_id in SCENARIO_SOURCES):
        refuse("source_id outside the import-pgn scenario sources")
    # the shipped rights gate decides stored-record acceptance: a
    # matching rights-class LABEL is not authorization.
    decision = intake_decision(source_id)
    if not decision.allowed:
        refuse(f"intake refused for the stored source: {decision.reason}")
    provenance = stored["provenance"]
    if not (isinstance(provenance, dict)
            and list(provenance) == PROVENANCE_FIELDS):
        refuse("provenance must have exactly the declared fields in order")
    if provenance["source_id"] != source_id:
        refuse("provenance source disagrees with the record")
    if provenance["rights_class"] != decision.rights_class:
        refuse("provenance rights class contradicts the gate decision")
    if not _valid_utc_ts(provenance["retrieved_at"]):
        refuse("provenance retrieved_at is not pinned UTC grammar")
    if not (isinstance(provenance["retrieval_detail"], str)
            and provenance["retrieval_detail"].strip()):
        refuse("provenance retrieval_detail must be a non-whitespace string")
    digest = stored["content_sha256"]
    # content_sha256 type and exactly-64-lowercase-hex grammar are
    # enforced by the full-digest recompute below: any non-string,
    # shorter, longer, uppercase or non-hex digest fails equality with
    # the recomputed canonical hash.
    if stored["variant"] != "standard":
        refuse("variant outside the scenario")
    tags = stored["tags"]
    # stored tags are pinned to the parser's reachable tag grammar:
    # names [A-Za-z0-9_]+, values free of line breaks (the parser reads
    # tag lines; a newline can never appear in a parsed value).
    if not (isinstance(tags, dict)
            and all(type(k) is str and _TAG_NAME_RE.match(k)
                    and type(v) is str and "\n" not in v and "\r" not in v
                    for k, v in tags.items())):
        refuse("tags must follow the PGN tag grammar "
               "(name [A-Za-z0-9_]+, value without line breaks)")
    if not (isinstance(stored["movetext"], str) and stored["movetext"].strip()):
        refuse("movetext must be a non-whitespace string")
    if not _valid_utc_ts(stored["imported_at"]):
        refuse("imported_at is not pinned UTC grammar")
    # semantic self-consistency: the stored record must BE the game its
    # identity names. Re-run the shipped validator on the reconstructed
    # PGN (tag/movetext grammar, legal SAN, result consistency,
    # variant/FEN exclusions), require an exact round-trip of the stored
    # tags/movetext, and recompute the identity from the validated tags.
    # Hashing corrupt data proves integrity since mutation, not validity.
    try:
        reparsed = validate_pgn_game(
            _reconstruct_pgn(tags, stored["movetext"]))
    except (MalformedPGN, IllegalMove) as e:
        refuse(f"stored game content fails the shipped validator: {e}")
    if reparsed.tags != tags:
        refuse("stored tags are not parser-reachable")
    if reparsed.movetext != stored["movetext"]:
        refuse("stored movetext is not parser-reachable")
    if ("\x00" in stored["movetext"]
            or any("\x00" in k or "\x00" in v for k, v in tags.items())
            or "\x00" in provenance["retrieval_detail"]):
        refuse("NUL bytes are not parser-reachable stored content")
    if game_identity(reparsed.tags, reparsed.movetext) != gid:
        refuse("stored game_id does not recompute from the identity "
               "headers")
    recomputed = hashlib.sha256(_canonical_record_bytes(stored)).hexdigest()
    if digest != recomputed:
        refuse("content_sha256 does not recompute from the canonical "
               "record bytes")
    embedded_hash = rec.split("--", 1)[1][:-len(".json")]
    if digest[:16] != embedded_hash:
        refuse("content_sha256 prefix disagrees with the manifest filename")


def _recover_store(store_root: Path, games_dir: Path,
                   index_path: Path) -> list[dict]:
    """Crash recovery, run BEFORE any new telemetry: validate the
    persisted manifest strictly (refusing contradictions untouched),
    then remove stale .tmp artifacts and unreferenced record versions so
    every prior crash window reads as pre-commit or fully committed.
    Returns the committed manifest index (ordered unique entries)."""
    index: list[dict] = []
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except (OSError, ValueError) as e:
            raise ImportFailure("partial_visibility",
                                f"store manifest unreadable: {e}") from e
        _validate_index(index, games_dir)
    for tmp in list(store_root.glob("*.tmp")) + list(games_dir.glob("*.tmp")):
        tmp.unlink()
    referenced = {e["record"] for e in index}
    for rec in games_dir.glob("*.json"):
        if rec.name not in referenced:
            rec.unlink()  # never-committed candidate version
    return index


_RETRIEVED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


USAGE = ("usage: python -m ingest.import_pgn <pgn-file> --store <dir> "
         "[--source SOURCE] [--retrieved-at ISO8601]")


def main(argv: list[str] | None = None) -> int:
    """Local-runnable import-pgn surface (T0539): drives the shipped
    scenario runner and renders the contract's visible output
    (import-summary JSON on stdout) and error shape (contract error JSON
    on stderr). Exit codes: 0 success (including already-imported), 1
    contract refusal or wrapped local I/O failure (internal), 2 usage
    error. A refused import commits no game record, index, or summary;
    it may persist an import.started telemetry line (and an empty games
    directory) from before the refusal - that exact state is pinned by
    the T0539 tests. Usage validation happens before any state."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or "--help" in args or "-h" in args:
        print(USAGE, file=sys.stderr)
        return 2
    positional: list[str] = []
    opts: dict[str, str] = {}
    known = {"--store", "--source", "--retrieved-at"}
    i = 0
    while i < len(args):
        a = args[i]
        if a in known:
            if (a in opts or i + 1 >= len(args)
                    or args[i + 1].startswith("-")):
                print(f"{USAGE}\nerror: {a} requires exactly one value",
                      file=sys.stderr)
                return 2
            opts[a] = args[i + 1]
            i += 2
        elif a.startswith("--"):
            print(f"{USAGE}\nerror: unknown option {a}", file=sys.stderr)
            return 2
        else:
            positional.append(a)
            i += 1
    if len(positional) != 1 or "--store" not in opts:
        print(USAGE, file=sys.stderr)
        return 2
    if not opts["--store"].strip():
        print(f"{USAGE}\nerror: --store requires a nonempty destination",
              file=sys.stderr)
        return 2
    if "--retrieved-at" in opts:
        rt = opts["--retrieved-at"]
        if not (rt.strip() and _RETRIEVED_AT_RE.match(rt)):
            print(f"{USAGE}\nerror: --retrieved-at must be UTC ISO8601 "
                  "YYYY-MM-DDTHH:MM:SSZ", file=sys.stderr)
            return 2
        try:
            datetime.strptime(rt, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            print(f"{USAGE}\nerror: --retrieved-at is not a real UTC "
                  "calendar time", file=sys.stderr)
            return 2
    if not Path(positional[0]).is_file():
        err = ImportFailure("malformed_input",
                            f"input file unreadable: {positional[0]}")
        print(json.dumps(err.as_dict(), indent=1), file=sys.stderr)
        return 1
    # noqa reason: runtime floor is py3.10, datetime.UTC unavailable
    retrieved_at = (opts.get("--retrieved-at")
                    or datetime.now(timezone.utc).strftime(  # noqa: UP017
                        "%Y-%m-%dT%H:%M:%SZ"))
    try:
        summary = run_import_pgn(Path(positional[0]), Path(opts["--store"]),
                                 opts.get("--source", "pgn-file"),
                                 retrieved_at=retrieved_at)
    except ImportFailure as e:
        print(json.dumps(e.as_dict(), indent=1), file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        # expected local I/O failures (store creation, reads, temp
        # writes, renames, telemetry/summary writes, malformed existing
        # store JSON) surface as the contract error envelope, never a
        # traceback
        err = ImportFailure("partial_visibility", f"local I/O failure: {e}")
        print(json.dumps(err.as_dict(), indent=1), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
