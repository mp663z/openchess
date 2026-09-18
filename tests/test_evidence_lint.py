"""T0007 v2: strict evidence-contract lint - adversarial tests for every bypass."""

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.evidence_lint as el  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40

TASKS = {
    "T9999": {"id": "T9999", "status": "done", "verification": "independent review",
              "done_sha": SHA},
    "T9998": {"id": "T9998", "status": "in_progress", "verification": "auto"},
    "T9997": {"id": "T9997", "status": "done", "verification": "auto", "done_sha": SHA},
}

FULL = f"""# T9999 Example - evidence

Acceptance restated. Artifact at {SHA}.

Status: done
Verification: independent review - PASS at {SHA}
Commands: `pytest tests/test_example.py -q`. Environment: python 3.12.
Recorded merge SHA on main: {SHA}
"""

AUTO = f"""# T9997 Auto task - evidence

Status: done
Verification: auto - green CI at {SHA}
Commands: `python tools/x.py`. Environment: python 3.12.
Recorded merge SHA on main: {SHA}
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_full_contract_file_passes(tmp_path):
    assert el.lint_file(write(tmp_path, "T9999.md", FULL), TASKS, {}) == []
    assert el.lint_file(write(tmp_path, "T9997.md", AUTO), TASKS, {}) == []


def test_prose_marker_mention_does_not_grandfather(tmp_path):
    text = FULL + "\nThis file mentions `pre-contract: true` inline as prose.\n"
    assert el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {}) == []


def test_marker_line_on_non_allowlisted_file_fails(tmp_path):
    text = "# T9999 fake\n\npre-contract: true\n"
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("non-allowlisted" in e for e in errors)


def test_allowlisted_file_passes_but_edit_invalidates(tmp_path):
    original = "# T9999 old\n\npre-contract: true - indexed.\n"
    p = write(tmp_path, "T9999.md", original)
    allow = {"evidence/T9999.md": hashlib.sha256(original.encode()).hexdigest()}
    assert el.lint_file(p, TASKS, allow, rel="evidence/T9999.md") == []
    p.write_text(original + "edited\n")
    errors = el.lint_file(p, TASKS, allow, rel="evidence/T9999.md")
    assert any("edited since grandfathering" in e for e in errors)


def test_pending_word_in_prose_does_not_satisfy_status(tmp_path):
    text = "# T9999 pending work\n\nEverything is pending, honestly.\n"
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("Status:" in e for e in errors)


def test_done_without_board_done_fails(tmp_path):
    text = FULL.replace("# T9999", "# T9998")
    errors = el.lint_file(write(tmp_path, "T9998.md", text), TASKS, {})
    assert any("board status is not done" in e for e in errors)


def test_done_with_wrong_sha_fails(tmp_path):
    text = FULL.replace(f"Recorded merge SHA on main: {SHA}",
                        f"Recorded merge SHA on main: {OTHER}")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("!= board done_sha" in e for e in errors)


def test_short_sha_fails(tmp_path):
    text = FULL.replace(f"Recorded merge SHA on main: {SHA}", "Recorded merge SHA on main: aaaa")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("40-hex" in e for e in errors)


def test_verification_banana_fails(tmp_path):
    text = FULL.replace("Verification: independent review", "Verification: banana")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("mode" in e for e in errors)


def test_independent_done_needs_pass_at_matching_sha(tmp_path):
    text = FULL.replace(f"Verification: independent review - PASS at {SHA}",
                        "Verification: independent review - routed for review")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("PASS at" in e for e in errors)
    text2 = FULL.replace(f"PASS at {SHA}", f"PASS at {OTHER}")
    errors2 = el.lint_file(write(tmp_path, "T9999.md", text2), TASKS, {})
    assert any("PASS SHA" in e for e in errors2)


def test_unverified_verdict_fails(tmp_path):
    text = FULL.replace(f"PASS at {SHA}", "UNVERIFIED")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("UNVERIFIED" in e for e in errors)


def test_commands_need_command_and_environment(tmp_path):
    no_env = FULL.replace(". Environment: python 3.12.", ".")
    errors = el.lint_file(write(tmp_path, "T9999.md", no_env), TASKS, {})
    assert any("Environment:" in e for e in errors)
    no_cmd = FULL.replace("`pytest tests/test_example.py -q`. ", "")
    errors = el.lint_file(write(tmp_path, "T9999.md", no_cmd), TASKS, {})
    assert any("backticked" in e for e in errors)


def test_repo_tree_is_lint_clean():
    out = subprocess.run(
        [sys.executable, "tools/evidence_lint.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout


def test_allowlist_is_frozen_yaml_with_sha256():
    data = yaml.safe_load((ROOT / "data/evidence-pre-contract.yaml").read_text())
    assert len(data["files"]) == 23
    for path, digest in data["files"].items():
        assert len(digest) == 64, path
        assert (ROOT / path).exists(), path
