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
             "body": f"noting: {cla_check.ACCEPTANCE_STATEMENT}"},
        ]))
    cla_check.verify_acceptance("octocat", e)  # author comment passes
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
