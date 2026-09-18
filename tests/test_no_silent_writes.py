"""T0006 v2: approvals bind to the full artifact; silent writes impossible."""

import hashlib
import json

import pytest

from brand.approval import Approval, ApprovalGate, Proposal, SilentWriteError

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
    # Role comes from the registry, never from caller-supplied strings.
    approval = gate.approve(p, "review-bot")
    assert approval.approver_role == "reviewer"


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
