"""T0541: Import/PGN/property test - seeded deterministic properties over
the SHIPPED import path (python -m ingest.import_pgn in a subprocess),
with persisted state checked on disk after every run.

Properties: byte-level determinism across fresh stores, idempotent
re-import, corpus permutation invariance, header-complete update
semantics with stale-record recovery, refusal atomicity on corrupted
games, and header-sparse content identity (distinct games never
silently replace each other). Random valid games are generated with
the shipped move generator (ingest.pgn.Board.legal_moves) under a
pinned seed - adjacent-negative cases come from corrupting those
games, never from handwritten fixtures alone."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

from ingest.pgn import Board

ROOT = Path(__file__).resolve().parents[1]
RETRIEVED_AT = "2026-09-19T00:00:00Z"
SEED = 20260920
FILES = "abcdefgh"


def _name(idx: int) -> str:
    return FILES[idx % 8] + str(idx // 8 + 1)


def _san(board: Board, move) -> str:
    """SAN for a generated move - test-side input generation; the shipped
    validator remains the oracle that checks every game."""
    if move.castle:
        san = "O-O" if move.castle == "K" else "O-O-O"
    else:
        piece = move.piece.upper()
        dest = _name(move.to)
        if piece == "P":
            san = (FILES[move.frm % 8] + "x" if move.capture else "") + dest
        else:
            others = [m for m in board.legal_moves(_annotate=False)
                      if m.piece == move.piece and m.to == move.to
                      and m.frm != move.frm]
            dis = ""
            if others:
                same_file = any(m.frm % 8 == move.frm % 8 for m in others)
                same_rank = any(m.frm // 8 == move.frm // 8 for m in others)
                if not same_file:
                    dis = FILES[move.frm % 8]
                elif not same_rank:
                    dis = str(move.frm // 8 + 1)
                else:
                    dis = _name(move.frm)
            san = piece + dis + ("x" if move.capture else "") + dest
        if move.promotion:
            san += "=" + move.promotion.upper()
    if move.gives_mate:
        san += "#"
    elif move.gives_check:
        san += "+"
    return san


def _random_moves(rng: random.Random, max_plies: int) -> tuple[list[str], str]:
    """A random valid playout via the SHIPPED move generator. Returns
    (SAN list, result token)."""
    board = Board.initial()
    sans: list[str] = []
    result = "*"
    for _ in range(max_plies):
        moves = board.legal_moves()
        if not moves:
            if board.attacked(board.king_square(board.turn),
                              "b" if board.turn == "w" else "w"):
                result = "1-0" if board.turn == "b" else "0-1"
            else:
                result = "1/2-1/2"
            break
        m = rng.choice(moves)
        sans.append(_san(board, m))
        board = board.apply(m)
    return sans, result


def _movetext(sans: list[str], result: str) -> str:
    out = []
    for i, san in enumerate(sans):
        if i % 2 == 0:
            out.append(f"{i // 2 + 1}.")
        out.append(san)
    out.append(result)
    return " ".join(out)


def _game_pgn(rng: random.Random, no: int, sparse: bool = False) -> str:
    sans, result = _random_moves(rng, max_plies=60)
    tags = [
        '[Event "Property Cup"]',
        f'[White "Player {no}A"]',
        f'[Black "Player {no}B"]',
        f'[Result "{result}"]',
    ]
    if not sparse:
        tags[1:1] = [
            f'[Site "board-{no}"]',
            '[UTCDate "2026.09.20"]',
            '[Date "2026.09.20"]',
            f'[Round "{no}"]',
        ]
    return "\n".join(tags) + "\n\n" + _movetext(sans, result) + "\n"


def _corpus(rng_seed: int, n: int) -> str:
    rng = random.Random(rng_seed)
    return "\n".join(_game_pgn(rng, i + 1) for i in range(n))


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ingest.import_pgn", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=120)


def import_ok(pgn: Path, store: Path) -> dict:
    cp = run_cli(str(pgn), "--store", str(store), "--retrieved-at", RETRIEVED_AT)
    assert cp.returncode == 0, cp.stderr
    assert cp.stderr == ""
    return json.loads(cp.stdout)


def store_tree(store: Path) -> dict[str, str]:
    """Every persisted file as relpath -> sha256 of its bytes."""
    out = {}
    for f in sorted(store.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(store))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def index_entries(store: Path) -> list[dict]:
    return json.loads((store / "index.json").read_text())


CORPUS = _corpus(SEED, 6)
GAME_COUNT = 6


def test_determinism_across_fresh_stores(tmp_path):
    pgn = tmp_path / "corpus.pgn"
    pgn.write_text(CORPUS)
    s1 = import_ok(pgn, tmp_path / "s1")
    s2 = import_ok(pgn, tmp_path / "s2")
    assert s1 == s2
    assert s1["games_imported"] == GAME_COUNT
    assert s1["games_already_imported"] == 0
    assert s1["games_updated"] == 0
    assert store_tree(tmp_path / "s1") == store_tree(tmp_path / "s2")


def test_reimport_is_pure_duplicate_noop(tmp_path):
    pgn = tmp_path / "corpus.pgn"
    pgn.write_text(CORPUS)
    store = tmp_path / "s"
    first = import_ok(pgn, store)
    tree_before = store_tree(store)
    index_before = (store / "index.json").read_bytes()
    second = import_ok(pgn, store)
    assert second["games_imported"] == 0
    assert second["games_already_imported"] == GAME_COUNT
    assert second["games_updated"] == 0
    assert (store / "index.json").read_bytes() == index_before
    # only telemetry/summary may gain lines; game records and index untouched
    after = store_tree(store)
    for path, digest in tree_before.items():
        if path.startswith("games/") or path == "index.json":
            assert after[path] == digest
    assert first["games_imported"] == GAME_COUNT


def test_permutation_invariant_identity_and_records(tmp_path):
    # split on game boundaries: tags start with [Event
    parts = CORPUS.split("\n[Event ")
    head, rest = parts[0], ["[Event " + p for p in parts[1:]]
    all_games = [head] + rest
    assert len(all_games) == GAME_COUNT
    base_ids: set[str] = set()
    base_records: set[str] = set()
    for trial in range(3):
        rng = random.Random(SEED + 100 + trial)
        order = all_games[:]
        rng.shuffle(order)
        pgn = tmp_path / f"perm{trial}.pgn"
        pgn.write_text("\n".join(order))
        store = tmp_path / f"ps{trial}"
        summary = import_ok(pgn, store)
        assert summary["games_imported"] == GAME_COUNT
        ids = {e["game_id"] for e in index_entries(store)}
        records = {f.name for f in (store / "games").glob("*.json")}
        if trial == 0:
            base_ids, base_records = ids, records
        else:
            assert ids == base_ids
            assert records == base_records


def test_header_complete_edit_is_update_with_recovery(tmp_path):
    pgn = tmp_path / "corpus.pgn"
    pgn.write_text(CORPUS)
    store = tmp_path / "s"
    import_ok(pgn, store)
    ids_before = {e["game_id"]: e["record"] for e in index_entries(store)}
    # edit exactly one header-complete game: drop its last move, result -> *
    parts = CORPUS.split("\n[Event ")
    head, rest = parts[0], ["[Event " + p for p in parts[1:]]
    edited_game, target = None, rest[0]
    tokens = target.split("\n\n", 1)
    moves = tokens[1].strip().split()
    # strip trailing result token and last move
    assert moves[-1] in {"1-0", "0-1", "1/2-1/2", "*"}
    moves = moves[:-1]
    last = moves.pop()
    assert not last.endswith("."), f"unexpected move-number token {last}"
    if moves and moves[-1].endswith("."):
        moves.pop()  # dangling move number
    edited_game = tokens[0] + "\n\n" + " ".join(moves) + " *\n"
    edited_game = edited_game.replace('[Result "1-0"]', '[Result "*"]')
    edited_game = edited_game.replace('[Result "0-1"]', '[Result "*"]')
    edited_game = edited_game.replace('[Result "1/2-1/2"]', '[Result "*"]')
    pgn2 = tmp_path / "edited.pgn"
    pgn2.write_text("\n".join([head, edited_game] + rest[1:]))
    summary = import_ok(pgn2, store)
    assert summary["games_updated"] == 1
    assert summary["games_imported"] == 0
    assert summary["games_already_imported"] == GAME_COUNT - 1
    ids_after = {e["game_id"]: e["record"] for e in index_entries(store)}
    assert set(ids_after) == set(ids_before)  # identity stable under edit
    changed = [g for g in ids_after if ids_after[g] != ids_before[g]]
    assert len(changed) == 1
    # the stale record version is pinned: present after the update
    # commit, removed by recovery at the start of the NEXT run
    stale = ids_before[changed[0]]
    assert (store / "games" / stale).exists()
    import_ok(pgn2, store)  # no-op run triggers recovery
    assert not (store / "games" / stale).exists()
    assert len(list((store / "games").glob("*.json"))) == GAME_COUNT


def _split_games(corpus: str) -> list[str]:
    parts = corpus.split("\n[Event ")
    return [parts[0]] + ["[Event " + p for p in parts[1:]]


def _corrupt_game(game: str) -> str:
    """Inject a malformed move token before the result token of exactly
    this game - a deterministic single-game defect."""
    lines = game.rstrip("\n").split("\n\n")
    tokens = lines[1].split()
    assert tokens[-1] in {"1-0", "0-1", "1/2-1/2", "*"}
    tokens.insert(-1, "Ke9")
    return lines[0] + "\n\n" + " ".join(tokens) + "\n"


def _telemetry_events(store: Path) -> list[str]:
    path = store / "telemetry.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


def _snapshot(store: Path) -> dict:
    """Externally inspectable committed state: index entries, record
    bytes, telemetry event sequence."""
    return {
        "index": index_entries(store),
        "records": {f.name: f.read_bytes() for f in (store / "games").glob("*.json")},
        "telemetry": _telemetry_events(store),
    }


def test_corrupted_game_refusal_preserves_committed_prefix(tmp_path):
    games = _split_games(CORPUS)
    assert len(games) == GAME_COUNT

    # reference: clean import gives the expected state for every prefix.
    # One shared corpus path is used for the reference AND every case so
    # provenance.retrieval_detail (the importing file) matches byte-for-byte.
    corpus_pgn = tmp_path / "corpus.pgn"
    corpus_pgn.write_text(CORPUS)
    ref = tmp_path / "ref"
    import_ok(corpus_pgn, ref)
    ref_snap = _snapshot(ref)

    # (case, expected number of committed games)
    cases: list[tuple[str, int]] = []
    # first-game corruption: zero commits
    cases.append((_corrupt_game(games[0]) + "\n" + "\n".join(games[1:]), 0))
    # middle-game corruption: exactly the preceding two survive
    cases.append(("\n".join(games[:2]) + "\n" + _corrupt_game(games[2])
                  + "\n" + "\n".join(games[3:]), 2))
    # last-game corruption: all but the last survive
    cases.append(("\n".join(games[:5]) + "\n" + _corrupt_game(games[5]), 5))
    # truncation after five complete games: exactly those five survive
    cases.append(("\n".join(games[:5]) + "\n[Event \"Property Cup\"]\n[White \"cut\"]\n\n1. e", 5))

    for i, (bad_corpus, prefix_len) in enumerate(cases):
        pgn = corpus_pgn
        pgn.write_text(bad_corpus)
        store = tmp_path / f"bs{i}"
        cp = run_cli(str(pgn), "--store", str(store),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert cp.stdout == ""
        assert "Traceback" not in cp.stderr
        err = json.loads(cp.stderr)
        assert err["code"] in {"malformed_request", "illegal_move"}

        # EXACTLY the committed prefix survives - no earlier record
        # removed or changed, no later record committed
        if prefix_len == 0:
            assert not (store / "index.json").exists() or index_entries(store) == []
            assert not (store / "games").exists() or \
                list((store / "games").glob("*.json")) == []
            assert _telemetry_events(store) == ["import.started"]
        else:
            snap = _snapshot(store)
            expected_index = ref_snap["index"][:prefix_len]
            assert snap["index"] == expected_index
            expected_records = {e["record"]: ref_snap["records"][e["record"]]
                                for e in expected_index}
            assert snap["records"] == expected_records  # byte-identical
            assert snap["telemetry"] == \
                ["import.started"] + ["import.game_stored"] * prefix_len

        # resume: only the missing games import; survivors are duplicates.
        # The corrected corpus is written over the SAME path so survivor
        # provenance (retrieval_detail names the importing file) stays
        # byte-identical to the reference.
        pgn.write_text(CORPUS)
        summary = import_ok(pgn, store)
        assert summary["games_updated"] == 0
        assert summary["games_already_imported"] == prefix_len
        assert summary["games_imported"] == GAME_COUNT - prefix_len
        assert _snapshot(store)["records"] == ref_snap["records"]
        assert index_entries(store) == ref_snap["index"]


def test_header_sparse_distinct_games_never_replace(tmp_path):
    rng = random.Random(SEED + 7)
    # same sparse tags (identity headers incomplete), different movetext
    g1 = _game_pgn(rng, 1, sparse=True)
    g2 = _game_pgn(rng, 1, sparse=True)
    while g2.split("\n\n", 1)[1] == g1.split("\n\n", 1)[1]:
        g2 = _game_pgn(rng, 1, sparse=True)
    pgn = tmp_path / "sparse.pgn"
    pgn.write_text(g1 + "\n" + g2)
    store = tmp_path / "s"
    summary = import_ok(pgn, store)
    assert summary["games_imported"] == 2
    assert summary["games_updated"] == 0
    assert len({e["game_id"] for e in index_entries(store)}) == 2
    assert len(list((store / "games").glob("*.json"))) == 2
