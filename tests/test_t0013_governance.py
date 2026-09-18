"""T0013: GOVERNANCE - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_governance_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "GOVERNANCE.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_governance_content_exact():
    text = " ".join((ROOT / "GOVERNANCE.md").read_text().lower().split())
    assert "project owner" in text
    assert "lazy consensus" in text
    assert "not subject to convenience overrides" in text
    assert "unverified" in text
