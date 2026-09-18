"""T0015: SECURITY - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_security_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "SECURITY.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_security_content_exact():
    text = " ".join((ROOT / "SECURITY.md").read_text().lower().split())
    assert "72 hours" in text
    assert "90 days" in text
    assert "sbom" in text
    assert "do not open a public issue" in text


def test_contact_and_sbom_claims_are_real():
    text = " ".join((ROOT / "SECURITY.md").read_text().lower().split())
    assert "private vulnerability reporting on github" in text
    assert "security contact field" not in text
    assert ".github/workflows/release.yml" in (ROOT / "SECURITY.md").read_text()
    assert "checksums.sha256" in text


def test_no_unimplemented_signing_claim():
    text = " ".join((ROOT / "SECURITY.md").read_text().lower().split())
    assert "signed" not in text
