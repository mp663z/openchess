"""T2717: bad rows quarantined with offsets, valid rows continue, no silent repair."""

from pathlib import Path

import zstandard as zstd

from ingest.quarantine import (
    QuarantineStore,
    iter_pgn_games_with_offsets,
    process_with_quarantine,
)

GOOD = '[Event "G{i}"]\n[Site "https://lichess.org/g{i}"]\n\n1. e4 e5 1-0\n'
BAD = '[Event "B{i}"]\n[Site "broken\n\n'  # unterminated tag, no movetext


def make_corpus(path: Path, pattern: list[str]) -> str:
    parts = []
    for i, kind in enumerate(pattern):
        parts.append((GOOD if kind == "good" else BAD).format(i=i))
    payload = "\n\n".join(parts)
    path.write_bytes(zstd.ZstdCompressor().compress(payload.encode()))
    return payload


def test_offsets_match_decompressed_stream(tmp_path):
    src = tmp_path / "c.pgn.zst"
    payload = make_corpus(src, ["good", "good", "good"])
    records = list(iter_pgn_games_with_offsets(src))
    assert len(records) == 3
    for seq, offset, raw in records:
        assert payload[offset : offset + 6] == '[Event', f"seq {seq} offset {offset}"
        assert payload[offset : offset + len(raw)] == raw


def test_bad_rows_quarantined_valid_continue(tmp_path):
    src = tmp_path / "c.pgn.zst"
    make_corpus(src, ["good", "bad", "good", "bad", "good"])
    store = QuarantineStore(tmp_path / "quarantine.jsonl")
    seen = []

    from ingest.schemas import adapt_game_pgn

    def handler(raw: str) -> None:
        adapt_game_pgn(raw, source_file="c.pgn.zst", license_value="CC0")
        seen.append(raw)

    result = process_with_quarantine(iter_pgn_games_with_offsets(src), "c.pgn.zst", handler, store)
    assert result == {"ok": 3, "quarantined": 2}
    assert len(seen) == 3  # every valid row processed despite interleaved failures

    quarantined = store.load()
    assert [q.sequence_no for q in quarantined] == [1, 3]
    for q in quarantined:
        assert q.source_file == "c.pgn.zst"
        assert q.byte_offset >= 0
        assert "not a PGN game record" in q.error
        assert q.raw.startswith('[Event "B')  # verbatim, unrepaired


def test_no_silent_repair_preserves_raw(tmp_path):
    src = tmp_path / "c.pgn.zst"
    payload = make_corpus(src, ["bad"])
    store = QuarantineStore(tmp_path / "q.jsonl")
    process_with_quarantine(
        iter_pgn_games_with_offsets(src),
        "c.pgn.zst",
        lambda raw: (_ for _ in ()).throw(ValueError("boom")),
        store,
    )
    (item,) = store.load()
    assert payload[item.byte_offset : item.byte_offset + len(item.raw)] == item.raw
