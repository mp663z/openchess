"""pre-push tree guard: executable regression tests (PR #62 verifier cases).

Runs .githooks/tree-guard.sh in throwaway git repos: clean / staged
tracked / unstaged tracked / untracked / ignored-file cases.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / ".githooks" / "tree-guard.sh"


@pytest.fixture()
def repo(tmp_path):
    def git(*args, check=True):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=check
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "README.md").write_text("hello\n")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    return tmp_path


def run_guard(repo):
    return subprocess.run(
        ["bash", str(GUARD)], cwd=repo, capture_output=True, text=True
    )


def test_clean_tree_passes(repo):
    assert run_guard(repo).returncode == 0


def test_staged_tracked_change_fails(repo):
    (repo / "README.md").write_text("changed\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    r = run_guard(repo)
    assert r.returncode == 1 and "uncommitted tracked changes" in r.stderr


def test_unstaged_tracked_change_fails(repo):
    (repo / "README.md").write_text("changed\n")
    assert run_guard(repo).returncode == 1


def test_untracked_file_fails(repo):
    (repo / "accidental.py").write_text("broken\n")
    r = run_guard(repo)
    assert r.returncode == 1 and "accidental.py" in r.stderr


def test_ignored_file_allowed_by_design(repo):
    (repo / "ignored.txt").write_text("scratch\n")
    assert run_guard(repo).returncode == 0


def test_guard_is_sourced_by_pre_push():
    assert "tree-guard.sh" in (ROOT / ".githooks" / "pre-push").read_text()
