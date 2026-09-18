"""T0033/T0034/T0035: rights, SBOM and CLA/DCO install checks."""

import pytest
import yaml

from tools import cla_check, rights_audit
from tools.install_checks import (
    CheckError,
    check_cla_dco,
    check_data_model_rights,
    check_sbom_signing,
    runner,
)


def test_discovery_finds_all_five_checks():
    ids = set(runner.check_ids())
    assert {"T0021", "T0032", "T0033", "T0034", "T0035"} <= ids


@pytest.mark.parametrize("check", [
    check_data_model_rights, check_sbom_signing, check_cla_dco,
])
def test_dual_mode(check):
    check.run("good")
    with pytest.raises(CheckError):
        check.run("violation")


@pytest.mark.parametrize("only", [{"T0033"}, {"T0034"}, {"T0035"}])
def test_runner_lane_green(only):
    assert runner.run_all(only=only) == []


def test_t0033_each_fixture_rejected_for_its_own_reason():
    for fname, expect in sorted(check_data_model_rights.VIOLATIONS.items()):
        problems = rights_audit.validate_sources(
            yaml.safe_load((check_data_model_rights.FIXTURES / fname).read_text())
        )
        assert any(expect in p for p in problems), f"{fname}: {problems}"


def test_t0033_real_manifest_passes_full_audit():
    doc = yaml.safe_load(rights_audit.RIGHTS.read_text())
    assert rights_audit.validate(doc) == []


def test_t0035_registry_fixtures_each_raise():
    for name, fn in sorted(check_cla_dco.REGISTRY_VIOLATIONS.items()):
        with pytest.raises(cla_check.ClaError):
            fn(), name


def test_t0035_de_minimis_truth_table():
    assert cla_check.is_de_minimis(5, [("M", "docs/faq.md")])
    for name, (lines, files) in sorted(check_cla_dco.DEMINIMIS_VIOLATIONS.items()):
        assert not cla_check.is_de_minimis(lines, files), name
