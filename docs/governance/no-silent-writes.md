# No silent writes (T0006)

Invariant: no state-changing operation on user data commits without a
visible diff and an explicit approval bound to that exact diff.

- The diff is the approval artifact: what you approve is what commits.
- Approvals are single-use; re-running a mutation needs a fresh approval.
- Changing the diff invalidates the approval (binding by diff hash).
- brand/approval.py is the reference implementation for tooling; the product
  core (Rust, per ADR-0001) implements the same gate for all user-data
  mutations. Tests in tests/test_no_silent_writes.py lock the behavior.
