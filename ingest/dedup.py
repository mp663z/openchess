"""T2718 - Deduplication identity rules for games, puzzles and evals.

Identity rules:
- game: sha256 of (Site URL) when present, else (White|Black|UTCDate|UTCTime|movetext)
- puzzle: PuzzleId
- eval: fen

Duplicate (same identity, same normalized content) -> skipped, provenance
extended. Conflicting version (same identity, different content) -> first
version retained, conflict recorded with BOTH provenances; nothing is
silently overwritten.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from ingest.schemas import Normalized

SCHEMA = """
CREATE TABLE IF NOT EXISTS dedup_records (
    identity TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (identity, schema_name)
);
CREATE TABLE IF NOT EXISTS dedup_provenance (
    identity TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    source_file TEXT NOT NULL,
    first_seen_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (identity, schema_name, source_file)
);
CREATE TABLE IF NOT EXISTS dedup_conflicts (
    identity TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    retained_hash TEXT NOT NULL,
    rejected_hash TEXT NOT NULL,
    retained_payload TEXT NOT NULL,
    rejected_payload TEXT NOT NULL,
    recorded_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (identity, schema_name, rejected_hash)
);
"""


def identity_of(n: Normalized) -> str:
    if n.schema_name == "puzzle":
        return f"puzzle:{n.row['puzzle_id']}"
    if n.schema_name == "eval":
        return f"eval:{n.row['fen']}"
    if n.schema_name == "game":
        if n.row.get("site_url"):
            return f"game:{n.row['site_url']}"
        ids = n.row["identifiers"]
        basis = "|".join(
            [ids.get("White", ""), ids.get("Black", ""), ids.get("UTCDate", ""),
             ids.get("UTCTime", ""), n.row["movetext"]]
        )
        return "game:anon:" + hashlib.sha256(basis.encode()).hexdigest()
    raise ValueError(f"no identity rule for schema {n.schema_name!r}")


def content_hash_of(n: Normalized) -> str:
    """Content hash excludes provenance (source_file); it identifies the
    record's payload, not where we saw it."""
    content = {k: v for k, v in n.row.items() if k != "source_file"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class DedupStore:
    def __init__(self, db_path: str | Path):
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(SCHEMA)

    def add(self, n: Normalized, *, source_file: str) -> str:
        """Returns 'inserted', 'duplicate', or 'conflict'."""
        identity = identity_of(n)
        content_hash = content_hash_of(n)
        payload = json.dumps(n.row, sort_keys=True)
        self.db.execute(
            "INSERT OR IGNORE INTO dedup_provenance (identity, schema_name, source_file)"
            " VALUES (?,?,?)",
            (identity, n.schema_name, source_file),
        )
        existing = self.db.execute(
            "SELECT content_hash, payload FROM dedup_records WHERE identity=? AND schema_name=?",
            (identity, n.schema_name),
        ).fetchone()
        if existing is None:
            self.db.execute(
                "INSERT INTO dedup_records (identity, schema_name, content_hash, payload)"
                " VALUES (?,?,?,?)",
                (identity, n.schema_name, content_hash, payload),
            )
            self.db.commit()
            return "inserted"
        if existing[0] == content_hash:
            self.db.commit()
            return "duplicate"
        self.db.execute(
            "INSERT OR IGNORE INTO dedup_conflicts"
            " (identity, schema_name, retained_hash, rejected_hash, retained_payload,"
            "  rejected_payload) VALUES (?,?,?,?,?,?)",
            (identity, n.schema_name, existing[0], content_hash, existing[1], payload),
        )
        self.db.commit()
        return "conflict"

    def count_records(self) -> int:
        return self.db.execute("SELECT count(*) FROM dedup_records").fetchone()[0]

    def provenance(self, n: Normalized) -> list[str]:
        identity = identity_of(n)
        rows = self.db.execute(
            "SELECT source_file FROM dedup_provenance"
            " WHERE identity=? AND schema_name=? ORDER BY source_file",
            (identity, n.schema_name),
        ).fetchall()
        return [r[0] for r in rows]

    def conflicts(self) -> list[tuple]:
        return self.db.execute(
            "SELECT identity, retained_hash, rejected_hash FROM dedup_conflicts"
        ).fetchall()

    def close(self) -> None:
        self.db.close()
