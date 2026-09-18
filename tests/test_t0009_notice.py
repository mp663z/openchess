"""T0009: network source notice - lint, content, mutation, wiring tests."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_notice_lint_clean():
    r = subprocess.run(
        ["python3", "tools/governance_doc_lint.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout


def test_notice_content_exact():
    text = (ROOT / "NOTICE").read_text()
    assert "AGPL-3.0-or-later" in text
    assert "section 13" in text.lower()
    assert "Corresponding Source" in text
    assert '"Source" in the project README' in text
    assert "release.yml" in text


def test_notice_promises_backed_by_repo_reality():
    # README carries the source link and the contact channel NOTICE cites
    readme = (ROOT / "README.md").read_text()
    assert "## Source" in readme and "github.com/" in readme.split("## Source")[1]
    assert "## Contact" in readme and "/issues" in readme.split("## Contact")[1]
    # the release workflow NOTICE cites exists and attaches SBOM + checksums
    wf = (ROOT / ".github/workflows/release.yml").read_text()
    assert "component_inventory" in wf and "checksums.sha256" in wf
    assert 'tags: ["v*"]' in wf


def test_lint_wired_into_ci_and_hook():
    import re

    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    # an actual CI run step, not a mention in a comment or name
    assert re.search(r"^\s*run: python tools/governance_doc_lint\.py\s*$", ci, re.M)
    hook_lines = [
        ln for ln in (ROOT / ".githooks/pre-push").read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert "python tools/governance_doc_lint.py" in hook_lines


def test_mutation_without_required_heading_fails(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gdl", ROOT / "tools/governance_doc_lint.py"
    )
    gdl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gdl)
    text = (ROOT / "NOTICE").read_text()
    # drop the source-offer heading; prose still mentions "source offer"
    mutated = text.replace("## Source offer\n", "## Where to get things\n", 1)
    p = tmp_path / "NOTICE"
    p.write_text(mutated)
    errors = gdl.lint_doc(p, "NOTICE")
    assert any("source offer" in e for e in errors)



def _load_lint():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gdl", ROOT / "tools/governance_doc_lint.py"
    )
    gdl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gdl)
    return gdl


def test_headings_only_with_no_bodies_fail(tmp_path):
    gdl = _load_lint()
    headings = [
        "# Network Use and Source Offer", "## Network use", "## Source offer",
        "## Corresponding source scope", "## Trademarks, data, and models",
    ]
    p = tmp_path / "NOTICE"
    p.write_text("\n\n".join(headings) + "\n" + "filler " * 80)
    errors = gdl.lint_doc(p, "NOTICE")
    assert any("no operative body" in e for e in errors)


def test_headings_inside_fenced_code_are_inert(tmp_path):
    gdl = _load_lint()
    real = (ROOT / "NOTICE").read_text()
    # hide every required heading inside a fence, keep the byte count
    fenced = real
    for h in ("## Network use", "## Source offer"):
        fenced = fenced.replace(h, f"```\n{h}\n```", 1)
    p = tmp_path / "NOTICE"
    p.write_text(fenced)
    errors = gdl.lint_doc(p, "NOTICE")
    assert any("missing required heading" in e for e in errors)


def test_headings_inside_html_comment_are_inert(tmp_path):
    gdl = _load_lint()
    real = (ROOT / "NOTICE").read_text()
    commented = real.replace("## Network use", "<!--\n## Network use\n-->", 1)
    p = tmp_path / "NOTICE"
    p.write_text(commented)
    errors = gdl.lint_doc(p, "NOTICE")
    assert any("'network use'" in e for e in errors)
