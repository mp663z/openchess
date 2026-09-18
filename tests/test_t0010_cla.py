"""T0010: contributor license agreement - lint, content, registry, mutation."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_cla_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout


def test_cla_grants_present():
    text = " ".join((ROOT / "CLA.md").read_text().lower().split())
    assert "grant of copyright license" in text
    assert "grant of patent license" in text
    assert "irrevocable" in text
    assert "patent litigation" in text


def test_sublicensing_locked_to_agpl_only():
    text = " ".join((ROOT / "CLA.md").read_text().lower().split())
    assert "only under agpl-3.0-or-later" in text
    assert "no right to relicense" in text
    # the open-ended relicensing option must be gone
    assert "another license approved" not in text


def test_acceptance_registry_schema():
    reg = yaml.safe_load((ROOT / "data/cla-acceptances.yaml").read_text())
    assert reg["schema_version"] == 1
    assert isinstance(reg["acceptances"], list)
    for entry in reg["acceptances"]:
        assert set(entry) >= {"github_handle", "accepted_at", "reference"}
    cla = " ".join((ROOT / "CLA.md").read_text().split())
    assert "data/cla-acceptances.yaml" in cla


def test_mutation_dropping_a_grant_heading_fails(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gdl", ROOT / "tools/governance_doc_lint.py"
    )
    gdl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gdl)
    mutated = (ROOT / "CLA.md").read_text().replace(
        "## 3. Grant of patent license", "## 3. Patent stuff", 1
    )
    p = tmp_path / "CLA.md"
    p.write_text(mutated)
    errors = gdl.lint_doc(p, "CLA.md")
    assert any("grant of patent license" in e for e in errors)
