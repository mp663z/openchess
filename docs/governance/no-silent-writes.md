# No silent writes (T0006)

Invariant: no state-changing operation on user data commits without a
visible approval artifact and an explicit approval bound to that exact
artifact.

- The artifact is the whole proposal: action + target/scope + before/after,
  canonically serialized. What you approve is what commits, byte for byte.
- The gate recomputes the canonical artifact and its sha256 at both approve()
  and commit() and compares with hmac.compare_digest; approve() stores the
  canonical artifact itself, so a mutated or fabricated proposal cannot
  replay an approval.
- Approvals are single-use; re-running a mutation needs a fresh approval.
- Approvers are registered identities; the recorded role comes from the
  gate's registry, never from caller-supplied strings.
- brand/approval.py is the reference implementation for tooling; the product
  core (Rust, per ADR-0001) implements the same gate for all user-data
  mutations. Tests in tests/test_no_silent_writes.py lock the behavior,
  including adversarial mutation/forgery cases.
