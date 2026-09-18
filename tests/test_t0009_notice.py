"""T0009: network source notice - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_notice_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "NOTICE"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_notice_content_exact():
    text = (ROOT / "NOTICE").read_text()
    assert "AGPL-3.0-or-later" in text
    assert "section 13" in text.lower()
    assert "Corresponding Source" in text
    assert "public source repository" in text
    assert "SBOM" in text
