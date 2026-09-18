"""T0017: CODEOWNERS - lint + content assertions."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_codeowners_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py", ".github/CODEOWNERS"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout


def test_codeowners_content_exact():
    text = (ROOT / ".github/CODEOWNERS").read_text()
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines, "no ownership rules"
    for path in ("LICENSE", "CLA.md", "GOVERNANCE.md", "tools/evidence_lint.py",
                 "data/release-lock.json"):
        assert any(ln.startswith(path) for ln in lines), path
    # every rule names an owner
    assert all("@" in ln for ln in lines)


def test_contributing_owned_and_routing_wording():
    text = (ROOT / ".github/CODEOWNERS").read_text()
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert any(ln.startswith("CONTRIBUTING.md") for ln in lines)
    head = " ".join(text.split()).lower()
    assert "review routing" in head
    assert "merge rights live in repository settings" in head
    assert "routing and merge rights" not in head


def test_lint_is_structural_not_substring():
    """A rules file missing a required path must fail the lint."""
    lint = (ROOT / "tools/governance_doc_lint.py").read_text()
    assert "CODEOWNERS_REQUIRED_PATHS" in lint
    assert "no rule for" in lint


def test_lint_rejects_malformed_rules(tmp_path):
    from tools import governance_doc_lint as gdl
    good = (ROOT / ".github/CODEOWNERS").read_text()

    def errs(content):
        p = tmp_path / "CODEOWNERS"
        p.write_text(content)
        return gdl.lint_doc(p, ".github/CODEOWNERS")

    assert errs(good) == []
    assert any("without an owner" in e for e in errs(good + "\nlonely-path\n"))
    assert any("invalid owner token" in e for e in errs(good + "\n/x hello@world\n"))
    assert any("invalid owner token" in e for e in errs(good + "\n/y @bad..dots\n"))
    # a comment containing @ is not a rule and does not break anything
    assert errs(good + "\n# mention @someone in a comment\n") == []
    # removing a required path fails
    import re
    stripped = re.sub(r"(?m)^LICENSE .*$", "", good)
    assert any("no rule for LICENSE" in e for e in errs(stripped))
