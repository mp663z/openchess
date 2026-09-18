"""T0006 v3: binding approvals + role-per-action authorization."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from brand.approval import (
    Approval,
    ApprovalGate,
    CapabilityGrant,
    Proposal,
    SilentWriteError,
)

APPROVERS = {"abhishek": "user", "review-bot": "reviewer"}


def make_gate():
    return ApprovalGate(APPROVERS)


def test_unapproved_mutation_raises():
    gate = make_gate()
    proposal = gate.propose("update_repertoire", "repertoire/main", {"line": "e4"}, {"line": "d4"})
    with pytest.raises(SilentWriteError):
        gate.commit(proposal)


def test_approved_mutation_commits_and_audits():
    gate = make_gate()
    proposal = gate.propose("update_repertoire", "repertoire/main", {"line": "e4"}, {"line": "d4"})
    gate.approve(proposal, "abhishek")
    gate.commit(proposal)
    assert gate.audit_log[0]["action"] == "update_repertoire"
    assert gate.audit_log[0]["target"] == "repertoire/main"
    assert gate.audit_log[0]["approver_identity"] == "abhishek"
    assert gate.audit_log[0]["approver_role"] == "user"


def test_approval_binds_to_exact_diff():
    gate = make_gate()
    p1 = gate.propose("update_repertoire", "repertoire/main", {"line": "e4"}, {"line": "d4"})
    gate.approve(p1, "abhishek")
    p2 = gate.propose("update_repertoire", "repertoire/main", {"line": "e4"}, {"line": "c4"})
    with pytest.raises(SilentWriteError):
        gate.commit(p2)


def test_action_is_part_of_the_artifact():
    """Verifier finding 2: action must be inside the hashed artifact."""
    gate = make_gate()
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    gate.approve(p, "abhishek")
    # Same before/after but a DIFFERENT action must not replay the approval.
    evil = Proposal(
        action="delete_all_user_data",
        target=p.target,
        before=p.before,
        after=p.after,
    )
    with pytest.raises(SilentWriteError):
        gate.commit(evil)
    assert p.artifact_hash != evil.artifact_hash


def test_target_scope_is_part_of_the_artifact():
    gate = make_gate()
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    gate.approve(p, "abhishek")
    widened = Proposal(action=p.action, target="notes/ALL", before=p.before, after=p.after)
    with pytest.raises(SilentWriteError):
        gate.commit(widened)


def test_before_after_mutation_with_stale_hash_rejected():
    """Mutating the proposal payload after approval invalidates it."""
    gate = make_gate()
    before = {"ids": ["g1"]}
    after = {"ids": []}
    p = gate.propose("delete_games", "games", before, after)
    gate.approve(p, "abhishek")
    before["ids"].append("g2")  # attacker mutates the shared payload
    with pytest.raises(SilentWriteError):
        gate.commit(p)
    assert gate.audit_log == []


def test_fabricated_hash_dict_rejected():
    """Verifier repro: fabricated dicts with attacker-chosen hashes must fail."""
    gate = make_gate()
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    gate.approve(p, "abhishek")
    forged = {
        "action": "delete_all_user_data",
        "target": "users",
        "before": {},
        "after": None,
        "diff_hash": p.artifact_hash,  # stolen hash from the approved proposal
    }
    with pytest.raises(SilentWriteError):
        gate.commit(forged)
    with pytest.raises(SilentWriteError):
        gate.approve(forged, "abhishek")


def test_malformed_proposal_rejected():
    gate = make_gate()
    for bad in (None, 42, "proposal", {"action": "x"}, object()):
        with pytest.raises(SilentWriteError):
            gate.commit(bad)
        with pytest.raises(SilentWriteError):
            gate.approve(bad, "abhishek")
    bad_payload = gate.propose("a", "t", object(), None)
    with pytest.raises(SilentWriteError):
        _ = bad_payload.canonical  # unserializable payload, fail-closed


def test_invalid_approver_rejected():
    gate = make_gate()
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    with pytest.raises(SilentWriteError):
        gate.approve(p, "mallory")  # not registered
    with pytest.raises(SilentWriteError):
        gate.approve(p, "")  # empty identity


def test_gate_requires_registered_approvers():
    with pytest.raises(ValueError, match="approver"):
        ApprovalGate({})


def test_approval_single_use():
    gate = make_gate()
    proposal = gate.propose("delete_game", "games/g1", {"id": "g1"}, None)
    gate.approve(proposal, "abhishek")
    gate.commit(proposal)
    with pytest.raises(SilentWriteError):
        gate.commit(proposal)


def test_stored_canonical_artifact_is_what_gets_audited():
    """The audited artifact is byte-identical to what the approver saw."""
    gate = make_gate()
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    approval = gate.approve(p, "abhishek")
    expected = json.dumps(
        {"action": "rename_note", "target": "notes/n1",
         "before": {"title": "a"}, "after": {"title": "b"}},
        sort_keys=True,
    ).encode()
    assert approval.canonical_artifact == expected
    assert approval.artifact_hash == hashlib.sha256(expected).hexdigest()
    gate.commit(p)
    assert gate.audit_log[0]["artifact_hash"] == approval.artifact_hash


def test_approval_record_is_immutable():
    gate = make_gate()
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    approval = gate.approve(p, "abhishek")
    assert isinstance(approval, Approval)
    with pytest.raises(AttributeError):
        approval.approver_role = "admin"  # type: ignore[misc]


# --- v3: role-per-action authorization -------------------------------------


def test_reviewer_cannot_approve_user_data_write():
    """Registered identity without the capability is not authorized."""
    gate = make_gate()
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    with pytest.raises(SilentWriteError, match="not authorized"):
        gate.approve(p, "review-bot")


@pytest.mark.parametrize("role", ["reviewer", "service", "admin"])
def test_non_user_roles_cannot_approve_user_data_writes(role):
    gate = ApprovalGate({"abhishek": "user", "agent-x": role})
    p = gate.propose("update_repertoire", "repertoire/main", {"line": "e4"}, {"line": "d4"})
    with pytest.raises(SilentWriteError):
        gate.approve(p, "agent-x")


def test_destructive_action_requires_user_role():
    gate = ApprovalGate({"abhishek": "user", "ops": "admin"})
    p = gate.propose("delete_all_user_data", "users", {"n": 3}, None)
    with pytest.raises(SilentWriteError, match="destructive"):
        gate.approve(p, "ops")
    gate.approve(p, "abhishek")  # user role holds the destructive capability
    gate.commit(p)
    assert gate.audit_log[0]["authorization"] == "role:user"


def test_destructive_capability_cannot_be_delegated_by_grant():
    grant = CapabilityGrant(
        grantee="review-bot",
        action="delete_all_user_data",
        target_scope="users",
        granted_by="abhishek",
        capability="user-data-destructive",
    )
    gate = ApprovalGate(APPROVERS, grants=[grant])
    p = gate.propose("delete_all_user_data", "users", {"n": 3}, None)
    with pytest.raises(SilentWriteError, match="destructive"):
        gate.approve(p, "review-bot")


def test_scoped_grant_allows_exact_action_and_target():
    grant = CapabilityGrant(
        grantee="review-bot",
        action="rename_note",
        target_scope="notes/*",
        granted_by="abhishek",
    )
    gate = ApprovalGate(APPROVERS, grants=[grant])
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    approval = gate.approve(p, "review-bot")
    assert approval.authorization == "grant:abhishek"
    gate.commit(p)
    assert gate.audit_log[0]["authorization"] == "grant:abhishek"


def test_scoped_grant_cannot_widen_action_or_target():
    grant = CapabilityGrant(
        grantee="review-bot",
        action="rename_note",
        target_scope="notes/*",
        granted_by="abhishek",
    )
    gate = ApprovalGate(APPROVERS, grants=[grant])
    other_action = gate.propose("delete_note", "notes/n1", {"title": "a"}, None)
    with pytest.raises(SilentWriteError):
        gate.approve(other_action, "review-bot")
    other_target = gate.propose("rename_note", "games/g1", {"title": "a"}, {"title": "b"})
    with pytest.raises(SilentWriteError):
        gate.approve(other_target, "review-bot")
    exact = CapabilityGrant(
        grantee="review-bot",
        action="rename_note",
        target_scope="notes/n1",
        granted_by="abhishek",
    )
    gate2 = ApprovalGate(APPROVERS, grants=[exact])
    off_scope = gate2.propose("rename_note", "notes/n2", {"title": "a"}, {"title": "b"})
    with pytest.raises(SilentWriteError):
        gate2.approve(off_scope, "review-bot")


def test_grant_from_non_user_grantor_confers_nothing():
    grant = CapabilityGrant(
        grantee="review-bot",
        action="rename_note",
        target_scope="notes/*",
        granted_by="review-bot",  # reviewer cannot delegate authority
    )
    gate = ApprovalGate(APPROVERS, grants=[grant])
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    with pytest.raises(SilentWriteError):
        gate.approve(p, "review-bot")


def test_expired_grant_confers_nothing():
    now = datetime.now(timezone.utc)  # noqa: UP017 - py3.10 runtime
    grant = CapabilityGrant(
        grantee="review-bot",
        action="rename_note",
        target_scope="notes/*",
        granted_by="abhishek",
        expires_at=(now - timedelta(minutes=1)).isoformat(),
    )
    gate = ApprovalGate(APPROVERS, grants=[grant], now=lambda: now)
    p = gate.propose("rename_note", "notes/n1", {"title": "a"}, {"title": "b"})
    with pytest.raises(SilentWriteError):
        gate.approve(p, "review-bot")


def test_authorization_bound_into_approval_and_audit():
    gate = make_gate()
    p = gate.propose("update_repertoire", "repertoire/main", {"line": "e4"}, {"line": "d4"})
    approval = gate.approve(p, "abhishek")
    assert approval.authorization == "role:user"
    gate.commit(p)
    assert gate.audit_log[0]["authorization"] == "role:user"


def test_default_policy_unknown_action_requires_user():
    gate = ApprovalGate({"abhishek": "user", "svc": "service"})
    p = gate.propose("brand_new_action", "anything", {"a": 1}, {"a": 2})
    with pytest.raises(SilentWriteError):
        gate.approve(p, "svc")
    gate.approve(p, "abhishek")
