"""T0006 v2 - No silent writes: executable invariant, binding approvals.

Every state-changing operation on user data goes through ApprovalGate:
1. build an immutable typed Proposal (action + target/scope + before -> after),
2. present it (the canonical artifact IS what the approver sees),
3. only after an explicit Approval is recorded may the mutation commit.

v2 remediation (independent-review FAIL at d62a0b45): approvals now bind to
the ENTIRE approval artifact. The gate recomputes the canonical artifact and
its sha256 at BOTH approve() and commit() and compares with
hmac.compare_digest; approve() stores the canonical artifact, not just its
hash, so a mutated or fabricated proposal cannot replay an approval.
Approvers must be registered identities; unknown identities and role
mismatches are rejected. Any mutation attempted without a prior approval for
its exact artifact raises SilentWriteError.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class SilentWriteError(Exception):
    pass


def _canonical(action: str, target: str, before: Any, after: Any) -> bytes:
    """Canonical serialization of the full approval artifact."""
    try:
        return json.dumps(
            {"action": action, "target": target, "before": before, "after": after},
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise SilentWriteError(f"proposal is not serializable: {exc}") from exc


@dataclass(frozen=True)
class Proposal:
    """Immutable, typed approval artifact. No caller-supplied hash field exists."""

    action: str
    target: str
    before: Any
    after: Any

    @property
    def canonical(self) -> bytes:
        return _canonical(self.action, self.target, self.before, self.after)

    @property
    def artifact_hash(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()


@dataclass(frozen=True)
class Approval:
    artifact_hash: str
    canonical_artifact: bytes
    approver_identity: str
    approver_role: str
    approved_at: str


class ApprovalGate:
    """Reference implementation for tooling-side state mutations.

    approvers: registry mapping approver identity -> role. Only registered
    identities may approve, and the recorded role always comes from the
    registry, never from the caller.
    """

    def __init__(self, approvers: dict[str, str]) -> None:
        if not approvers:
            raise ValueError("gate requires at least one registered approver")
        self._approvers = dict(approvers)
        self._approvals: dict[str, Approval] = {}
        self.audit_log: list[dict[str, str]] = []

    def _require_proposal(self, proposal: Any) -> Proposal:
        if not isinstance(proposal, Proposal):
            raise SilentWriteError(
                f"expected an immutable Proposal, got {type(proposal).__name__}"
            )
        return proposal

    def propose(self, action: str, target: str, before: Any, after: Any) -> Proposal:
        """Build the immutable artifact that must be approved."""
        return Proposal(action=action, target=target, before=before, after=after)

    def approve(self, proposal: Any, approver_identity: str) -> Approval:
        proposal = self._require_proposal(proposal)
        role = self._approvers.get(approver_identity)
        if role is None:
            raise SilentWriteError(f"unregistered approver {approver_identity!r}")
        canonical = proposal.canonical  # recomputed from the live proposal
        artifact_hash = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(artifact_hash, proposal.artifact_hash):
            raise SilentWriteError("proposal artifact hash mismatch")
        approval = Approval(
            artifact_hash=artifact_hash,
            canonical_artifact=canonical,
            approver_identity=approver_identity,
            approver_role=role,
            approved_at=datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - py3.10 runtime
        )
        self._approvals[artifact_hash] = approval
        return approval

    def commit(self, proposal: Any) -> None:
        """Commit the mutation; raises unless this exact artifact was approved."""
        proposal = self._require_proposal(proposal)
        canonical = proposal.canonical  # recomputed at commit time
        artifact_hash = hashlib.sha256(canonical).hexdigest()
        approval = self._approvals.get(artifact_hash)
        if approval is None:
            raise SilentWriteError(
                f"action {proposal.action!r} has no approval for artifact "
                f"{artifact_hash[:12]}"
            )
        # Constant-time compare of hash AND the stored canonical artifact:
        # the committed proposal must be byte-identical to what was approved.
        hash_ok = hmac.compare_digest(artifact_hash, approval.artifact_hash)
        artifact_ok = hmac.compare_digest(canonical, approval.canonical_artifact)
        if not (hash_ok and artifact_ok):
            raise SilentWriteError("proposal differs from the approved artifact")
        del self._approvals[artifact_hash]  # single-use: one approval, one commit
        self.audit_log.append(
            {
                "action": proposal.action,
                "target": proposal.target,
                "artifact_hash": artifact_hash,
                "approver_identity": approval.approver_identity,
                "approver_role": approval.approver_role,
                "approved_at": approval.approved_at,
            }
        )
