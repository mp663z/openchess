"""T0012: CONTRIBUTING - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_contributing_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "CONTRIBUTING.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_contributing_content_exact():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    assert "evidence/TNNNN.md" in text
    assert "UNVERIFIED is never done" in text
    assert "CLA.md" in text and "DCO.md" in text
    assert "AGPL-3.0-or-later" in text
