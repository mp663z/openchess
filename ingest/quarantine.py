"""T2717 - Malformed-record quarantine.

Wraps per-record processing: bad records are quarantined with their source
location (file + byte offset in the decompressed stream + sequence number),
valid records continue, and nothing is silently repaired - the raw bytes of
a quarantined record are preserved verbatim for human inspection.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Quarantined:
    source_file: str
    byte_offset: int
    sequence_no: int
    error: str
    raw: str  # verbatim source bytes, never repaired


class QuarantineStore:
    """Appends quarantined records to a JSONL sidecar; loadable for review."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def add(self, item: Quarantined) -> None:
        with self.path.open("a") as fh:
            fh.write(json.dumps(asdict(item)) + "\n")

    def load(self) -> list[Quarantined]:
        if not self.path.exists():
            return []
        return [
            Quarantined(**json.loads(line))
            for line in self.path.read_text().splitlines()
            if line.strip()
        ]

    def count(self) -> int:
        return len(self.load())


def process_with_quarantine(
    records: Iterator[tuple[int, int, str]],
    source_file: str,
    handler: Callable[[str], Any],
    store: QuarantineStore,
) -> dict:
    """Run handler over (sequence_no, byte_offset, raw) records; handler
    exceptions quarantine the record and processing continues."""
    ok = 0
    bad = 0
    for seq, offset, raw in records:
        try:
            handler(raw)
            ok += 1
        except Exception as exc:  # any parse failure quarantines, never repairs
            store.add(
                Quarantined(
                    source_file=source_file,
                    byte_offset=offset,
                    sequence_no=seq,
                    error=f"{type(exc).__name__}: {exc}",
                    raw=raw,
                )
            )
            bad += 1
    return {"ok": ok, "quarantined": bad}


def iter_pgn_games_with_offsets(
    path: str | Path, chunk_size: int = 1 << 20
) -> Iterator[tuple[int, int, str]]:
    """Like ingest.streaming.iter_pgn_games but also yields each game's byte
    offset in the decompressed stream."""
    from ingest.streaming import _raw_chunks

    buf = ""
    buf_start = 0  # decompressed byte offset of buf[0]
    seq = 0
    for chunk in _raw_chunks(Path(path), chunk_size):
        buf += chunk.decode("utf-8", errors="replace")
        while (idx := buf.find("\n\n[", 1)) != -1:
            segment, buf = buf[:idx], buf[idx + 2 :]
            offset = buf_start
            buf_start += idx + 2
            game = segment.strip("\n")
            if game.strip():
                offset += len(segment) - len(segment.lstrip("\n"))
                yield seq, offset, game
                seq += 1
    if buf.strip():
        offset = buf_start + len(buf) - len(buf.lstrip("\n"))
        yield seq, offset, buf.strip("\n")
