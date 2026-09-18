"""T0010 v3: CLA gate - registry validation and PR classification."""

import json
from pathlib import Path

import pytest
import yaml

from tools import cla_check

ROOT = Path(__file__).resolve().parent.parent


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


def test_real_registry_validates():
    reg = cla_check.load_registry()
    assert isinstance(reg, dict)


def test_registry_rejects_bad_handle(tmp_path):
    p = tmp_path / "reg.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "acceptances": [
        {"github_handle": "bad handle!", "accepted_at": "2026-09-19T00:00:00Z",
         "reference": "example/project#1"},
    ]}))
    with pytest.raises(cla_check.ClaError, match="github_handle"):
        cla_check.load_registry(p)


def test_registry_rejects_non_iso_timestamp(tmp_path):
    p = tmp_path / "reg.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "acceptances": [
        {"github_handle": "octocat", "accepted_at": "yesterday",
         "reference": "example/project#1"},
    ]}))
    with pytest.raises(cla_check.ClaError, match="ISO-8601"):
        cla_check.load_registry(p)


def test_registry_rejects_duplicate_and_bad_reference(tmp_path):
    p = tmp_path / "reg.yaml"
    entry = {"github_handle": "octocat", "accepted_at": "2026-09-19T00:00:00Z",
             "reference": "see chat"}
    p.write_text(yaml.safe_dump({"schema_version": 1, "acceptances": [entry]}))
    with pytest.raises(cla_check.ClaError, match="reference"):
        cla_check.load_registry(p)
    p.write_text(yaml.safe_dump({"schema_version": 1, "acceptances": [
        {**entry, "reference": "example/project#1"},
        {**entry, "reference": "example/project#2"},
    ]}))
    with pytest.raises(cla_check.ClaError, match="duplicate"):
        cla_check.load_registry(p)


def test_de_minimis_classification():
    small = [("M", "README.md")]
    assert cla_check.is_de_minimis(7, small)
    assert not cla_check.is_de_minimis(11, small)  # too many lines
    assert not cla_check.is_de_minimis(7, [("A", "new.md")])  # new file
    assert not cla_check.is_de_minimis(7, [("M", "CLA.md")])  # protected file
    assert not cla_check.is_de_minimis(7, [("M", "tools/dag.py")])  # protected path


def test_pr_gate_registered_and_unregistered(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cla_check, "changed_files", lambda base: [("M", "tools/dag.py")]
    )
    monkeypatch.setattr(cla_check, "REGISTRY", tmp_path / "reg.yaml")
    monkeypatch.setattr(cla_check, "load_registry", lambda path=None: {
        "trusteddev": {"accepted_at": "2026-09-19T00:00:00Z"}
    })
    ok = cla_check.check_pr(write_event(tmp_path, login="TrustedDev"), "main")
    assert "CLA acceptance recorded" in ok
    with pytest.raises(cla_check.ClaError, match="without a recorded"):
        cla_check.check_pr(write_event(tmp_path, login="stranger"), "main")


def test_pr_gate_de_minimis_needs_no_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(cla_check, "changed_files", lambda base: [("M", "README.md")])
    ok = cla_check.check_pr(
        write_event(tmp_path, login="stranger", additions=3, deletions=1), "main"
    )
    assert "de minimis" in ok


def test_ci_wires_the_gate():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python tools/cla_check.py" in ci
