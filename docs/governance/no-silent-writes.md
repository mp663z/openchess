# No silent writes (T0006)

Invariant: no state-changing operation on user data commits without a
visible approval artifact and an explicit, AUTHORIZED approval bound to
that exact artifact.

- The artifact is the whole proposal: action + target/scope + before/after,
  canonically serialized. What you approve is what commits, byte for byte.
- The gate recomputes the canonical artifact and its sha256 at both approve()
  and commit() and compares with hmac.compare_digest; approve() stores the
  canonical artifact itself, so a mutated or fabricated proposal cannot
  replay an approval.
- Approvals are single-use; re-running a mutation needs a fresh approval.

## Authorization model

Authentication is not enough: approve() enforces role-per-action.

- Every action maps to a required capability (default `user-data-write`;
  destructive actions like delete_game(s)/delete_all_user_data map to
  `user-data-destructive`). Unknown actions default to `user-data-write`
  (fail-closed).
- The `user` role (the data owner) holds all capabilities. All other roles
  hold none by default.
- Destructive capabilities require the `user` role directly and can never be
  delegated.
- A CapabilityGrant lets a registered `user`-role identity delegate one
  exact (action, target-scope) non-destructive capability to another
  registered identity. Scopes are exact targets or `prefix/*` subtrees;
  grants expire; a grant from a non-user grantor confers nothing; a grant
  cannot widen its action or target.
- The authorization decision ("role:user" or "grant:<grantor>") is bound
  into the approval record and the audit log.

## Registry policy

Approvers enter the registry only by an explicit edit to the gate
configuration reviewed like any other governance change (this file +
brand/approval.py + its tests, in one PR, with independent review). Registry
membership confers ONLY the capabilities of the registered role per the
table in brand/approval.py (ROLE_CAPABILITIES); it never confers caller-
supplied roles or ad-hoc authority. There is no "every registered identity
is fully authorized" mode.

- brand/approval.py is the reference implementation for tooling; the product
  core (Rust, per ADR-0001) implements the same gate for all user-data
  mutations. Tests in tests/test_no_silent_writes.py lock the behavior,
  including adversarial mutation/forgery and authorization cases.
