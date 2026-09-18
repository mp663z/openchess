import json
from pathlib import Path

import pytest

from tools.dag import DagError, claimable, cmd_complete, load, save_atomic, verify

REAL_BOARD_PATH = Path(__file__).resolve().parent.parent / "tasks" / "dag.json"


def make_board(tmp_path: Path) -> Path:
    board = {
        "schema_version": "test",
        "tasks": [
            {
                "id": "T1",
                "phase": "P0",
                "milestone": "M",
                "week": "0",
                "track": "validation",
                "title": "root",
                "dependencies": [],
                "acceptance": "a",
                "verification": "v",
                "status": "todo",
                "spine_outcome": "non-spine enabler",
                "roadmap_layer": "L1",
            },
            {
                "id": "T2",
                "phase": "P0",
                "milestone": "M",
                "week": "0",
                "track": "data",
                "title": "child",
                "dependencies": ["T1"],
                "acceptance": "a",
                "verification": "v",
                "status": "todo",
                "spine_outcome": "non-spine enabler",
                "roadmap_layer": "L1",
            },
        ],
    }
    p = tmp_path / "dag.json"
    p.write_text(json.dumps(board))
    return p


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_verify_ok(tmp_path):
    board = load(make_board(tmp_path))
    assert verify(board) == []


def test_claimable_respects_dependencies(tmp_path):
    board = load(make_board(tmp_path))
    assert [t["id"] for t in claimable(board)] == ["T1"]
    board["tasks"][0]["status"] = "done"
    board["tasks"][0]["done_sha"] = "abc123"
    board["tasks"][0]["evidence_manifest"] = "evidence/T1.md"
    assert [t["id"] for t in claimable(board)] == ["T2"]


def test_complete_requires_sha_and_evidence(tmp_path):
    path = make_board(tmp_path)
    board = load(path)
    with pytest.raises(DagError):
        cmd_complete(board, Args(task_id="T1", sha="", evidence="x"))
    with pytest.raises(DagError):
        cmd_complete(board, Args(task_id="T1", sha="abc", evidence=""))
    cmd_complete(board, Args(task_id="T1", sha="abc123", evidence="evidence/T1.md"))
    save_atomic(path, board)
    reloaded = load(path)
    t1 = reloaded["tasks"][0]
    assert t1["status"] == "done"
    assert t1["done_sha"] == "abc123"


def test_complete_blocked_by_unmet_dependency(tmp_path):
    board = load(make_board(tmp_path))
    with pytest.raises(DagError):
        cmd_complete(board, Args(task_id="T2", sha="abc", evidence="e"))


def test_cycle_detected(tmp_path):
    board = load(make_board(tmp_path))
    board["tasks"][0]["dependencies"] = ["T2"]
    assert any("cycle" in p for p in verify(board))


def test_done_without_sha_flagged(tmp_path):
    board = load(make_board(tmp_path))
    board["tasks"][0]["status"] = "done"
    assert any("done_sha" in p for p in verify(board))


def test_real_board_is_valid():
    board = load(Path(__file__).resolve().parent.parent / "tasks" / "dag.json")
    assert verify(board) == []
    assert len(board["tasks"]) == 5257


def test_duplicate_ids_rejected(tmp_path):
    board = json.loads((REAL_BOARD_PATH).read_text())
    board["tasks"].append(dict(board["tasks"][0]))
    with pytest.raises(DagError, match="duplicate task ids"):
        verify(board)


def test_empty_acceptance_rejected(tmp_path):
    board = json.loads((REAL_BOARD_PATH).read_text())
    board["tasks"][0]["acceptance"] = "   "
    problems = verify(board)
    assert any("empty acceptance" in p for p in problems)


def test_empty_verification_rejected(tmp_path):
    board = json.loads((REAL_BOARD_PATH).read_text())
    board["tasks"][0]["verification"] = ""
    problems = verify(board)
    assert any("empty verification" in p for p in problems)


def test_empty_title_rejected(tmp_path):
    board = json.loads((REAL_BOARD_PATH).read_text())
    board["tasks"][0]["title"] = ""
    problems = verify(board)
    assert any("empty title" in p for p in problems)
