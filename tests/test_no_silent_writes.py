"""T0006: visible diff + approval required; silent writes impossible."""

import pytest

from brand.approval import ApprovalGate, SilentWriteError


def test_unapproved_mutation_raises():
    gate = ApprovalGate()
    proposal = gate.propose("update_repertoire", {"line": "e4"}, {"line": "d4"})
    with pytest.raises(SilentWriteError):
        gate.commit(proposal)


def test_approved_mutation_commits_and_audits():
    gate = ApprovalGate()
    proposal = gate.propose("update_repertoire", {"line": "e4"}, {"line": "d4"})
    assert proposal["before"] == {"line": "e4"} and proposal["after"] == {"line": "d4"}
    gate.approve(proposal, "user")
    gate.commit(proposal)
    assert gate.audit_log[0]["action"] == "update_repertoire"
    assert gate.audit_log[0]["approver_role"] == "user"


def test_approval_binds_to_exact_diff():
    gate = ApprovalGate()
    p1 = gate.propose("update_repertoire", {"line": "e4"}, {"line": "d4"})
    gate.approve(p1, "user")
    p2 = gate.propose("update_repertoire", {"line": "e4"}, {"line": "c4"})  # changed diff
    with pytest.raises(SilentWriteError):
        gate.commit(p2)


def test_approval_single_use():
    gate = ApprovalGate()
    proposal = gate.propose("delete_game", {"id": "g1"}, None)
    gate.approve(proposal, "user")
    gate.commit(proposal)
    with pytest.raises(SilentWriteError):
        gate.commit(proposal)  # second commit needs fresh approval
