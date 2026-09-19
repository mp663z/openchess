"""T0003 (v2): only the literal {{PRODUCT_NAME}} is used until the name gate.

Case-insensitive scan over every tracked text file. No directory exemptions:
the only tolerated occurrences of the working codename are the explicit,
justified allowlist entries below.
"""

import re
import subprocess
from pathlib import Path

import brand

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "{{PRODUCT_NAME}}"
TEXT_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".toml", ".json", ".txt")
# Extensionless / dotfile tracked text files are scanned too (LICENSE, hooks).

# Explicit exceptions with justification (path, pattern that must match the line).
ALLOWLIST = {
    "pyproject.toml": re.compile(r'^\s*name\s*=\s*"openchess"'),  # distribution slug
    "tools/cla_check.py": re.compile(r"DEFAULT_REPO = "),  # factual repo address constant
    "evidence/T0019.md": re.compile(r'"name":"openchess-control-plane"'),  # quoted gh api metadata
    "docs/plan/development-plan-v9.md": None,   # verbatim source document of record
    "docs/plan/product-report-v5.md": None,     # verbatim source document of record
}
URL_PATTERN = re.compile(r"github\.com/mp663z/openchess")  # factual repo address


def tracked_text_files() -> list[str]:
    all_files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return sorted(
        f for f in all_files
        if f.endswith(TEXT_SUFFIXES) or "." not in Path(f).name or Path(f).name.startswith(".")
    )


def codename_lines(path: Path) -> list[str]:
    return [
        line for line in path.read_text(errors="replace").splitlines()
        if re.search(r"openchess", line, re.IGNORECASE)
        and not URL_PATTERN.search(line)
    ]


def test_product_name_is_literal_placeholder():
    assert brand.PRODUCT_NAME == PLACEHOLDER


def test_case_insensitive_scan_all_text_files():
    offenders = {}
    for rel in tracked_text_files():
        if rel == "tests/test_naming.py":
            continue
        hits = codename_lines(ROOT / rel)
        if not hits:
            continue
        allow = ALLOWLIST.get(rel, "NOT_ALLOWED")
        if allow == "NOT_ALLOWED":
            offenders[rel] = hits[:3]
        elif allow is not None:  # pattern-scoped exception
            bad = [line for line in hits if not allow.search(line)]
            if bad:
                offenders[rel] = bad[:3]
        # allow is None: whole-file verbatim-document exception, listed above
    assert not offenders, f"codename outside allowlist: {offenders}"


def test_no_alternative_product_name_assignment_anywhere():
    for rel in tracked_text_files():
        for m in re.finditer(
            r"(?i)(product_name|app_name|product_title)\s*[=:]\s*[\"']([^\"']+)[\"']",
            (ROOT / rel).read_text(errors="replace"),
        ):
            assert m.group(2) == PLACEHOLDER, f"{rel}: hardcoded name {m.group(2)!r}"


def test_user_visible_surfaces_use_placeholder():
    """Every actual user-visible naming surface uses the single source."""
    readme = (ROOT / "README.md").read_text()
    assert PLACEHOLDER in readme and not codename_lines(ROOT / "README.md")
    dag = (ROOT / "tasks/dag.json").read_text()
    promise = re.search(r'"product_promise":\s*"([^"]+)"', dag).group(1)
    assert PLACEHOLDER in promise
