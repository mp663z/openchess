"""T0010: contributor license agreement - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cla_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "CLA.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_cla_grants_present():
    text = (ROOT / "CLA.md").read_text().lower()
    assert "grant of copyright license" in text
    assert "grant of patent license" in text
    assert "irrevocable" in text
    assert "agpl-3.0-or-later" in text
    assert "patent litigation" in text
