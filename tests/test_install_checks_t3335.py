"""T0033/T0034/T0035: rights, SBOM and CLA/DCO install checks."""

import pytest

from tools import cla_check
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


def test_t0033_fixtures_and_real_manifest():
    # per-fixture assertions and full-audit coverage live in
    # tests/test_public_source_rights.py (single home next to the manifest)
    assert (check_data_model_rights.FIXTURES / "bad_fake_license.yaml").exists()


@pytest.mark.parametrize("name", sorted(check_cla_dco.VIOLATIONS))
def test_t0035_each_seeded_attack_rejected_end_to_end(name):
    kw = check_cla_dco.VIOLATIONS[name]
    with pytest.raises(cla_check.ClaError), check_cla_dco._seeded_pr(**kw):
        pass


@pytest.mark.parametrize("name", sorted(check_cla_dco.GOOD))
def test_t0035_good_scenarios_pass_end_to_end(name):
    with check_cla_dco._seeded_pr(**check_cla_dco.GOOD[name]) as result:
        assert isinstance(result, str) and result


def test_t0035_verify_acceptance_neutralized_fails_harness(monkeypatch):
    from tools.install_checks import runner

    monkeypatch.setattr(cla_check, "verify_acceptance", lambda *a, **k: None)
    assert runner.run_all(only={"T0035"}) != []
