"""T0021/T0032: install-check harness - discovery, dual-mode, fault injection."""

import subprocess
from pathlib import Path

import pytest

from tools.install_checks import (
    CheckError,
    check_dependency_license,
    check_format_type_api,
    runner,
)

ROOT = Path(__file__).resolve().parent.parent


def test_runner_both_modes_green():
    skipped: list[str] = []
    assert runner.run_all(skipped=skipped) == []
    if skipped:  # local clone only; in_ci() turns every skip into a failure
        pytest.skip("; ".join(skipped))


def test_discovery_finds_checks_without_registry():
    names = runner.discover()
    assert "check_format_type_api" in names
    assert "check_dependency_license" in names


def test_unknown_check_id_fails_closed():
    failures = runner.run_all(only={"T9999"})
    assert failures and "unknown check id" in failures[0]


def test_t0021_violation_is_caught():
    check_format_type_api.run("good")
    with pytest.raises(CheckError):
        check_format_type_api.run("violation")


@pytest.mark.parametrize("fixture,detector", [
    ("bad_format.py", "format"),
    ("bad_type.py", "type"),
    ("bad_api.py", "api"),
])
def test_t0021_each_detector_independently_bites(fixture, detector):
    failed = check_format_type_api._failures(
        check_format_type_api.FIXTURES / fixture
    )
    assert failed == {detector}


@pytest.mark.parametrize("removed", ["format", "type", "api"])
def test_t0021_detector_removal_fails_harness(monkeypatch, removed):
    kept = tuple(d for d in check_format_type_api.DETECTORS if d[0] != removed)
    monkeypatch.setattr(check_format_type_api, "DETECTORS", kept)
    assert runner.run_all(only={"T0021"}) != []


def test_t0032_violation_is_caught():
    check_dependency_license.run("good")
    with pytest.raises(CheckError):
        check_dependency_license.run("violation")


def test_t0032_every_seeded_case_rejected_by_real_audit():
    for case, (dist, _what) in sorted(check_dependency_license.VIOLATION_CASES.items()):
        problems = check_dependency_license._audit_in_subprocess(
            check_dependency_license.FIXTURES / case, dist
        )
        assert problems, f"{case} escaped the real audit"


def test_t0032_seeded_good_cases_pass_real_audit():
    for case, (dist, _what) in sorted(check_dependency_license.GOOD_CASES.items()):
        assert not check_dependency_license._audit_in_subprocess(
            check_dependency_license.FIXTURES / case, dist
        ), f"{case} wrongly rejected"


def test_ci_wires_the_runner():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python -m tools.install_checks.runner" in ci


def test_seeded_violation_fixtures_tracked():
    r = subprocess.run(
        ["git", "ls-files", "tools/install_checks/fixtures"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    for needle in ("bad_format.py", "bad_type.py", "bad_api.py", "METADATA"):
        assert needle in r.stdout


def test_type_checker_is_a_real_dev_dependency():
    reqs = (ROOT / "requirements-dev.txt").read_text()
    assert "mypy" in reqs
