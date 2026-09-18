"""T2722 - Incremental update job.

Given the pinned snapshot manifest (data/datasets/snapshot-pins.yaml shape) and
an ingest function, applies only snapshots whose content hash has not already
been ingested, and publishes a delta manifest recording what changed.

Idempotent: re-running with the same pins ingests nothing and publishes an
empty delta. A changed hash for a known snapshot id is a conflict - recorded
in the delta manifest and NOT ingested (fail-closed, per pin policy).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS applied_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    url TEXT NOT NULL,
    applied_at TEXT DEFAULT (datetime('now'))
);
"""


@dataclass(frozen=True)
class DeltaEntry:
    snapshot_id: str
    action: str  # "ingested" | "skipped_unchanged" | "conflict_hash_changed"
    sha256: str
    detail: str


class IncrementalUpdater:
    def __init__(self, state_db: str | Path):
        self.db = sqlite3.connect(str(state_db))
        self.db.executescript(SCHEMA)

    def apply(
        self,
        pins: list[dict],
        ingest: Callable[[dict], dict],
        delta_manifest_path: str | Path,
    ) -> dict:
        """pins: [{'id':..., 'url':..., 'sha256':..., 'bytes':...}, ...]
        ingest: function(pin) -> {'rows': int, ...} called only for new snapshots.
        Returns and writes the delta manifest."""
        entries: list[DeltaEntry] = []
        for pin in pins:
            sid, sha = pin["id"], pin["sha256"]
            row = self.db.execute(
                "SELECT sha256 FROM applied_snapshots WHERE snapshot_id=?", (sid,)
            ).fetchone()
            if row is None:
                result = ingest(pin)
                self.db.execute(
                    "INSERT INTO applied_snapshots (snapshot_id, sha256, url) VALUES (?,?,?)",
                    (sid, sha, pin["url"]),
                )
                self.db.commit()
                entries.append(DeltaEntry(sid, "ingested", sha, json.dumps(result, sort_keys=True)))
            elif row[0] == sha:
                entries.append(DeltaEntry(sid, "skipped_unchanged", sha, "hash already applied"))
            else:
                entries.append(
                    DeltaEntry(
                        sid, "conflict_hash_changed", sha,
                        f"applied hash {row[0]} != pin hash {sha}; NOT ingested",
                    )
                )
        manifest = {
            "schema_version": 1,
            "snapshots": [asdict(e) for e in entries],
            "counts": {
                "ingested": sum(1 for e in entries if e.action == "ingested"),
                "skipped_unchanged": sum(1 for e in entries if e.action == "skipped_unchanged"),
                "conflicts": sum(1 for e in entries if e.action == "conflict_hash_changed"),
            },
        }
        Path(delta_manifest_path).write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest

    def close(self) -> None:
        self.db.close()
