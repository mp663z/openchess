"""pre-push tree guard: executable regression tests (PR #62 verifier cases).

Runs .githooks/tree-guard.sh in throwaway git repos: clean / staged
tracked / unstaged tracked / untracked / ignored-file cases, plus the
hook-environment leak case (git exports GIT_DIR to hooks; test subprocesses
must never inherit it - that corrupted this worktree once for real).
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / ".githooks" / "tree-guard.sh"


def clean_env() -> dict:
    """Git exports GIT_DIR & friends when running hooks; without stripping
    them, a test's git calls in a temp dir would operate on the REAL repo
    (this corrupted the worktree once: fixture commits landing on HEAD).
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


@pytest.fixture()
def repo(tmp_path):
    def git(*args, check=True):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True,
            check=check, env=clean_env(),
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
        ["bash", str(GUARD)], cwd=repo, capture_output=True, text=True,
        env=clean_env(),
    )


def test_clean_tree_passes(repo):
    assert run_guard(repo).returncode == 0


def test_staged_tracked_change_fails(repo):
    (repo / "README.md").write_text("changed\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, env=clean_env())
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


def test_fixture_survives_hook_environment(repo, monkeypatch):
    """Simulate a pre-push invocation: GIT_DIR pointing at the real repo
    must not leak into fixture git calls."""
    monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
    (repo / "x.py").write_text("x\n")
    r = run_guard(repo)
    assert r.returncode == 1 and "x.py" in r.stderr


def test_guard_is_sourced_by_pre_push():
    assert "tree-guard.sh" in (ROOT / ".githooks" / "pre-push").read_text()
