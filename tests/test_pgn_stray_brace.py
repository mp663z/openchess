"""Regression for stray-brace PGN input: bounded subprocesses prevent a CI hang.

The empty-token guard is a defensive fallback; this battery directly checks
the stray-brace behavior, not the equivalence of other delimiters.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_t0537_import_pgn_contract import VALID_PGN

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = "1. e4 e5 2. Nf3 Nc6 1-0"


@pytest.mark.parametrize("movetext,expected", [
    ("} 1. e4 1-0", "unbalanced comment close"),
    ("1. e4 } e5 1-0", "unbalanced comment close"),
    ("1. e4 1-0 }", "unbalanced comment close"),
    ("}}} 1. e4 1-0", "unbalanced comment close"),
    ("1. e4 (1. d4 } d5) e5 1-0", "ok"),
])
def test_validator_stray_closing_brace_terminates_within_five_seconds(movetext, expected):
    fixture = VALID_PGN.replace(ORIGINAL, movetext)
    code = ("import sys\n"
            "from ingest.pgn import MalformedPGN, validate_pgn_game\n"
            "try:\n"
            "    game = validate_pgn_game(sys.stdin.read())\n"
            "except MalformedPGN as error:\n"
            "    print(type(error).__name__ + ': ' + str(error))\n"
            "else:\n"
            "    print('ok ' + ' '.join(game.san_moves))\n")
    completed = subprocess.run([sys.executable, "-c", code], input=fixture,
                               cwd=ROOT, capture_output=True, text=True, timeout=5, check=True)
    if expected == "ok":
        assert completed.stdout == "ok e4 e5\n"
    else:
        assert completed.stdout == f"MalformedPGN: {expected}\n"
    assert completed.stderr == ""


def test_import_path_stray_brace_refuses_without_record_or_summary(tmp_path):
    fixture = tmp_path / "stray.pgn"
    fixture.write_text(VALID_PGN.replace(ORIGINAL, "1. e4 } e5 1-0"))
    store = tmp_path / "store"
    cmd = [sys.executable, "-m", "ingest.import_pgn", str(fixture),
           "--store", str(store), "--source", "pgn-file",
           "--retrieved-at", "2026-09-25T00:00:00Z"]
    completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               timeout=5)
    assert completed.returncode != 0
    assert "malformed_request" in completed.stderr
    assert not list((store / "games").glob("*.json"))
    assert not (store / "summary.json").exists()
    assert not (store / "index.json").exists()


def test_refusal_locator_and_folder_rejection_for_stray_brace():
    # The reference intake also validates PGN. Keep the entire path in a
    # bounded child process so reverting the production guard cannot hang CI.
    code = r'''from tests.test_t0548_import_multi_pgn_contract import ReferenceStore
from tests.test_t0570_import_bad_tags_contract import PRODUCTION_BINDING, SCENARIO
from tests.test_t0636_import_folder_contract import _valid, reference_folder

game = _valid(2).replace("1. e4 e5 2. Nf3 Nc6 1-0", "1. e4 } e5 1-0")
store = ReferenceStore()
result = PRODUCTION_BINDING(game, store, scenario=SCENARIO)
assert result.code == "malformed_request"
assert result.marker["location"]["game_number"] == 1
assert store.rejections == [result] and store.records == {} and store.index == []
folder = ReferenceStore()
summary = reference_folder({"a.pgn": _valid(1) + "\n" + game + "\n" + _valid(3)}, folder)
assert len(folder.rejections) == 1
assert folder.rejections[0].file == "a.pgn"
assert folder.rejections[0].game_number == 2
assert folder.rejections[0].code == "malformed_request"
assert [folder.records[e["record"]]["tags"]["Round"] for e in folder.index] == ["1", "3"]
assert summary["total"]["games_imported"] == 2
print("refused-without-state")
'''
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                               capture_output=True, text=True, timeout=5, check=True)
    assert completed.stdout == "refused-without-state\n" and completed.stderr == ""
