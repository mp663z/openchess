"""T0043: the variant red suite must be RED for exactly the right
reason - the T0044 runtime (tools/variant_runtime) does not exist -
while executing every T0042 fixture case, and must stay out of the
default gate's collection.

Flip hook: the moment tools/variant_runtime.py exists this harness
FAILS (stale red claim); the T0044 PR turns the red suite green and
replaces this harness with direct gate collection in the same change."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RED_SUITE = ROOT / "tests" / "red" / "variant_behavior.py"
RUNTIME = ROOT / "tools" / "variant_runtime.py"
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "variant" / "cases.json").read_text())

TOTAL = sum(len(FIXTURE[s]) for s in ("happy", "boundary", "malformed", "rollback"))
DEFERRED = sum(1 for c in FIXTURE["boundary"] if "deferred" in c)
RED = TOTAL - DEFERRED
# ... and pinned to the T0042-reviewed fixture state: a fixture edit
# (shrink OR growth) silently re-deriving expectations is a hole, so the
# pins anchor this harness to the reviewed counts. A legitimate fixture
# change updates the pins in the same PR.
TOTAL_PIN = 25
DEFERRED_PIN = 1
RED_PIN = 24

PYTEST_BASE = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*PYTEST_BASE, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )


def _cases(report: Path) -> list[ET.Element]:
    root = ET.parse(report).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    return [tc for s in suites for tc in s.findall("testcase")]


def _outcome(tc: ET.Element) -> tuple[str, str]:
    for tag in ("failure", "error", "skipped"):
        node = tc.find(tag)
        if node is not None:
            return tag, (node.get("message") or "") + "\n" + (node.text or "")
    return "passed", ""


def test_red_suite_exists_and_stays_out_of_default_collection():
    assert RED_SUITE.is_file()
    assert not RED_SUITE.name.startswith("test_"), (
        "a test_-prefixed name would be collected by the default gate and red it"
    )
    out = _run(["tests", "--collect-only", "-q"])
    assert out.returncode == 0, out.stdout[-300:] + out.stderr[-300:]
    assert "variant_behavior" not in out.stdout, "red suite leaked into gate collection"


def test_runtime_not_implemented_yet():
    assert not RUNTIME.exists(), (
        "tools/variant_runtime.py exists: the T0043 red claim is stale - "
        "turn the red suite green and flip this harness in the T0044 PR"
    )


def test_red_suite_is_red_for_the_right_reason(tmp_path):
    report = tmp_path / "red.xml"
    r = _run([str(RED_SUITE), "-q", "--junitxml", str(report)])
    assert r.returncode == 1, (
        f"expected a red run (exit 1), got {r.returncode}: {r.stdout[-300:]}{r.stderr[-300:]}"
    )
    cases = _cases(report)
    assert len(cases) == TOTAL_PIN, (
        f"executed {len(cases)} cases, pinned expectation {TOTAL_PIN} (derived {TOTAL})"
    )
    outcomes = {tc.get("name"): _outcome(tc) for tc in cases}
    passed = [n for n, (tag, _b) in outcomes.items() if tag == "passed"]
    skipped = [n for n, (tag, _b) in outcomes.items() if tag == "skipped"]
    failed = [(n, b) for n, (tag, b) in outcomes.items() if tag in ("failure", "error")]
    assert not passed, f"green cases in a red suite: {passed}"
    assert len(skipped) == DEFERRED_PIN, f"skipped {len(skipped)} != pinned deferred {DEFERRED_PIN}"
    assert len(failed) == RED_PIN, f"red {len(failed)} != pinned {RED_PIN}"
    for name, blob in failed:
        assert "ModuleNotFoundError" in blob and "tools.variant_runtime" in blob, (
            f"{name}: red for the wrong reason: {blob[:200]}"
        )
    for name in skipped:
        assert "identity-differs-variant" in name, f"unexpected skip: {name}"


def test_fixture_counts_internally_consistent():
    assert TOTAL > 0 and DEFERRED >= 1 and RED > 0
    assert (
        len(FIXTURE["happy"])
        + len(FIXTURE["boundary"])
        + len(FIXTURE["malformed"])
        + len(FIXTURE["rollback"])
        == TOTAL
    )
    assert (TOTAL, DEFERRED, RED) == (TOTAL_PIN, DEFERRED_PIN, RED_PIN), (
        f"fixture drifted from the T0042-reviewed counts: derived "
        f"({TOTAL}, {DEFERRED}, {RED}) != pinned "
        f"({TOTAL_PIN}, {DEFERRED_PIN}, {RED_PIN}) - a legitimate "
        "fixture change updates the pins in the same PR"
    )
