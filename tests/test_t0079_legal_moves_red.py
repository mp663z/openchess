"""T0079: the legal-moves red suite must be RED for exactly the right
reason - the T0080 runtime (tools/legal_moves_runtime) does not exist -
while executing every T0078 fixture case, and must stay out of the
default gate's collection.

Flip hook: the moment tools/legal_moves_runtime exists this harness
FAILS (stale red claim); the T0080 PR turns the red suite green and
replaces this harness with direct gate collection in the same change."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RED_SUITE = ROOT / "tests" / "red" / "legal_moves_behavior.py"
RUNTIME = ROOT / "tools" / "legal_moves_runtime.py"
FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "legal_moves" / "cases.json").read_text())

TOTAL = sum(len(FIXTURE[s]) for s in
            ("happy", "boundary", "terminal", "malformed", "rollback"))
DEFERRED = 0  # the legal-moves fixture declares no deferred cases
RED = TOTAL - DEFERRED
# ... pinned to the T0078-reviewed fixture state: a fixture edit
# (shrink OR growth) silently re-deriving expectations is a hole, so the
# pins anchor this harness to the reviewed counts. A legitimate fixture
# change updates the pins in the same PR.
TOTAL_PIN = 68
DEFERRED_PIN = 0
RED_PIN = 68
# Byte-level continuity pins: counts are diagnostic only - a same-count
# fixture replacement or a same-shape red-suite rewrite must fail. Any
# legitimate edit updates the digest in the same PR.
FIXTURE_SHA256 = "7f5d63486aa4bc8aee831342dc3dbcae63e0b94f79d5dec960bb44fcafe270dd"
RED_SUITE_SHA256 = "ac79a7d59e1bf89d7a2da8c0d09c887abafa398cae0c8ec9dbe0f5a6c07adea1"
ABSENT_MARKER = "RED-EXPECTED-ABSENT"

PYTEST_BASE = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*PYTEST_BASE, *args], cwd=ROOT, capture_output=True,
        text=True, timeout=timeout
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
        "a test_-prefixed name would be collected by the default gate"
        " and red it"
    )
    out = _run(["tests", "--collect-only", "-q"])
    assert out.returncode == 0, out.stdout[-300:] + out.stderr[-300:]
    assert "legal_moves_behavior" not in out.stdout, (
        "red suite leaked into gate collection")


def test_runtime_not_implemented_yet():
    # import semantics, not one path: a package form
    # (tools/legal_moves_runtime/__init__.py) must trip the gate just as
    # a module file does
    assert not RUNTIME.exists(), (
        "tools/legal_moves_runtime.py exists: the T0079 red claim is"
        " stale - turn the red suite green and flip this harness in the"
        " T0080 PR"
    )
    assert importlib.util.find_spec("tools.legal_moves_runtime") is None, (
        "tools.legal_moves_runtime is importable: the T0079 red claim"
        " is stale - turn the red suite green and flip this harness in"
        " the T0080 PR"
    )


def test_red_suite_is_red_for_the_right_reason(tmp_path):
    report = tmp_path / "red.xml"
    r = _run([str(RED_SUITE), "-q", "--junitxml", str(report)])
    assert r.returncode == 1, (
        f"expected a red run (exit 1), got {r.returncode}:"
        f" {r.stdout[-300:]}{r.stderr[-300:]}"
    )
    cases = _cases(report)
    assert len(cases) == TOTAL_PIN, (
        f"executed {len(cases)} cases, pinned expectation {TOTAL_PIN}"
        f" (derived {TOTAL})"
    )
    outcomes = {tc.get("name"): _outcome(tc) for tc in cases}
    passed = [n for n, (tag, _b) in outcomes.items() if tag == "passed"]
    skipped = [n for n, (tag, _b) in outcomes.items() if tag == "skipped"]
    failed = [(n, b) for n, (tag, b) in outcomes.items()
              if tag in ("failure", "error")]
    assert not passed, f"green cases in a red suite: {passed}"
    assert len(skipped) == DEFERRED_PIN, (
        f"skipped {len(skipped)} != pinned deferred {DEFERRED_PIN}")
    assert len(failed) == RED_PIN, f"red {len(failed)} != pinned {RED_PIN}"
    for name, blob in failed:
        assert ABSENT_MARKER in blob, (
            f"{name}: red for the wrong reason (the marked absence"
            f" error is only produced when the import system itself"
            f" cannot resolve the module, never by a module raising"
            f" internally): {blob[:200]}"
        )


def test_continuity_digests_pin_fixture_and_red_suite_bytes():
    fx = hashlib.sha256(
        (ROOT / "tests" / "fixtures" / "legal_moves" / "cases.json")
        .read_bytes())
    rs = hashlib.sha256(RED_SUITE.read_bytes())
    assert fx.hexdigest() == FIXTURE_SHA256, (
        "fixture bytes drifted from the T0078-reviewed digest - a"
        " legitimate fixture change updates FIXTURE_SHA256 in the same"
        " PR"
    )
    assert rs.hexdigest() == RED_SUITE_SHA256, (
        "red suite bytes drifted from the T0079-reviewed digest - a"
        " legitimate red-suite change updates RED_SUITE_SHA256 in the"
        " same PR"
    )


def test_fixture_counts_internally_consistent():
    assert TOTAL > 0 and RED > 0
    assert (
        len(FIXTURE["happy"])
        + len(FIXTURE["boundary"])
        + len(FIXTURE["terminal"])
        + len(FIXTURE["malformed"])
        + len(FIXTURE["rollback"])
        == TOTAL
    )
    assert (TOTAL, DEFERRED, RED) == (TOTAL_PIN, DEFERRED_PIN, RED_PIN), (
        f"fixture drifted from the T0078-reviewed counts: derived"
        f" ({TOTAL}, {DEFERRED}, {RED}) != pinned ({TOTAL_PIN},"
        f" {DEFERRED_PIN}, {RED_PIN})"
    )
