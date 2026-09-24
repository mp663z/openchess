"""T0542: Import/PGN/E2E restart - crash the SHIPPED import-pgn CLI at every
persisted write step, restart it in a fresh process, and check the
persisted state on disk.

The crash harness runs `ingest.import_pgn.main` in a subprocess with the
filesystem writes it performs counted (Path.write_text, os.replace, and
telemetry appends) and kills the process with os._exit(137) at write N:
either just BEFORE the write, or after writing half of the payload (a torn
temp file). Nothing else in the shipped path is changed. The restart is
the plain shipped command `python -m ingest.import_pgn` in a new process.

Expected behaviour (import.yaml atomicity/idempotency and the module's
crash-recovery contract):
- in every crash window the store reads as pre-commit or fully committed:
  the index is an in-order prefix of the clean run's index, every entry
  names a present record byte-identical to the clean run's, and the
  telemetry never claims more stored games than the index commits;
- the restart removes stale .tmp files and unreferenced candidates before
  any new telemetry, imports exactly the missing games (committed ones are
  already-imported no-ops) and ends byte-identical to a clean single run
  (games and index), with an exact summary and telemetry;
- an update crashed before its index commit leaves the OLD version
  readable; the restart commits the new one.

Adjacent negatives: a restart with a refused source leaves the crash
residue byte-identical (refusal happens before recovery); a restart whose
corpus has a corrupted later game recovers, commits the valid prefix and
refuses; a dangling index entry after a crash is refused untouched.
Source mutants of ingest/import_pgn.py that break crash safety are each
killed by this battery.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_t0541_import_pgn_property import _corpus, _corrupt_game, _split_games

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "ingest" / "import_pgn.py"
RETRIEVED_AT = "2026-09-24T00:00:00Z"
CRASHED = 137

HARNESS = r"""
import os, pathlib, sys
N = int(os.environ.get("CRASH_AT", "0"))
MODE = os.environ.get("CRASH_MODE", "before")
count = [0]

kinds = []

def tick(torn=None, kind="x"):
    kinds.append(kind)
    count[0] += 1
    if count[0] == N:
        if torn is not None:
            torn()
        os._exit(137)

_wt = pathlib.Path.write_text
def write_text(self, data, *a, **k):
    tick((lambda: _wt(self, data[: len(data) // 2], *a, **k)) if MODE == "torn" else None, "w")
    return _wt(self, data, *a, **k)
pathlib.Path.write_text = write_text

_replace = os.replace
def replace(a, b):
    tick()
    return _replace(a, b)
os.replace = replace

_open = pathlib.Path.open
def open_(self, mode="r", *a, **k):
    if "a" in mode:
        tick()
    return _open(self, mode, *a, **k)
pathlib.Path.open = open_

src = os.environ.get("MUTANT_SRC")
if src:
    import types
    import ingest
    mod = types.ModuleType("ingest.import_pgn")
    mod.__file__ = os.environ["PRODUCTION"]
    sys.modules["ingest.import_pgn"] = mod
    ingest.import_pgn = mod
    exec(compile(open(src).read(), os.environ["PRODUCTION"], "exec"), mod.__dict__)
from ingest import import_pgn
code = import_pgn.main(sys.argv[1:])
if os.environ.get("COUNT_FILE"):
    pathlib.Path(os.environ["COUNT_FILE"]).write_bytes(",".join(kinds).encode())
sys.exit(code)
"""


def _args(pgn, store, *extra):
    return [str(pgn), "--store", str(store), "--retrieved-at", RETRIEVED_AT, *extra]


def run_shipped(pgn, store, *extra):
    return subprocess.run(
        [sys.executable, "-m", "ingest.import_pgn", *_args(pgn, store, *extra)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def run_harness(pgn, store, crash_at=0, mode="before", mutant=None, count_file=None, extra=()):
    env = dict(os.environ, CRASH_AT=str(crash_at), CRASH_MODE=mode, PRODUCTION=str(PRODUCTION))
    if mutant:
        env["MUTANT_SRC"] = str(mutant)
    if count_file:
        env["COUNT_FILE"] = str(count_file)
    return subprocess.run(
        [sys.executable, "-c", HARNESS, *_args(pgn, store, *extra)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def tree(store):
    return {
        str(f.relative_to(store)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(store.rglob("*"))
        if f.is_file()
    }


def raw_tree(store):
    return {
        str(f.relative_to(store)): f.read_bytes() for f in sorted(store.rglob("*")) if f.is_file()
    }


def committed(store):
    """(index entries, games/ tree) - the durable committed state."""
    index_path = store / "index.json"
    try:
        index = json.loads(index_path.read_text()) if index_path.exists() else []
    except ValueError:
        index = None  # a torn manifest is visible: never a valid window
    games = {k: v for k, v in tree(store).items() if k.startswith("games/")}
    return index, games


def events(store):
    path = store / "telemetry.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


# -- scenarios -----------------------------------------------------------------


def _write(path, text):
    path.write_text(text)
    return path


def _edited(corpus, which):
    """Corpus with a non-identity tag added to game WHICH: same header
    identity, different content, so the import is a recorded update."""
    games = _split_games(corpus)
    games[which] = games[which].replace(
        '[Event "Property Cup"]', '[Event "Property Cup"]\n[Annotator "restart"]', 1
    )
    return "\n".join(g.rstrip("\n") + "\n" for g in games)


class Scenario:
    """One import (optionally on top of a base import) with its clean
    reference store and write-event count."""

    def __init__(self, tmp, name, corpus, base=None, mutant=None):
        self.tmp, self.name, self.mutant = tmp, name, mutant
        self.pgn = _write(tmp / f"{name}.pgn", corpus)
        self.base = _write(tmp / f"{name}-base.pgn", base) if base else None
        self.ref = self.fresh("ref")
        count = tmp / f"{name}.count"
        cp = run_harness(self.pgn, self.ref, mutant=mutant, count_file=count)
        assert cp.returncode == 0, cp.stderr
        self.ref_summary = json.loads(cp.stdout)
        self.kinds = count.read_text().split(",")
        self.writes = len(self.kinds)
        self.ref_index, self.ref_games = committed(self.ref)

    def fresh(self, label):
        store = self.tmp / f"{self.name}-{label}"
        if self.base:
            cp = run_harness(self.base, store, mutant=self.mutant)
            assert cp.returncode == 0, cp.stderr
        return store


def _clean_games(store):
    """games/ records the index references (unreferenced ones are candidates)."""
    index, games = committed(store)
    names = {"games/" + e["record"] for e in index}
    return {k: v for k, v in games.items() if k in names}


def crash_then_restart(sc, crash_at, mode, shipped_restart=True):
    """Crash the import at write CRASH_AT, check the crash window, restart,
    check the recovered store. Returns a list of violated expectations."""
    bad = []
    store = sc.fresh(f"{mode}-{crash_at}")
    base_index, base_games = committed(store)
    base_events = events(store)
    cp = run_harness(sc.pgn, store, crash_at, mode, mutant=sc.mutant)
    if cp.returncode != CRASHED:
        return [f"no crash (exit {cp.returncode})"]
    index, _games = committed(store)
    if index is None:
        return ["torn index.json visible after the crash"]
    ref_ids = [e["game_id"] for e in sc.ref_index]
    ids = [e["game_id"] for e in index]
    base_ids = [e["game_id"] for e in base_index]
    # crash window: pre-commit or fully committed per game
    if sc.base is None and ids != ref_ids[: len(ids)]:
        bad.append("index not an in-order prefix of the clean run")
    if sc.base is not None and ids != base_ids:
        bad.append("update changed index membership or order")
    for e in index:
        path = store / "games" / e["record"]
        if not path.exists():
            bad.append("index names a missing record")
            continue
        ok_versions = {
            sc.ref_games.get("games/" + e["record"]),
            base_games.get("games/" + e["record"]),
        }
        if tree(store).get("games/" + e["record"]) not in ok_versions:
            bad.append("committed record differs from every clean version")
    new_events = events(store)[len(base_events) :]
    stored = new_events.count("import.game_stored") + new_events.count("import.game_updated")
    newly = sum(1 for e in index if e not in base_index)
    if stored > newly:
        bad.append("telemetry claims a game the index never committed")
    # restart in a fresh process
    before_restart = events(store)
    if shipped_restart and sc.mutant is None:
        rp = run_shipped(sc.pgn, store)
    else:
        rp = run_harness(sc.pgn, store, mutant=sc.mutant)
    if rp.returncode != 0:
        return bad + [f"restart refused: {rp.stderr.strip()[:200]}"]
    summary = json.loads(rp.stdout)
    r_index, _ = committed(store)
    if r_index != sc.ref_index:
        bad.append("restart index differs from the clean run")
    if _clean_games(store) != {
        k: v for k, v in sc.ref_games.items() if k in {"games/" + e["record"] for e in sc.ref_index}
    }:
        bad.append("restart records differ from the clean run")
    if any(p.endswith(".tmp") for p in tree(store)):
        bad.append("stale .tmp survived the restart")
    total = (
        sc.ref_summary["games_imported"]
        + sc.ref_summary["games_already_imported"]
        + sc.ref_summary["games_updated"]
    )
    got_total = (
        summary["games_imported"] + summary["games_already_imported"] + summary["games_updated"]
    )
    if got_total != total:
        bad.append("restart summary count")
    done_before = sum(1 for e in index if e not in base_index)
    if sc.base is None and (summary["games_imported"], summary["games_already_imported"]) != (
        total - done_before,
        done_before,
    ):
        bad.append("restart did not import exactly the missing games")
    restart_events = events(store)[len(before_restart) :]
    if (
        not restart_events
        or restart_events[0] != "import.started"
        or restart_events[-1] != "import.completed"
    ):
        bad.append("restart telemetry frame")
    # one more run: every leftover candidate is recovered away, a pure no-op
    np_ = run_harness(sc.pgn, store, mutant=sc.mutant)
    if np_.returncode != 0:
        return bad + ["no-op rerun refused"]
    if committed(store) != (sc.ref_index, _clean_games(sc.ref)):
        bad.append("no-op rerun left state other than the clean committed state")
    return bad


# -- tests -----------------------------------------------------------------------

CORPUS = _corpus(20260924, 3)


def crash_points(sc):
    """Every write step crashed before it, and every temp write torn."""
    points = [(n, "before") for n in range(1, sc.writes + 1)]
    points += [(n, "torn") for n, kind in enumerate(sc.kinds, 1) if kind == "w"]
    return points


def sweep(sc, first_only=False):
    failures = {}
    for n, mode in crash_points(sc):
        bad = crash_then_restart(sc, n, mode)
        if bad:
            failures[f"{mode}-{n}"] = bad
            if first_only:
                break
    return failures


def _restart(sc, store, pgn=None, extra=()):
    pgn = sc.pgn if pgn is None else pgn
    if sc.mutant is None:
        return run_shipped(pgn, store, *extra)
    return run_harness(pgn, store, mutant=sc.mutant, extra=extra)


def _crash(sc, label, n, mode):
    store = sc.fresh(label)
    cp = run_harness(sc.pgn, store, n, mode, mutant=sc.mutant)
    return store, cp.returncode == CRASHED


# write positions, from the clean run's write kinds (x = rename/append,
# w = temp write): a new import is started, then per game record tmp,
# record rename, index tmp, index rename, stored line; an update run on
# the edited second game is started, duplicate, then the same 5 writes
NEW_GAME3_RECORD_TMP = 1 + 5 + 5 + 1
NEW_GAME2_INDEX_RENAME = 1 + 5 + 4
UPDATE_INDEX_RENAME = 2 + 4


def neg_old_version_kept(sc):
    """An update crashed just before its index rename leaves the OLD
    version indexed and readable; the restart commits the new one."""
    bad = []
    if sc.kinds[:UPDATE_INDEX_RENAME] != ["x", "x", "w", "x", "w", "x"]:
        return ["write layout changed"]
    base = sc.fresh("old-base")
    old_index, old_games = committed(base)
    store, crashed = _crash(sc, "old-version", UPDATE_INDEX_RENAME, "before")
    if not crashed:
        return ["no crash"]
    index, games = committed(store)
    if index != old_index or any(
        games.get("games/" + e["record"]) != old_games["games/" + e["record"]] for e in index
    ):
        bad.append("old version not readable before the update commit")
    if len(games) != len(old_games) + 1:
        bad.append("expected exactly one unreferenced candidate")
    if _restart(sc, store).returncode != 0 or committed(store)[0] != sc.ref_index:
        bad.append("restart did not commit the update")
    return bad


def neg_refused_source(sc):
    """A restart with an out-of-scenario source is refused before recovery:
    the crash residue stays byte-identical; a proper restart recovers."""
    store, crashed = _crash(sc, "refused", NEW_GAME2_INDEX_RENAME, "before")
    if not crashed:
        return ["no crash"]
    before = raw_tree(store)
    rp = _restart(sc, store, extra=("--source", "lichess-api"))
    bad = []
    if rp.returncode != 1 or json.loads(rp.stderr or "{}").get("code") != "unknown_rights":
        bad.append("out-of-scenario source not refused as unknown_rights")
    if raw_tree(store) != before:
        bad.append("refused restart touched the crash residue")
    if _restart(sc, store).returncode != 0 or committed(store) != (
        sc.ref_index,
        _clean_games(sc.ref),
    ):
        bad.append("proper restart did not recover")
    return bad


def neg_corrupted_later_game(sc, tmp):
    """Crash with game 3's record temp torn; restart on a corpus whose game 3
    is corrupted: recovery removes the torn temp, game 3 is refused, games
    1-2 stay committed, no summary."""
    store, crashed = _crash(sc, "corrupt", NEW_GAME3_RECORD_TMP, "torn")
    if not crashed or not list((store / "games").glob("*.tmp")):
        return ["no torn record temp"]
    games = _split_games(CORPUS)
    games[2] = _corrupt_game(games[2])
    bad_pgn = _write(tmp / "bad.pgn", "\n".join(g.rstrip("\n") + "\n" for g in games))
    before_events = events(store)
    rp = _restart(sc, store, pgn=bad_pgn)
    bad = []
    if rp.returncode != 1 or json.loads(rp.stderr or "{}").get("code") not in (
        "illegal_move",
        "malformed_request",
    ):
        bad.append("corrupted game not refused")
    index, stored = committed(store)
    if index != sc.ref_index[:2]:
        bad.append("committed prefix changed")
    if set(stored) != {"games/" + e["record"] for e in index or []}:
        bad.append("unreferenced record after recovery")
    if any(p.endswith(".tmp") for p in tree(store)):
        bad.append("stale .tmp survived recovery")
    if events(store)[len(before_events) :] != [
        "import.started",
        "import.game_duplicate",
        "import.game_duplicate",
    ]:
        bad.append("restart telemetry")
    if (store / "summary.json").exists():
        bad.append("summary written by a refused run")
    return bad


def neg_dangling_index(sc):
    """A referenced record deleted after a crash: the restart refuses
    (partial_visibility, code internal) and touches nothing."""
    store, crashed = _crash(sc, "dangling", NEW_GAME2_INDEX_RENAME, "before")
    if not crashed:
        return ["no crash"]
    index, _ = committed(store)
    (store / "games" / index[0]["record"]).unlink()
    before = raw_tree(store)
    rp = _restart(sc, store)
    bad = []
    if rp.returncode != 1 or json.loads(rp.stderr or "{}").get("code") != "internal":
        bad.append("dangling entry not refused as partial_visibility")
    if raw_tree(store) != before:
        bad.append("refused restart touched the store")
    return bad


@pytest.fixture(scope="module")
def new_import(tmp_path_factory):
    return Scenario(tmp_path_factory.mktemp("new"), "new", CORPUS)


@pytest.fixture(scope="module")
def update_import(tmp_path_factory):
    return Scenario(tmp_path_factory.mktemp("upd"), "upd", _edited(CORPUS, 1), base=CORPUS)


def test_clean_run_shape(new_import, update_import):
    assert new_import.ref_summary["games_imported"] == 3
    assert update_import.ref_summary == dict(
        update_import.ref_summary, games_imported=0, games_already_imported=2, games_updated=1
    )
    # 1 started + 5 per stored game + 1 completed + summary tmp write and rename
    assert new_import.writes == 1 + 5 * 3 + 1 + 2
    assert new_import.kinds.count("w") == 2 * 3 + 1


def test_crash_at_every_write_of_a_new_import_then_restart(new_import):
    assert sweep(new_import) == {}


def test_crash_at_every_write_of_an_update_then_restart(update_import):
    assert sweep(update_import) == {}


def test_update_crashed_before_its_commit_keeps_the_old_version(update_import):
    assert neg_old_version_kept(update_import) == []


def test_restart_with_a_refused_source_leaves_the_crash_residue(new_import):
    assert neg_refused_source(new_import) == []


def test_restart_with_a_corrupted_later_game_recovers_and_keeps_the_prefix(new_import, tmp_path):
    assert neg_corrupted_later_game(new_import, tmp_path) == []


def test_restart_with_a_dangling_index_entry_is_refused_untouched(new_import):
    assert neg_dangling_index(new_import) == []


# -- source mutants ----------------------------------------------------------------

MUTANTS = {
    "recovery-keeps-tmp": (
        'for tmp in list(store_root.glob("*.tmp")) + list(games_dir.glob("*.tmp")):',
        "for tmp in []:",
    ),
    "recovery-keeps-candidates": (
        "rec.unlink()  # never-committed candidate version",
        "pass  # never-committed candidate version",
    ),
    "index-before-record": (
        "    _atomic_write(games_dir / _record_filename(record),\n"
        "                  json.dumps(record, indent=1))\n"
        "    _atomic_write(index_path, json.dumps(index, indent=1))\n",
        "    _atomic_write(index_path, json.dumps(index, indent=1))\n"
        "    _atomic_write(games_dir / _record_filename(record),\n"
        "                  json.dumps(record, indent=1))\n",
    ),
    "write-in-place": (
        "    tmp.write_text(payload)\n    os.replace(tmp, path)\n",
        "    path.write_text(payload)\n",
    ),
    "telemetry-before-commit": (
        "        _commit_game(record, new_index, games_dir, index_path)\n"
        "        index = new_index\n"
        '        emit("import.game_stored")\n',
        '        emit("import.game_stored")\n'
        "        _commit_game(record, new_index, games_dir, index_path)\n"
        "        index = new_index\n",
    ),
}


def battery_red(new, upd, tmp):
    """True as soon as any part of the battery fails under the scenarios'
    code (production or a mutant)."""
    checks = (
        lambda: neg_corrupted_later_game(new, tmp),
        lambda: neg_refused_source(new),
        lambda: neg_dangling_index(new),
        lambda: neg_old_version_kept(upd),
        lambda: sweep(new, first_only=True),
        lambda: sweep(upd, first_only=True),
    )
    for check in checks:
        try:
            if check():
                return True
        except Exception:  # noqa: BLE001 - a crash of the check is a kill
            return True
    return False


@pytest.mark.parametrize("name", list(MUTANTS))
def test_source_mutant_is_killed(name, tmp_path):
    before, after = MUTANTS[name]
    source = PRODUCTION.read_text()
    assert source.count(before) == 1, name
    mutant = tmp_path / "import_pgn_mutant.py"
    mutant.write_text(source.replace(before, after))
    try:
        new = Scenario(tmp_path, "new", CORPUS, mutant=mutant)
        upd = Scenario(tmp_path, "upd", _edited(CORPUS, 1), base=CORPUS, mutant=mutant)
    except AssertionError:
        return  # the clean run itself fails: killed
    assert battery_red(new, upd, tmp_path)
