"""T0014: TRADEMARK - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_trademark_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", "TRADEMARK.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_trademark_content_exact():
    text = (ROOT / "TRADEMARK.md").read_text().lower()
    assert "nominative fair use" in text
    assert "not endorsed by the project owner" in text
    assert "clearly different name" in text
    assert "agpl-3.0-or-later" in text


def test_contact_points_at_readme():
    text = " ".join((ROOT / "TRADEMARK.md").read_text().lower().split())
    assert "contact section of the readme" in text
    assert "contact published in the repository" not in text
