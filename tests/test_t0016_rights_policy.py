"""T0016: rights policy - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_rights_policy_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "docs/rights-policy.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_rights_policy_content_exact():
    text = (ROOT / "docs/rights-policy.md").read_text().lower()
    assert "fail closed" in text
    assert "cc0" in text
    assert "research-only or non-commercial" in text
    assert "popularity in the community is not permission" in text
