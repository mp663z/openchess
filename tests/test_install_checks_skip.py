"""Local-only skip for T0036 and the fail-closed gh api timeout.

No test here runs tools/setup.sh or touches the clone's git config: the
branch guard's verdict is replaced with monkeypatch.
"""

import subprocess

import pytest

from tools import branch_guard, cla_check
from tools.install_checks import CheckError, CheckSkipped, in_ci, runner
from tools.install_checks import check_branch_protection as t0036

UNSET = [branch_guard.HOOKS_PATH_UNSET]
_REAL_VERIFY = branch_guard.verify


def _real_repo_verdict(problems):
    """Replace only the real-repo verdict; fixture trees stay real."""
    def verify(root=None, *a, **k):
        return list(problems) if root is None else _REAL_VERIFY(root, *a, **k)
    return verify


def _ci(monkeypatch, on):
    if on:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.mark.parametrize("value", ["true"])  # the only CI value
def test_skip_never_fires_in_ci(monkeypatch, value):
    monkeypatch.setenv("GITHUB_ACTIONS", value)
    monkeypatch.setattr(branch_guard, "verify", _real_repo_verdict(UNSET))
    with pytest.raises(CheckError, match="core.hooksPath is not configured"):
        t0036.run("good")
    skipped: list[str] = []
    failures = runner.run_all(only={"T0036"}, skipped=skipped)
    assert skipped == []
    assert failures and "T0036" in failures[0] and "mode=good" in failures[0]


@pytest.mark.parametrize("value", ["false", "", "1", "TRUE", "True", " true"])
def test_ci_is_exactly_github_actions_true(monkeypatch, value):
    monkeypatch.setenv("GITHUB_ACTIONS", value)
    assert in_ci() is False
    monkeypatch.setattr(branch_guard, "verify", _real_repo_verdict(UNSET))
    with pytest.raises(CheckSkipped, match="check_branch_protection"):
        t0036.run("good")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert in_ci() is True


def test_skip_without_a_skipped_list_is_a_failure(monkeypatch):
    _ci(monkeypatch, False)
    monkeypatch.setattr(branch_guard, "verify", _real_repo_verdict(UNSET))
    failures = runner.run_all(only={"T0036"})
    assert failures and "skip not recorded" in failures[0]


def test_skip_fires_locally_only_for_unset_hooks_path(monkeypatch):
    _ci(monkeypatch, False)
    monkeypatch.setattr(branch_guard, "verify", _real_repo_verdict(UNSET))
    with pytest.raises(CheckSkipped, match="check_branch_protection"):
        t0036.run("good")
    skipped: list[str] = []
    assert runner.run_all(only={"T0036"}, skipped=skipped) == []
    assert len(skipped) == 1 and "check_branch_protection" in skipped[0]


@pytest.mark.parametrize("problems", [
    UNSET + ["pre-push hook missing"],
    ["core.hooksPath must be exactly '.githooks', got 'x'"],
    ["pre-push hook missing at .githooks/pre-push"],
])
def test_other_guard_problems_still_fail_locally(monkeypatch, problems):
    _ci(monkeypatch, False)
    monkeypatch.setattr(branch_guard, "verify", _real_repo_verdict(problems))
    with pytest.raises(CheckError):
        t0036.run("good")


@pytest.mark.parametrize("ci,mode", [(True, "good"), (True, "violation"),
                                     (False, "violation")])
def test_runner_rejects_a_skip_outside_local_good_mode(monkeypatch, ci, mode):
    _ci(monkeypatch, ci)

    def run(m):
        if m == mode:
            raise CheckSkipped("forged skip")
        if m == "violation":
            raise CheckError("caught")

    monkeypatch.setattr(t0036, "run", run)
    skipped: list[str] = []
    failures = runner.run_all(only={"T0036"}, skipped=skipped)
    assert skipped == []
    assert failures and "skip not allowed" in failures[0]


def test_gh_api_timeout_fails_closed(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "t")
    seen = {}

    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(cla_check.subprocess, "run", hung)
    with pytest.raises(cla_check.ClaError, match="timed out"):
        cla_check._gh_api("repos/x/y/pulls/1")
    assert seen["timeout"] == cla_check.GH_API_TIMEOUT_S == 30
