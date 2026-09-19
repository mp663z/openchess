"""T0035: CLA/DCO check - end-to-end through the REAL gate, seeded attacks.

Every scenario runs through cla_check.check_pr with seeded git/API/registry
surfaces (patched in-process, restored after each run) - the same function
CI runs on real pull_request events. Scope: the automated gate covers
registry validation, de-minimis classification and acceptance verification;
DCO sign-off on de-minimis PRs is verified manually at review (DCO.md) and
is not claimed as automated here.

Good scenarios: de-minimis docs-only change by an unregistered author
passes; a registered author whose own PR body carries the exact acceptance
statement passes; the 10-line de-minimis boundary is exact.
Violation scenarios (each must raise ClaError): unregistered author with a
non-de-minimis change; self-entry only in the PR head (base registry
empty); registry reference to a PR authored by someone else; no
acceptance; negated acceptance; quoted acceptance; acceptance in a comment
by a different user; malformed base registry; 11-line change over the de
minimis budget without a registry entry.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tools import cla_check
from tools.install_checks import CheckError

CHECK_ID = "T0035"
REPO = cla_check.DEFAULT_REPO
STMT = cla_check.ACCEPTANCE_STATEMENT


def _entry(handle="alice", reference=None):
    return {
        "github_handle": handle,
        "accepted_at": "2026-09-18T12:00:00+00:00",
        "reference": reference or f"{REPO}#7",
    }


@contextmanager
def _seeded_pr(*, author, additions, deletions, files, registry_raw, api):
    """Run check_pr against fully seeded git/API/registry surfaces."""
    event = {"pull_request": {"user": {"login": author},
                              "additions": additions, "deletions": deletions}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(event, f)
        event_path = f.name

    def fake_gh(endpoint):
        if endpoint.startswith(f"repos/{REPO}/pulls/"):
            return api.get("pr", {})
        if endpoint.startswith(f"repos/{REPO}/issues/"):
            return api.get("comments", [])
        raise AssertionError(f"unexpected API call: {endpoint}")

    def fake_base_registry(_base_ref):
        # mirrors load_base_registry: malformed content fails closed HERE
        return cla_check.validate_registry(registry_raw, REPO)

    with mock.patch.object(cla_check, "changed_files", lambda _b: files), \
         mock.patch.object(cla_check, "load_base_registry", fake_base_registry), \
         mock.patch.object(cla_check, "_gh_api", fake_gh):
        try:
            yield cla_check.check_pr(event_path, "main")
        finally:
            Path(event_path).unlink(missing_ok=True)


REG_OK = {"schema_version": 1, "acceptances": [_entry()]}
REG_EMPTY = {"schema_version": 1, "acceptances": []}
REG_BAD = {"schema_version": 2, "acceptances": "nope"}

GOOD = {
    "de minimis docs-only, unregistered": dict(
        author="bob", additions=4, deletions=1, files=[("M", "docs/faq.md")],
        registry_raw=REG_EMPTY, api={}),
    "registered author, exact acceptance in body": dict(
        author="alice", additions=60, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_OK,
        api={"pr": {"user": {"login": "alice"}, "body": f"Thanks!\n{STMT}\n"}}),
    "de minimis boundary at exactly 10 lines": dict(
        author="bob", additions=7, deletions=3, files=[("M", "docs/faq.md")],
        registry_raw=REG_EMPTY, api={}),
}

VIOLATIONS = {
    "unregistered, non-de-minimis": dict(
        author="mallory", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_EMPTY, api={}),
    "self-entry in head only (base empty)": dict(
        author="mallory", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_EMPTY,  # head carries the entry; the gate reads BASE
        api={"pr": {"user": {"login": "mallory"}, "body": STMT}}),
    "reference authored by someone else": dict(
        author="alice", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_OK,
        api={"pr": {"user": {"login": "bob"}, "body": STMT}}),
    "no acceptance statement": dict(
        author="alice", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_OK,
        api={"pr": {"user": {"login": "alice"}, "body": "hello"}}),
    "negated acceptance": dict(
        author="alice", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_OK,
        api={"pr": {"user": {"login": "alice"},
                    "body": f"I refuse to say the magic words: {STMT}!"}}),
    "quoted acceptance": dict(
        author="alice", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_OK,
        api={"pr": {"user": {"login": "alice"}, "body": f"> {STMT}\n\nnice doc"}}),
    "acceptance only in another user's comment": dict(
        author="alice", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_OK,
        api={"pr": {"user": {"login": "alice"}, "body": "hi"},
             "comments": [{"user": {"login": "mallory"}, "body": STMT}]}),
    "malformed base registry fails closed": dict(
        author="alice", additions=40, deletions=10, files=[("M", "tools/dag.py")],
        registry_raw=REG_BAD, api={}),
    "11 lines: over the de minimis budget": dict(
        author="bob", additions=8, deletions=3, files=[("M", "docs/faq.md")],
        registry_raw=REG_EMPTY, api={}),
    "protected file under budget": dict(
        author="bob", additions=1, deletions=0, files=[("M", "LICENSE")],
        registry_raw=REG_EMPTY, api={}),
    "de minimis with malformed base registry": dict(
        author="bob", additions=2, deletions=1, files=[("M", "docs/faq.md")],
        registry_raw=REG_BAD, api={}),
}


def run(mode: str) -> None:
    if mode == "good":
        cla_check.load_registry()  # the repo's real registry must validate
        for name, kw in sorted(GOOD.items()):
            try:
                with _seeded_pr(**kw) as result:
                    if not isinstance(result, str) or not result:
                        raise CheckError(f"good scenario returned nothing: {name}")
            except cla_check.ClaError as e:
                raise CheckError(f"good scenario rejected: {name}: {e}") from e
        return
    uncaught = []
    for name, kw in sorted(VIOLATIONS.items()):
        try:
            with _seeded_pr(**kw):
                uncaught.append(f"{name}: NOT rejected")
        except cla_check.ClaError:
            pass
    if uncaught:
        return  # harness FAILS: a CLA/DCO bypass escaped the real gate
    raise CheckError("all seeded CLA/DCO attacks rejected by the real gate")
