import json
from pathlib import Path

import pytest

from tools.dag import DagError, claimable, cmd_complete, index, load, save_atomic, verify

REAL_BOARD_PATH = Path(__file__).resolve().parent.parent / "tasks" / "dag.json"


SHA40 = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"

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
    board["tasks"][0]["done_sha"] = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
    board["tasks"][0]["evidence_manifest"] = "evidence/T1.md"
    assert [t["id"] for t in claimable(board)] == ["T2"]


def test_complete_requires_sha_and_evidence(tmp_path):
    path = make_board(tmp_path)
    board = load(path)
    with pytest.raises(DagError):
        cmd_complete(board, Args(task_id="T1", sha="", evidence="x"))
    with pytest.raises(DagError):
        cmd_complete(board, Args(task_id="T1", sha="abc", evidence="evidence/T1.md"))
    cmd_complete(board, Args(task_id="T1", sha=SHA40,
                             evidence="evidence/T1.md"))
    save_atomic(path, board)
    reloaded = load(path)
    t1 = reloaded["tasks"][0]
    assert t1["status"] == "done"
    assert t1["done_sha"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"


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
    problems = verify(board)
    assert any("duplicate task id" in p for p in problems)
    # claimable/index still raise fail-closed on duplicates
    with pytest.raises(DagError, match="duplicate task ids"):
        index(board)


def _minimal_task(**over):
    t = {
        "id": "T1", "title": "t", "acceptance": "a", "verification": "v",
        "status": "todo", "dependencies": [], "milestone": "m", "phase": "p",
        "roadmap_layer": "r", "spine_outcome": "s", "track": "x", "week": 1,
    }
    t.update(over)
    return t


def test_null_dependencies_reported_not_crash():
    problems = verify({"tasks": [_minimal_task(dependencies=None)]})
    assert any("dependencies must be a list" in p for p in problems)


def test_nonstring_dependency_rejected():
    problems = verify({"tasks": [_minimal_task(dependencies=[123])]})
    assert any("dependency must be a string" in p for p in problems)


def test_null_id_rejected():
    problems = verify({"tasks": [_minimal_task(id=None)]})
    assert any("non-empty string" in p for p in problems)


def test_empty_id_rejected():
    problems = verify({"tasks": [_minimal_task(id="")]})
    assert any("non-empty string" in p for p in problems)


def test_nonstring_title_rejected():
    problems = verify({"tasks": [_minimal_task(title=123)]})
    assert any("title must be a string" in p for p in problems)


def test_tasks_not_a_list_rejected():
    assert verify({"tasks": None}) == ["board: tasks must be a list"]


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



def test_complete_rejects_short_and_nonhex_sha(tmp_path):
    board = load(make_board(tmp_path))
    for bad in ("abc123", "g" * 40, "A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4",
                SHA40 + "ff", SHA40[:-1]):
        with pytest.raises(DagError, match="40-hex"):
            cmd_complete(board, Args(task_id="T1", sha=bad,
                                     evidence="evidence/T1.md"))


def test_complete_rejects_wrong_evidence_path(tmp_path):
    board = load(make_board(tmp_path))
    for bad in ("", "evidence/T2.md", "manifests/T1.md", "evidence/t1.md",
                "evidence/T1.md "):
        with pytest.raises(DagError, match="evidence"):
            cmd_complete(board, Args(task_id="T1", sha=SHA40, evidence=bad))


def test_verify_flags_done_tasks_with_lax_records(tmp_path):
    board = load(make_board(tmp_path))
    board["tasks"][0]["status"] = "done"
    board["tasks"][0]["done_sha"] = "abc123"
    board["tasks"][0]["evidence_manifest"] = "somewhere/else.md"
    problems = verify(board)
    assert any("40-hex" in p for p in problems)
    assert any("evidence" in p for p in problems)
