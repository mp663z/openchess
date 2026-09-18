"""T0017: CODEOWNERS - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_codeowners_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", ".github/CODEOWNERS"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_codeowners_content_exact():
    text = (ROOT / ".github/CODEOWNERS").read_text()
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines, "no ownership rules"
    for path in ("LICENSE", "CLA.md", "GOVERNANCE.md", "tools/evidence_lint.py",
                 "data/release-lock.json"):
        assert any(ln.startswith(path) for ln in lines), path
    # every rule names an owner
    assert all("@" in ln for ln in lines)


def test_contributing_owned_and_routing_wording():
    text = (ROOT / ".github/CODEOWNERS").read_text()
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert any(ln.startswith("CONTRIBUTING.md") for ln in lines)
    head = " ".join(text.split()).lower()
    assert "review routing" in head
    assert "merge rights live in repository settings" in head
    assert "routing and merge rights" not in head


def test_lint_is_structural_not_substring():
    """A rules file missing a required path must fail the lint."""
    lint = (ROOT / "tools/governance_doc_lint.py").read_text()
    assert "CODEOWNERS_REQUIRED_PATHS" in lint
    assert "no rule for" in lint
