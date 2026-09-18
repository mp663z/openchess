"""T0003: only the literal {{PRODUCT_NAME}} is used until the name gate."""

import re
import subprocess
from pathlib import Path

import brand

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "{{PRODUCT_NAME}}"

# Files whose prose may mention the working codename (descriptive, not naming).
CODENAME_ALLOWLIST = {"tools/dag.py"}


def test_product_name_is_literal_placeholder():
    assert brand.PRODUCT_NAME == PLACEHOLDER


def test_no_alternative_product_name_assignment():
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    for rel in tracked:
        for m in re.finditer(r"(?m)^\s*(PRODUCT_NAME|APP_NAME|TITLE)\s*=\s*[\"']([^\"']+)[\"']",
                             (ROOT / rel).read_text()):
            assert m.group(2) == PLACEHOLDER, f"{rel}: hardcoded name {m.group(2)!r}"


def test_working_codename_confined_to_allowlisted_prose():
    tracked = subprocess.run(
        ["git", "ls-files", "*.py", "*.yaml", "*.yml", "*.toml", "*.json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for rel in tracked:
        if rel in CODENAME_ALLOWLIST:
            continue
        if rel.startswith(("docs/", "evidence/", "data/", "tasks/", "tests/test_naming.py")):
            continue
        if "OpenChess" in (ROOT / rel).read_text(errors="replace"):
            offenders.append(rel)
    assert not offenders, f"working codename in code/config: {offenders}"
