"""T0021/T0032: install-check harness - discovery, dual-mode, fault injection."""

import subprocess
from pathlib import Path

from tools.install_checks import CheckError, check_dependency_license, check_format_type_api, runner

ROOT = Path(__file__).resolve().parent.parent


def test_runner_both_modes_green():
    assert runner.run_all() == []


def test_discovery_finds_checks_without_registry():
    names = runner.discover()
    assert "check_format_type_api" in names
    assert "check_dependency_license" in names


def test_t0021_violation_is_caught():
    check_format_type_api.run("good")
    import pytest

    with pytest.raises(CheckError):
        check_format_type_api.run("violation")


def test_t0032_violation_is_caught():
    check_dependency_license.run("good")
    import pytest

    with pytest.raises(CheckError):
        check_dependency_license.run("violation")


def test_ci_wires_the_runner():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python -m tools.install_checks.runner" in ci


def test_seeded_violation_fixtures_tracked():
    r = subprocess.run(
        ["git", "ls-files", "tools/install_checks/fixtures"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert "seed_bad.py" in r.stdout
    assert "METADATA" in r.stdout
