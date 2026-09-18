"""T0011: DCO / de minimis - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dco_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "DCO.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_dco_content_exact():
    text = (ROOT / "DCO.md").read_text().lower()
    assert "developer certificate of origin" in text
    assert "version 1.1" in text
    assert "signed-off-by" in text
    assert "10 changed lines" in text
    # de minimis must be bounded, not open-ended
    assert "all of the following" in text
    # no unenforceable CI-rejection claim, no Mechanical-Change exception
    assert "ci rejects" not in text
    assert "mechanical-change" not in text
    # real mechanism: maintainer verifies at review
    assert "maintainers verify" in text
