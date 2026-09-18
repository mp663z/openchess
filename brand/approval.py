"""T0006 v3 - No silent writes: binding approvals + role-per-action authorization.

Every state-changing operation on user data goes through ApprovalGate:
1. build an immutable typed Proposal (action + target/scope + before -> after),
2. present it (the canonical artifact IS what the approver sees),
3. only after an explicit Approval is recorded may the mutation commit.

v2 remediation (independent-review FAIL at d62a0b45): approvals bind to the
ENTIRE approval artifact, recomputed and constant-time compared at BOTH
approve() and commit(); approve() stores the canonical artifact itself.

v3 remediation (independent-review FAIL at 47eb9222): authorization, not just
authentication. The gate enforces role-per-action: only a role holding the
action's capability may approve (destructive user-data actions require the
`user` role and can never be delegated); scoped CapabilityGrants from a
`user`-role identity delegate exact (action, target-scope) non-destructive
capabilities and cannot widen. The authorization decision is bound into the
approval and audit records.
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
class CapabilityGrant:
    """A standing, scoped delegation of one capability from a `user`-role
    identity to another registered identity. Grants are exact: they cover one
    action and one target scope (exact target or a `prefix/*` subtree), and
    can never confer a destructive capability."""

    grantee: str
    action: str
    target_scope: str
    granted_by: str
    capability: str = "user-data-write"
    expires_at: str | None = None

    def covers(self, action: str, target: str) -> bool:
        if action != self.action:
            return False
        if self.target_scope.endswith("/*"):
            return target.startswith(self.target_scope[:-1])
        return target == self.target_scope


@dataclass(frozen=True)
class Approval:
    artifact_hash: str
    canonical_artifact: bytes
    approver_identity: str
    approver_role: str
    authorization: str  # how the approver was authorized: "role:user" or "grant:<granted_by>"
    approved_at: str


DESTRUCTIVE = "user-data-destructive"
DEFAULT_CAPABILITY = "user-data-write"

# Role -> capabilities. The `user` role (the data owner) holds everything;
# every other role holds nothing unless a CapabilityGrant delegates a scoped,
# non-destructive capability.
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "user": frozenset({DEFAULT_CAPABILITY, DESTRUCTIVE}),
    "reviewer": frozenset(),
    "service": frozenset(),
    "admin": frozenset(),
}

# Action -> required capability. Unknown actions default to
# DEFAULT_CAPABILITY; destructive user-data actions are explicit.
ACTION_CAPABILITIES: dict[str, str] = {
    "delete_game": DESTRUCTIVE,
    "delete_games": DESTRUCTIVE,
    "delete_all_user_data": DESTRUCTIVE,
}


class ApprovalGate:
    """Reference implementation for tooling-side state mutations.

    approvers: registry mapping approver identity -> role. Only registered
    identities may approve; the recorded role always comes from the registry,
    never from the caller. Authorization is role-per-action: an approval
    requires the role to hold the action's capability, or a CapabilityGrant
    from a `user`-role identity covering exactly this action and target.
    Registry membership is established out of band by the data owner; adding
    an identity confers only its role's capabilities.
    """

    def __init__(
        self,
        approvers: dict[str, str],
        grants: list[CapabilityGrant] | None = None,
        now: callable | None = None,
    ) -> None:
        if not approvers:
            raise ValueError("gate requires at least one registered approver")
        self._approvers = dict(approvers)
        self._grants = list(grants or [])
        self._now = now or (lambda: datetime.now(timezone.utc))  # noqa: UP017 - py3.10 runtime
        self._approvals: dict[str, Approval] = {}
        self.audit_log: list[dict[str, str]] = []

    def _required_capability(self, action: str) -> str:
        return ACTION_CAPABILITIES.get(action, DEFAULT_CAPABILITY)

    def _authorize(self, action: str, target: str, identity: str, role: str) -> str:
        capability = self._required_capability(action)
        if capability in ROLE_CAPABILITIES.get(role, frozenset()):
            return f"role:{role}"
        if capability == DESTRUCTIVE:
            raise SilentWriteError(
                f"destructive action {action!r} requires the user role; "
                "it cannot be delegated"
            )
        now = self._now()
        for grant in self._grants:
            if grant.grantee != identity or not grant.covers(action, target):
                continue
            grantor_role = self._approvers.get(grant.granted_by)
            if grantor_role != "user":
                continue  # only a user-role identity can delegate authority
            if grant.capability != capability:
                continue
            if grant.expires_at is not None and now >= datetime.fromisoformat(
                grant.expires_at
            ):
                continue  # expired grants confer nothing
            return f"grant:{grant.granted_by}"
        raise SilentWriteError(
            f"{identity!r} (role {role!r}) is not authorized for action "
            f"{action!r} on {target!r} (capability {capability!r})"
        )

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
        authorization = self._authorize(
            proposal.action, proposal.target, approver_identity, role
        )
        canonical = proposal.canonical  # recomputed from the live proposal
        artifact_hash = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(artifact_hash, proposal.artifact_hash):
            raise SilentWriteError("proposal artifact hash mismatch")
        approval = Approval(
            artifact_hash=artifact_hash,
            canonical_artifact=canonical,
            approver_identity=approver_identity,
            approver_role=role,
            authorization=authorization,
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
                "authorization": approval.authorization,
                "approved_at": approval.approved_at,
            }
        )
