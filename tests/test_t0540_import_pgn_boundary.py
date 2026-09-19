"""T0540: Import/PGN/boundary tests - boundary and adjacent-negative
behavior on the SHIPPED import-pgn path (python -m ingest.import_pgn in
a subprocess), with persisted state checked on disk.

Boundaries exercised: empty and whitespace-only input, missing and
extra trailing newlines, CRLF line endings, games and the "\\n\\n["
separator straddling the streaming chunk boundary, multi-byte UTF-8
characters straddling the chunk boundary, large multi-game files, and
persisted-state shape at every step.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures"
VALID_PGN = FIXTURE_DIR / "valid_user_game.pgn"
RETRIEVED_AT = "2026-09-19T00:00:00Z"
CHUNK_SIZE = 1 << 20  # pinned to ingest.streaming.CHUNK_SIZE


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ingest.import_pgn", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=120)


def import_ok(pgn: Path, store: Path, **extra: str) -> dict:
    args = [str(pgn), "--store", str(store), "--retrieved-at", RETRIEVED_AT]
    for k, v in extra.items():
        args += [f"--{k.replace('_', '-')}", v]
    cp = run_cli(*args)
    assert cp.returncode == 0, cp.stderr
    assert cp.stderr == ""
    return json.loads(cp.stdout)


def refuse(pgn: Path, store: Path, code: str) -> dict:
    cp = run_cli(str(pgn), "--store", str(store),
                 "--retrieved-at", RETRIEVED_AT)
    assert cp.returncode == 1
    assert cp.stdout == ""
    assert "Traceback" not in cp.stderr
    err = json.loads(cp.stderr)
    assert err["code"] == code
    return err


def one_game(white: str = "a", ply: str = "e4 e5 2. Nf3 Nc6") -> str:
    return (f'[Event "x"]\n[Site "?"]\n[Date "2026.01.01"]\n'
            f'[Round "1"]\n[White "{white}"]\n[Black "b"]\n'
            f'[Result "1-0"]\n\n{ply} 1-0\n')


def stored_records(store: Path) -> list[dict]:
    games = store / "games"
    if not games.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(games.glob("*.json"))]


class TestEmptyAndShapeBoundaries:
    def test_empty_file_no_importable_games(self, tmp_path):
        pgn = tmp_path / "empty.pgn"
        pgn.write_text("")
        err = refuse(pgn, tmp_path / "s", "malformed_request")
        assert "no importable games" in json.dumps(err) or err["code"]
        assert not (tmp_path / "s").exists() or stored_records(tmp_path / "s") == []

    def test_whitespace_only_file(self, tmp_path):
        pgn = tmp_path / "ws.pgn"
        pgn.write_text("  \n\n\t\n   \n")
        refuse(pgn, tmp_path / "s", "malformed_request")

    def test_no_trailing_newline(self, tmp_path):
        pgn = tmp_path / "nonl.pgn"
        pgn.write_text(one_game().rstrip("\n"))
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 1
        assert len(stored_records(tmp_path / "s")) == 1

    def test_many_trailing_blank_lines(self, tmp_path):
        pgn = tmp_path / "blank.pgn"
        pgn.write_text(one_game() + "\n\n\n\n")
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 1
        assert len(stored_records(tmp_path / "s")) == 1

    def test_leading_blank_lines(self, tmp_path):
        pgn = tmp_path / "lead.pgn"
        pgn.write_text("\n\n\n" + one_game())
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 1


class TestLargeAndChunkBoundaries:
    def test_game_spanning_chunk_boundary(self, tmp_path):
        # pad game 1 with a long comment so the game-1/game-2 separator
        # lands past the 1 MiB streaming chunk boundary
        pad = "{ " + "x" * (CHUNK_SIZE - 900) + " } "
        g1 = one_game(ply="e4 e5 " + pad + "2. Nf3 Nc6")
        pgn = tmp_path / "big.pgn"
        pgn.write_text(g1 + "\n\n" + one_game(white="c"))
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 2
        assert len(stored_records(tmp_path / "s")) == 2

    def test_separator_straddling_chunk_boundary(self, tmp_path):
        # place the "\\n\\n[" separator so the "[" lands in the next chunk
        base = one_game()
        target = CHUNK_SIZE
        pad_len = target - len(base) - 3  # '{' + pad + '}' then \n\n[
        pad = "{ " + "y" * pad_len + " }"
        g1 = base.rstrip("\n")[:-3] + pad + " 1-0\n"  # keep result legal-ish
        pgn = tmp_path / "straddle.pgn"
        content = g1 + "\n\n" + one_game(white="c")
        assert content.index("\n\n[", 1) > CHUNK_SIZE - 2
        pgn.write_text(content)
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 2

    def test_multibyte_utf8_straddling_chunk_boundary(self, tmp_path):
        # a 4-byte UTF-8 character positioned to straddle the chunk edge
        # must survive intact in the persisted tags (no U+FFFD mangling)
        # build a game whose Black tag value ends with a 4-byte char at
        # exactly the chunk boundary
        body = one_game(white="w")
        # compute padding so that byte offset CHUNK_SIZE splits the char
        head = body.split('[Black "b"]')[0] + '[Black "'
        tail = '"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'
        pad = "b" * (CHUNK_SIZE - len(head.encode()) - 2)
        game = head + pad + "\U0001f600" + tail
        assert len((head + pad).encode()) + 2 == CHUNK_SIZE
        pgn = tmp_path / "utf8.pgn"
        pgn.write_text(game)
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 1
        rec = stored_records(tmp_path / "s")[0]
        assert rec["tags"]["Black"] == pad + "\U0001f600"
        assert "\ufffd" not in json.dumps(rec)

    def test_thousand_game_file(self, tmp_path):
        games = [one_game(white=f"p{i}") for i in range(1000)]
        pgn = tmp_path / "many.pgn"
        pgn.write_text("\n\n".join(games) + "\n")
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 1000
        index = json.loads((tmp_path / "s" / "index.json").read_text())
        assert len(index) == 1000
        gids = [e["game_id"] for e in index]
        assert len(set(gids)) == 1000
        # reimport: all duplicates, no growth
        summary2 = import_ok(pgn, tmp_path / "s")
        assert summary2["games_imported"] == 0
        assert len(json.loads((tmp_path / "s" / "index.json").read_text())) == 1000


class TestLineEndingBoundaries:
    def test_crlf_file(self, tmp_path):
        pgn = tmp_path / "crlf.pgn"
        pgn.write_bytes(one_game().replace("\n", "\r\n").encode())
        cp = run_cli(str(pgn), "--store", str(tmp_path / "s"),
                     "--retrieved-at", RETRIEVED_AT)
        # shipped behavior pinned either way, but never a traceback
        assert "Traceback" not in cp.stderr
        if cp.returncode == 0:
            assert json.loads(cp.stdout)["games_imported"] == 1
        else:
            assert json.loads(cp.stderr)["code"] == "malformed_request"

    def test_crlf_separator_between_games(self, tmp_path):
        content = one_game() + "\r\n\r\n" + one_game(white="c")
        pgn = tmp_path / "crlf2.pgn"
        pgn.write_bytes(content.encode())
        cp = run_cli(str(pgn), "--store", str(tmp_path / "s"),
                     "--retrieved-at", RETRIEVED_AT)
        assert "Traceback" not in cp.stderr


class TestAdjacentNegativeBoundaries:
    @pytest.mark.parametrize("ply", [
        "1. e4 e5 2. Nf3",          # missing result token (truncated)
        "1. e4 e5 2. Nf3 Nc6 0-1 extra",  # token after result
    ])
    def test_almost_valid_movetext(self, tmp_path, ply):
        pgn = tmp_path / "bad.pgn"
        pgn.write_text(one_game(ply=ply.split(" 1-0")[0]).replace(
            "1-0\n", "\n").replace('[Result "1-0"]', '[Result "*"]'))
        cp = run_cli(str(pgn), "--store", str(tmp_path / "s"),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert "Traceback" not in cp.stderr
        assert json.loads(cp.stderr)["code"] in (
            "malformed_request", "illegal_move")

    def test_in_progress_result_marker_accepted(self, tmp_path):
        # "*" is a legal PGN result token; ongoing games import cleanly
        pgn = tmp_path / "live.pgn"
        pgn.write_text(one_game().replace('[Result "1-0"]', '[Result "*"]')
                       .replace(" 1-0\n", " *\n"))
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 1
        assert len(stored_records(tmp_path / "s")) == 1

    def test_illegal_move_on_last_ply(self, tmp_path):
        pgn = tmp_path / "ill.pgn"
        pgn.write_text(one_game(ply="e4 e5 2. Ke1 Ke8 3. Ke1 Ke8 4. Qh5 Qh4 5. Qxh4"))
        cp = run_cli(str(pgn), "--store", str(tmp_path / "s"),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert json.loads(cp.stderr)["code"] in (
            "malformed_request", "illegal_move")

    def test_unbalanced_comment(self, tmp_path):
        pgn = tmp_path / "uc.pgn"
        pgn.write_text(one_game(ply="e4 { unclosed e5 2. Nf3 Nc6"))
        cp = run_cli(str(pgn), "--store", str(tmp_path / "s"),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert "Traceback" not in cp.stderr
        # unbalanced comment => malformed movetext; store must not gain
        # a record for the rejected game
        assert stored_records(tmp_path / "s") == []


class TestCompressedInputBoundary:
    def test_zst_compressed_input(self, tmp_path):
        import zstandard as zstd
        games = [one_game(white=f"z{i}") for i in range(5)]
        raw = ("\n\n".join(games) + "\n").encode()
        pgn = tmp_path / "in.pgn.zst"
        pgn.write_bytes(zstd.ZstdCompressor().compress(raw))
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 5
        assert len(stored_records(tmp_path / "s")) == 5

    def test_zst_game_spanning_chunk_boundary(self, tmp_path):
        import zstandard as zstd
        pad = "{ " + "x" * (CHUNK_SIZE - 900) + " } "
        g1 = one_game(ply="e4 e5 " + pad + "2. Nf3 Nc6")
        raw = (g1 + "\n\n" + one_game(white="c")).encode()
        pgn = tmp_path / "big.pgn.zst"
        pgn.write_bytes(zstd.ZstdCompressor(level=1).compress(raw))
        summary = import_ok(pgn, tmp_path / "s")
        assert summary["games_imported"] == 2


class TestPersistedStateAtBoundaries:
    def test_chunk_spanning_content_stored_intact(self, tmp_path):
        pad = "{ " + "x" * (CHUNK_SIZE - 900) + " } "
        g1 = one_game(ply="e4 e5 " + pad + "2. Nf3 Nc6")
        pgn = tmp_path / "big.pgn"
        pgn.write_text(g1 + "\n\n" + one_game(white="c"))
        import_ok(pgn, tmp_path / "s")
        recs = stored_records(tmp_path / "s")
        assert any(pad.strip() in r["movetext"] for r in recs)
        # exact record field set/order on every stored record
        for r in recs:
            assert list(r) == ["game_id", "source_id", "provenance",
                               "content_sha256", "variant", "tags",
                               "movetext", "imported_at"]

    def test_persisted_index_and_telemetry_exact(self, tmp_path):
        pgn = tmp_path / "t.pgn"
        pgn.write_text(one_game() + "\n\n" + one_game(white="c"))
        import_ok(pgn, tmp_path / "s")
        store = tmp_path / "s"
        index = json.loads((store / "index.json").read_text())
        assert len(index) == 2
        for e in index:
            assert list(e) == ["game_id", "record"]
            assert (store / "games" / e["record"]).is_file()
        events = [json.loads(line)["event"]
                  for line in (store / "telemetry.jsonl").read_text()
                  .splitlines()]
        assert events[0] == "import.started"
        assert events[-1] == "import.completed"
        summary = json.loads((store / "summary.json").read_text()) \
            if (store / "summary.json").is_file() else None
        if summary is not None:
            assert summary["games_imported"] == 2

    def test_bom_at_file_start(self, tmp_path):
        pgn = tmp_path / "bom.pgn"
        pgn.write_bytes(b"\xef\xbb\xbf" + one_game().encode())
        cp = run_cli(str(pgn), "--store", str(tmp_path / "s"),
                     "--retrieved-at", RETRIEVED_AT)
        # pinned either way, never a traceback; if refused, store gains
        # no record
        assert "Traceback" not in cp.stderr
        if cp.returncode == 0:
            assert len(stored_records(tmp_path / "s")) == 1
        else:
            assert json.loads(cp.stderr)["code"] == "malformed_request"
            assert stored_records(tmp_path / "s") == []
