"""T0009: network source notice - lint, content, mutation, wiring tests."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_notice_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout


def test_notice_content_exact():
    text = (ROOT / "NOTICE").read_text()
    assert "AGPL-3.0-or-later" in text
    assert "section 13" in text.lower()
    assert "Corresponding Source" in text
    assert '"Source" in the project README' in text
    assert "release.yml" in text


def test_notice_promises_backed_by_repo_reality():
    # README carries the source link and the contact channel NOTICE cites
    readme = (ROOT / "README.md").read_text()
    assert "## Source" in readme and "github.com/" in readme.split("## Source")[1]
    assert "## Contact" in readme and "/issues" in readme.split("## Contact")[1]
    # the release workflow NOTICE cites exists and attaches SBOM + checksums
    wf = (ROOT / ".github/workflows/release.yml").read_text()
    assert "component_inventory" in wf and "checksums.sha256" in wf
    assert 'tags: ["v*"]' in wf


def test_lint_wired_into_ci_and_hook():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    hook = (ROOT / ".githooks/pre-push").read_text()
    assert "governance_doc_lint.py" in ci
    assert "governance_doc_lint.py" in hook


def test_mutation_without_required_heading_fails(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gdl", ROOT / "tools/governance_doc_lint.py"
    )
    gdl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gdl)
    text = (ROOT / "NOTICE").read_text()
    # drop the source-offer heading; prose still mentions "source offer"
    mutated = text.replace("## Source offer\n", "## Where to get things\n", 1)
    p = tmp_path / "NOTICE"
    p.write_text(mutated)
    errors = gdl.lint_doc(p, "NOTICE")
    assert any("source offer" in e for e in errors)
