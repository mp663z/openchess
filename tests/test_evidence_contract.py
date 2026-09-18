"""T0007: evidence contract lint enforces SHA + verification + commands; UNVERIFIED is not done."""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.evidence_lint import lint_file  # noqa: E402

SHA = "a" * 40

FULL = f"""# T9999 Example - evidence

Acceptance restated here. Artifact at {SHA}.
Verification: auto - green CI at {SHA}.
Commands: `pytest tests/test_example.py -q`. Environment: python 3.12.
"""


def write(tmp_path, text):
    p = tmp_path / "T9999.md"
    p.write_text(text)
    return p


def test_full_contract_file_passes(tmp_path):
    assert lint_file(write(tmp_path, FULL)) == []


def test_pending_marker_satisfies_sha_requirement(tmp_path):
    text = FULL.replace(SHA, "pending", 1)
    errors = lint_file(write(tmp_path, text))
    assert all("SHA" not in e for e in errors)


def test_missing_sha_and_pending_fails(tmp_path):
    text = FULL.replace(SHA, "merged recently")
    errors = lint_file(write(tmp_path, text))
    assert any("SHA" in e for e in errors)


def test_missing_verification_line_fails(tmp_path):
    text = FULL.replace(f"Verification: auto - green CI at {SHA}.\n", "")
    errors = lint_file(write(tmp_path, text))
    assert any("Verification:" in e for e in errors)


def test_missing_commands_line_fails(tmp_path):
    text = FULL.replace(
        "Commands: `pytest tests/test_example.py -q`. Environment: python 3.12.\n", ""
    )
    errors = lint_file(write(tmp_path, text))
    assert any("Commands:" in e for e in errors)


def test_done_claim_without_sha_fails():
    text = (FULL + "\nstatus: done\n").replace(SHA, "merged")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "T9999.md"
        p.write_text(text)
        errors = lint_file(p)
    assert any("UNVERIFIED" in e or "SHA" in e for e in errors)


def test_pre_contract_marker_grandfathers_structure(tmp_path):
    errors = lint_file(write(tmp_path, "# T9999 old\n\npre-contract: true - indexed.\n"))
    assert errors == []


def test_title_must_name_task(tmp_path):
    errors = lint_file(write(tmp_path, FULL.replace("# T9999", "# Something else")))
    assert any("title" in e for e in errors)


def test_repo_evidence_tree_is_lint_clean():
    out = subprocess.run(
        [sys.executable, "tools/evidence_lint.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout
