"""T0006 - No silent writes: executable invariant.

Every state-changing operation on user data goes through ApprovalGate:
1. compute a visible diff (before -> after),
2. present it (the diff IS the approval artifact),
3. only after an explicit Approval is recorded may the mutation commit.

A mutation attempted without a prior approval for its exact diff raises
SilentWriteError. Approvals bind to the diff hash, so changing the diff
invalidates the approval.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class SilentWriteError(Exception):
    pass


@dataclass(frozen=True)
class Approval:
    diff_hash: str
    approver_role: str
    approved_at: str


def diff_hash(before: Any, after: Any) -> str:
    canonical = json.dumps({"before": before, "after": after}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ApprovalGate:
    """Reference implementation for tooling-side state mutations."""

    def __init__(self) -> None:
        self._approvals: dict[str, Approval] = {}
        self.audit_log: list[dict[str, str]] = []

    def propose(self, action: str, before: Any, after: Any) -> dict:
        """Compute the visible diff that must be approved."""
        return {
            "action": action,
            "before": before,
            "after": after,
            "diff_hash": diff_hash(before, after),
        }

    def approve(self, proposal: dict, approver_role: str) -> Approval:
        approval = Approval(
            diff_hash=proposal["diff_hash"],
            approver_role=approver_role,
            approved_at=datetime.now(timezone.utc).isoformat()  # noqa: UP017 - python 3.10 runtime,
        )
        self._approvals[approval.diff_hash] = approval
        return approval

    def commit(self, proposal: dict) -> None:
        """Commit the mutation; raises unless the exact diff was approved."""
        dh = proposal["diff_hash"]
        if dh not in self._approvals:
            raise SilentWriteError(
                f"action {proposal['action']!r} has no approval for diff {dh[:12]}"
            )
        approval = self._approvals.pop(dh)  # single-use: one approval, one commit
        self.audit_log.append(
            {
                "action": proposal["action"],
                "diff_hash": dh,
                "approver_role": approval.approver_role,
                "approved_at": approval.approved_at,
            }
        )
