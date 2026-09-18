"""T2723 - Corpus audit sample tool: raw-to-normalized reconciliation.

Streams a bounded sample from a downloaded lichess snapshot, normalizes every
row through the schema adapter, and emits a reconciliation manifest: raw byte
checksum, row counts, dedup result, quarantine count, license tally. The
independent verifier re-runs this tool and reconciles every number.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import zstandard as zstd

from ingest.schemas import adapt_puzzle_csv_line


def run_sample(source: Path, n_rows: int) -> dict:
    raw_hasher = hashlib.sha256()
    normalized_hashes: list[str] = []
    license_tally: dict[str, int] = {}
    quarantined = 0
    header: list[str] | None = None
    seen_ids: set[str] = set()
    dupes = 0
    rows_read = 0
    bytes_read = 0

    dctx = zstd.ZstdDecompressor(max_window_size=2**27)
    with source.open("rb") as fh, dctx.stream_reader(fh) as reader:
        buf = ""
        while rows_read < n_rows:
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            raw_hasher.update(chunk)
            bytes_read += len(chunk)
            buf += chunk.decode("utf-8", errors="replace")
            while "\n" in buf and rows_read < n_rows:
                line, buf = buf.split("\n", 1)
                if header is None:
                    header = line.split(",")
                    continue
                if not line.strip():
                    continue
                rows_read += 1
                try:
                    n = adapt_puzzle_csv_line(
                        header, line, source_file=source.name, license_value="CC0"
                    )
                except Exception:
                    quarantined += 1
                    continue
                if n.row["puzzle_id"] in seen_ids:
                    dupes += 1
                seen_ids.add(n.row["puzzle_id"])
                normalized_hashes.append(n.row["puzzle_id"])
                license_tally[n.row["license"]] = license_tally.get(n.row["license"], 0) + 1

    return {
        "schema_version": 1,
        "source_file": source.name,
        "sample_rows_requested": n_rows,
        "rows_read": rows_read,
        "bytes_read": bytes_read,
        "raw_sample_sha256": raw_hasher.hexdigest(),
        "normalized_count": len(normalized_hashes),
        "quarantined_count": quarantined,
        "duplicate_puzzle_ids": dupes,
        "license_tally": license_tally,
        "normalized_id_multiset_sha256": hashlib.sha256(
            "\n".join(sorted(normalized_hashes)).encode()
        ).hexdigest(),
    }


if __name__ == "__main__":
    src = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("data/datasets/corpus-audit-sample.json")
    manifest = run_sample(src, n)
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
