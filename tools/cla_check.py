"""CLA gate (CLA.md section 7, T0010).

Runs on pull_request events. Reads the trusted GitHub event payload for the
PR author, classifies the change as de minimis or CLA-required from the
actual diff, and fails when a CLA-required contribution has no recorded
acceptance in data/cla-acceptances.yaml.

De minimis (ALL required, matching DCO.md):
  <= 10 changed lines (additions + deletions from the event payload), no
  added or removed files, and no protected path touched (governance/legal
  docs, tooling, data, schemas, dependency manifests, CI).
Anything else requires a registry entry for the PR author's GitHub handle.

Registry (data/cla-acceptances.yaml) is strictly validated: normalized
GitHub handle, ISO-8601 timestamp with timezone, immutable URL or PR
reference, unique handles. A malformed registry fails closed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "cla-acceptances.yaml"

HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}$", re.I)
ISO_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$"
)
REF_RE = re.compile(r"^(https://\S+|[\w.-]+/[\w.-]+#\d+)$")

PROTECTED_PREFIXES = (
    "tools/", "data/", "tasks/", ".github/", ".githooks/", "evidence/",
    "docs/rights-policy.md", "docs/licensing.md",
)
PROTECTED_FILES = {
    "LICENSE", "NOTICE", "CLA.md", "DCO.md", "CONTRIBUTING.md",
    "GOVERNANCE.md", "TRADEMARK.md", "SECURITY.md", ".github/CODEOWNERS",
    "licensing-boundary.yaml", "pyproject.toml", "requirements-dev.txt",
    "data/cla-acceptances.yaml",
}
DE_MINIMIS_MAX_LINES = 10


class ClaError(RuntimeError):
    pass


def load_registry(path: Path = REGISTRY) -> dict[str, dict]:
    """Strictly validate the registry; fail closed on any malformation."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ClaError("cla-acceptances.yaml: bad schema_version")
    entries = raw.get("acceptances")
    if not isinstance(entries, list):
        raise ClaError("cla-acceptances.yaml: acceptances must be a list")
    seen: set[str] = set()
    out: dict[str, dict] = {}
    for e in entries:
        if not isinstance(e, dict):
            raise ClaError("registry entry is not a mapping")
        handle = str(e.get("github_handle", "")).strip().lower()
        if not HANDLE_RE.fullmatch(handle):
            raise ClaError(f"registry: bad github_handle {e.get('github_handle')!r}")
        if handle in seen:
            raise ClaError(f"registry: duplicate handle {handle}")
        seen.add(handle)
        ts = str(e.get("accepted_at", ""))
        if not ISO_TS_RE.fullmatch(ts):
            raise ClaError(f"registry: {handle} accepted_at not ISO-8601 with tz")
        ref = str(e.get("reference", ""))
        if not REF_RE.fullmatch(ref):
            raise ClaError(f"registry: {handle} reference must be a URL or owner/repo#N")
        out[handle] = e
    return out


def changed_files(base_ref: str) -> list[tuple[str, str]]:
    """(status, path) pairs for the PR diff; statuses A/M/D/R100 etc."""
    subprocess.run(
        ["git", "fetch", "origin", base_ref, "--depth=1"],
        cwd=ROOT, check=True, capture_output=True,
    )
    out = subprocess.run(
        ["git", "diff", "--name-status", f"origin/{base_ref}...HEAD"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    pairs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            pairs.append((parts[0], parts[-1]))
    return pairs


def is_de_minimis(changed_lines: int, files: list[tuple[str, str]]) -> bool:
    if changed_lines > DE_MINIMIS_MAX_LINES:
        return False
    for status, path in files:
        if status.startswith(("A", "D", "R")):  # new/removed/renamed file
            return False
        if path in PROTECTED_FILES or path.startswith(PROTECTED_PREFIXES):
            return False
    return True


def check_pr(event_path: str, base_ref: str) -> str:
    event = json.loads(Path(event_path).read_text())
    pr = event.get("pull_request")
    if not pr:
        raise ClaError("no pull_request payload - cla_check runs on PR events")
    author = str(pr["user"]["login"]).strip().lower()
    changed_lines = int(pr.get("additions", 0)) + int(pr.get("deletions", 0))
    files = changed_files(base_ref)
    registry = load_registry()
    if is_de_minimis(changed_lines, files):
        return (
            f"de minimis ({changed_lines} lines, {len(files)} files): "
            f"{author} needs no registry entry; DCO sign-off verified at review"
        )
    if author in registry:
        return f"{author}: CLA acceptance recorded {registry[author]['accepted_at']}"
    raise ClaError(
        f"{author}: CLA-required change (not de minimis) without a recorded "
        f"acceptance in data/cla-acceptances.yaml - state CLA acceptance in "
        f"the PR; a maintainer records it before merge (CLA.md section 7)"
    )


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    base_ref = os.environ.get("GITHUB_BASE_REF", "main")
    if not event_path:
        print("SKIP cla_check: not a pull_request event")
        return 0
    try:
        print(f"OK cla_check: {check_pr(event_path, base_ref)}")
        return 0
    except ClaError as e:
        print(f"FAIL cla_check: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
