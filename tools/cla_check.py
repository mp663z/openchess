"""CLA gate (CLA.md section 7, T0010).

Runs on pull_request events. Trust model: the registry is loaded from the
trusted BASE ref of the PR, never from the PR head - a contributor cannot
authorize themselves by adding their own entry in the same PR. Registry
references are restricted to this repository's pull requests and each
referenced PR is verified through the GitHub API: its author must match the
registered handle and the PR body or one of the author's comments must
contain the exact acceptance statement from CLA.md.

De minimis (ALL required, matching DCO.md):
  <= 10 changed lines (additions + deletions from the event payload), no
  added or removed files, and no protected path touched (governance/legal
  docs, tooling, data, schemas, dependency manifests, CI).
Anything else requires a verified registry entry for the PR author.

Registry (data/cla-acceptances.yaml) is strictly validated: normalized
GitHub handle, real timezone-aware ISO-8601 timestamp, this-repo PR
reference, unique handles. A malformed registry fails closed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_REL = "data/cla-acceptances.yaml"
REGISTRY = ROOT / REGISTRY_REL
DEFAULT_REPO = "mp663z/openchess"
ACCEPTANCE_STATEMENT = "I accept the CLA in CLA.md"

HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}$", re.I)

PROTECTED_PREFIXES = (
    "tools/", "data/", "tasks/", ".github/", ".githooks/", "evidence/",
    "docs/rights-policy.md", "docs/licensing.md",
)
PROTECTED_FILES = {
    "LICENSE", "NOTICE", "CLA.md", "DCO.md", "CONTRIBUTING.md",
    "GOVERNANCE.md", "TRADEMARK.md", "SECURITY.md", ".github/CODEOWNERS",
    "licensing-boundary.yaml", "pyproject.toml", "requirements-dev.txt",
    REGISTRY_REL,
}
DE_MINIMIS_MAX_LINES = 10


class ClaError(RuntimeError):
    pass


def current_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)


def _parse_reference(ref: str, repo: str) -> int:
    """Reference must be this repo's PR: owner/repo#N or its GitHub URL."""
    m = re.fullmatch(rf"{re.escape(repo)}#(\d+)", ref)
    if not m:
        m = re.fullmatch(
            rf"https://github\.com/{re.escape(repo)}/pull/(\d+)", ref
        )
    if not m:
        raise ClaError(
            f"registry: reference {ref!r} must be {repo}#N or its PR URL"
        )
    return int(m.group(1))


def parse_accepted_at(ts: str) -> datetime:
    """Real timezone-aware ISO-8601 timestamp; shape alone is not enough."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise ClaError(f"registry: accepted_at {ts!r} is not a real date") from None
    if dt.tzinfo is None:
        raise ClaError(f"registry: accepted_at {ts!r} lacks a timezone")
    return dt


def validate_registry(raw, repo: str | None = None) -> dict[str, dict]:
    """Strictly validate parsed registry content; fail closed on bad data."""
    repo = repo or current_repo()
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
        parse_accepted_at(str(e.get("accepted_at", "")))
        _parse_reference(str(e.get("reference", "")), repo)
        out[handle] = e
    return out


def load_registry(path: Path = REGISTRY) -> dict[str, dict]:
    return validate_registry(yaml.safe_load(path.read_text()))


def load_base_registry(base_ref: str) -> dict[str, dict]:
    """Registry from the trusted base ref, never from the PR head."""
    r = subprocess.run(
        ["git", "show", f"origin/{base_ref}:{REGISTRY_REL}"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return {}  # registry does not exist on the base yet (bootstrap)
    return validate_registry(yaml.safe_load(r.stdout))


def _gh_api(endpoint: str) -> dict | list:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ClaError("no GH_TOKEN/GITHUB_TOKEN for acceptance verification")
    r = subprocess.run(
        ["gh", "api", endpoint],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    if r.returncode != 0:
        raise ClaError(f"GitHub API failed for {endpoint}: {r.stderr.strip()}")
    return json.loads(r.stdout)


def _has_acceptance(text: str | None) -> bool:
    """Affirmative consent: the exact statement as a standalone normalized
    line. Substring hits inside prose, negations, or quotations do not
    count."""
    if not text:
        return False
    return any(
        " ".join(line.split()) == ACCEPTANCE_STATEMENT
        for line in text.splitlines()
    )


def verify_acceptance(handle: str, entry: dict, repo: str | None = None) -> None:
    """The referenced PR must be authored by handle and contain the exact
    acceptance statement in its body or in a comment by that author."""
    repo = repo or current_repo()
    pr_number = _parse_reference(str(entry.get("reference", "")), repo)
    pr = _gh_api(f"repos/{repo}/pulls/{pr_number}")
    author = str(pr.get("user", {}).get("login", "")).strip().lower()
    if author != handle:
        raise ClaError(
            f"registry: {handle} references PR #{pr_number} authored by "
            f"{author or 'unknown'} - reference must be the contributor's own PR"
        )
    if _has_acceptance(pr.get("body")):
        return
    comments = _gh_api(f"repos/{repo}/issues/{pr_number}/comments")
    for c in comments if isinstance(comments, list) else []:
        if (
            str(c.get("user", {}).get("login", "")).strip().lower() == handle
            and _has_acceptance(c.get("body"))
        ):
            return
    raise ClaError(
        f"registry: {handle} PR #{pr_number} has no comment/body with the "
        f"exact acceptance statement {ACCEPTANCE_STATEMENT!r}"
    )


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
    # The base registry is validated for EVERY PR, de minimis included: a
    # malformed registry fails closed everywhere, never silently skipped.
    registry = load_base_registry(base_ref)
    if is_de_minimis(changed_lines, files):
        return (
            f"de minimis ({changed_lines} lines, {len(files)} files): "
            f"{author} needs no registry entry; DCO sign-off verified at review"
        )
    entry = registry.get(author)
    if entry is None:
        raise ClaError(
            f"{author}: CLA-required change (not de minimis) without a "
            f"verified acceptance in the BASE registry ({REGISTRY_REL}) - "
            f"state {ACCEPTANCE_STATEMENT!r} in this PR; a maintainer records "
            f"it on the base branch before merge (CLA.md section 7). An entry "
            f"added in this PR does not count."
        )
    verify_acceptance(author, entry)
    return f"{author}: CLA acceptance verified via {entry['reference']}"


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
