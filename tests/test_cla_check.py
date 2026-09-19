"""T0010 v4: CLA gate - base-ref registry, strict validation, verification."""

import json
from pathlib import Path

import pytest
import yaml

from tools import cla_check

ROOT = Path(__file__).resolve().parent.parent
REPO = "example/project"  # test repo identity via GITHUB_REPOSITORY


@pytest.fixture(autouse=True)
def repo_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)


def write_event(tmp_path, login="octocat", additions=5, deletions=2):
    p = tmp_path / "event.json"
    p.write_text(json.dumps({
        "pull_request": {
            "user": {"login": login},
            "additions": additions,
            "deletions": deletions,
        }
    }))
    return str(p)


def entry(handle="octocat", ts="2026-09-19T00:00:00Z", ref=None):
    return {
        "github_handle": handle,
        "accepted_at": ts,
        "reference": ref or f"{REPO}#7",
    }


def registry_yaml(entries):
    return yaml.safe_dump({"schema_version": 1, "acceptances": entries})


# --- registry validation -------------------------------------------------

def test_real_registry_validates():
    reg = cla_check.load_registry()
    assert isinstance(reg, dict)


def test_timestamp_must_be_a_real_aware_datetime():
    assert cla_check.parse_accepted_at("2026-09-19T00:00:00Z").tzinfo is not None
    with pytest.raises(cla_check.ClaError):  # regex-shape-only dates rejected
        cla_check.parse_accepted_at("2026-99-99T99:99:99Z")
    with pytest.raises(cla_check.ClaError):  # naive timestamps rejected
        cla_check.parse_accepted_at("2026-09-19T00:00:00")
    with pytest.raises(cla_check.ClaError):
        cla_check.parse_accepted_at("yesterday")


def test_reference_restricted_to_this_repo():
    assert cla_check._parse_reference(f"{REPO}#7", REPO) == 7
    assert cla_check._parse_reference(
        f"https://github.com/{REPO}/pull/7", REPO) == 7
    for bad in ("https://evil.invalid/fake", "other/repo#7",
                f"https://github.com/other/{REPO.split('/')[1]}/pull/7",
                "see chat", f"{REPO}#abc"):
        with pytest.raises(cla_check.ClaError):
            cla_check._parse_reference(bad, REPO)


def test_registry_rejects_bad_handle_and_duplicates(tmp_path):
    p = tmp_path / "reg.yaml"
    p.write_text(registry_yaml([entry(handle="bad handle!")]))
    with pytest.raises(cla_check.ClaError, match="github_handle"):
        cla_check.load_registry(p)
    p.write_text(registry_yaml([entry(), entry()]))
    with pytest.raises(cla_check.ClaError, match="duplicate"):
        cla_check.load_registry(p)


# --- acceptance verification ----------------------------------------------

def fake_api(pr_author="octocat", body="", comments=None):
    def _api(endpoint):
        if endpoint.startswith(f"repos/{REPO}/pulls/"):
            return {"user": {"login": pr_author}, "body": body}
        return comments or []
    return _api


def test_verify_acceptance_paths(monkeypatch):
    e = entry()
    monkeypatch.setattr(cla_check, "_gh_api",
                        fake_api(body=cla_check.ACCEPTANCE_STATEMENT))
    cla_check.verify_acceptance("octocat", e)  # body statement passes
    monkeypatch.setattr(cla_check, "_gh_api", fake_api(body="hello"))
    with pytest.raises(cla_check.ClaError, match="acceptance statement"):
        cla_check.verify_acceptance("octocat", e)
    monkeypatch.setattr(cla_check, "_gh_api", fake_api(pr_author="mallory"))
    with pytest.raises(cla_check.ClaError, match="authored by"):
        cla_check.verify_acceptance("octocat", e)
    monkeypatch.setattr(
        cla_check, "_gh_api",
        fake_api(body="", comments=[
            {"user": {"login": "octocat"},
             "body": f"\n{cla_check.ACCEPTANCE_STATEMENT}\n"},
        ]))
    cla_check.verify_acceptance("octocat", e)  # standalone-line comment passes
    monkeypatch.setattr(
        cla_check, "_gh_api",
        fake_api(body="", comments=[
            {"user": {"login": "maintainer"},
             "body": cla_check.ACCEPTANCE_STATEMENT},
        ]))
    with pytest.raises(cla_check.ClaError):  # someone else's statement
        cla_check.verify_acceptance("octocat", e)


# --- PR gate ---------------------------------------------------------------

def test_de_minimis_classification():
    small = [("M", "README.md")]
    assert cla_check.is_de_minimis(7, small)
    assert not cla_check.is_de_minimis(11, small)
    assert not cla_check.is_de_minimis(7, [("A", "new.md")])
    assert not cla_check.is_de_minimis(7, [("M", "CLA.md")])
    assert not cla_check.is_de_minimis(7, [("M", "tools/dag.py")])
    assert not cla_check.is_de_minimis(7, [("M", "data/cla-acceptances.yaml")])


def test_pr_gate_registered_author_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(cla_check, "changed_files",
                        lambda base: [("M", "tools/dag.py")])
    monkeypatch.setattr(cla_check, "load_base_registry",
                        lambda base: {"octocat": entry()})
    monkeypatch.setattr(cla_check, "verify_acceptance",
                        lambda h, e: None)
    ok = cla_check.check_pr(write_event(tmp_path), "main")
    assert "verified" in ok


def test_pr_gate_self_entry_in_current_pr_does_not_authorize(
        tmp_path, monkeypatch):
    """Attack: first-time contributor adds their own registry entry in the
    same PR. The base registry has no entry, so the gate must fail even
    though the PR head contains one."""
    monkeypatch.setattr(cla_check, "changed_files", lambda base: [
        ("M", "tools/dag.py"), ("M", "data/cla-acceptances.yaml"),
    ])
    monkeypatch.setattr(cla_check, "load_base_registry", lambda base: {})
    # the PR head registry DOES contain the attacker's entry:
    assert "mallory" in cla_check.validate_registry(
        yaml.safe_load(registry_yaml([entry(handle="mallory")])) or {})
    with pytest.raises(cla_check.ClaError, match="BASE registry"):
        cla_check.check_pr(write_event(tmp_path, login="mallory"), "main")


def test_pr_gate_unregistered_and_de_minimis(tmp_path, monkeypatch):
    monkeypatch.setattr(cla_check, "changed_files",
                        lambda base: [("M", "tools/dag.py")])
    monkeypatch.setattr(cla_check, "load_base_registry", lambda base: {})
    with pytest.raises(cla_check.ClaError, match="BASE registry"):
        cla_check.check_pr(write_event(tmp_path, login="stranger"), "main")
    monkeypatch.setattr(cla_check, "changed_files",
                        lambda base: [("M", "README.md")])
    ok = cla_check.check_pr(
        write_event(tmp_path, login="stranger", additions=3, deletions=1),
        "main")
    assert "de minimis" in ok


def test_ci_wires_the_gate():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python tools/cla_check.py" in ci


def test_acceptance_must_be_standalone_line():
    s = cla_check.ACCEPTANCE_STATEMENT
    assert cla_check._has_acceptance(f"\n  {s}  \n")  # whitespace ok
    assert not cla_check._has_acceptance(
        f'I do not agree; the requested phrase was "{s}"')
    assert not cla_check._has_acceptance(f"please write: {s}")
    assert not cla_check._has_acceptance(f"{s} today")
    assert not cla_check._has_acceptance(f'"{s}"')
    assert not cla_check._has_acceptance("")
    assert not cla_check._has_acceptance(None)


def _git(args, cwd, env=None):
    import os
    import subprocess
    if env is None:
        # Never inherit GIT_* from a hook/CI context: GIT_DIR would override
        # cwd and operate on the surrounding real repository.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True,
                          env=env, timeout=60).stdout


def test_changed_files_never_reshallows_a_full_clone(tmp_path, monkeypatch):
    """Regression for CI run 35414644614: `git fetch --depth=1` inside
    changed_files re-shallowed the full CI checkout and the three-dot diff
    then failed with 'no merge base' (exit 128)."""
    import os
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", "-q", str(origin)], cwd=tmp_path)
    seed = tmp_path / "seed"
    _git(["init", "-q", str(seed)], cwd=tmp_path)
    _git(["-C", str(seed), "config", "user.email", "t@example.com"], cwd=tmp_path)
    _git(["-C", str(seed), "config", "user.name", "t"], cwd=tmp_path)
    (seed / "base.txt").write_text("base\n")
    _git(["-C", str(seed), "add", "-A"], cwd=tmp_path)
    _git(["-C", str(seed), "commit", "-q", "-m", "base"], cwd=tmp_path)
    _git(["-C", str(seed), "branch", "-M", "main"], cwd=tmp_path)
    _git(["-C", str(seed), "push", "-q", str(origin), "main"], cwd=tmp_path)
    _git(["-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"],
         cwd=tmp_path)
    work = tmp_path / "work"
    _git(["clone", "-q", str(origin), str(work)], cwd=tmp_path)
    _git(["-C", str(work), "config", "user.email", "t@example.com"], cwd=tmp_path)
    _git(["-C", str(work), "config", "user.name", "t"], cwd=tmp_path)
    # PR head: a merge commit whose second parent carries the change,
    # mirroring refs/pull/N/merge in CI.
    _git(["-C", str(work), "checkout", "-q", "-b", "pr"], cwd=tmp_path)
    (work / "pr.txt").write_text("pr\n")
    _git(["-C", str(work), "add", "-A"], cwd=tmp_path)
    _git(["-C", str(work), "commit", "-q", "-m", "pr change"], cwd=tmp_path)
    _git(["-C", str(work), "checkout", "-q", "main"], cwd=tmp_path)
    _git(["-C", str(work), "merge", "-q", "--no-ff", "-m", "merge", "pr"],
         cwd=tmp_path)
    assert not (work / ".git" / "shallow").exists(), "fixture must start full"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cla_check, "ROOT", work)
    for var in list(os.environ):
        if var.startswith("GIT_"):
            monkeypatch.delenv(var, raising=False)
    files = cla_check.changed_files("main")
    assert ("A", "pr.txt") in files
    assert not (work / ".git" / "shallow").exists(), (
        "changed_files must not re-shallow a full clone"
    )
