# Evidence contract (T0007)

Done requires acceptance, an exact SHA, and a verifier manifest. UNVERIFIED
is never done.

A task is DONE on the board only when ALL of the following hold:

1. **Acceptance met** - the task's acceptance text (tasks/dag.json) is
   satisfied by the artifact at the recorded SHA.
2. **Exact SHA** - the board records the exact merge commit on main that
   carries the artifact (`dag.py complete --sha`). "On a branch", "in a PR",
   and "locally" are not done.
3. **Evidence manifest** - evidence/TNNNN.md passes tools/evidence_lint.py:
   - title naming the task id,
   - at least one exact 40-hex SHA (or the literal word `pending` when the
     task is in progress),
   - a `Verification:` line naming the task's verification mode and, for
     independent-review tasks, the verifier verdict with its SHA,
   - a `Commands:` line giving the exact commands that reproduce the
     evidence, with environment.
4. **Verifier manifest** - tasks whose verification mode is `independent
   review` / `independent verifier` / `auto + independent review` are done
   only after the independent verifier's PASS is recorded against the exact
   merge SHA. Auto-verified tasks are done on green CI plus the lint-clean
   evidence file. UNVERIFIED, self-asserted, or stale-SHA verdicts are not
   done.

## Pre-contract files

Evidence files written before this contract carry the marker
`pre-contract: true` with a retroactive index line. They are grandfathered
for structure only: the task-level rules (exact SHA on the board, verifier
PASS for independent-review tasks) still applied when they completed. Any
edit to a pre-contract file must bring it to the full contract.

## Enforcement

- tools/evidence_lint.py lints every evidence/*.md file; CI and the
  pre-push hook run it.
- tools/dag.py complete refuses without --sha and --evidence (existing).
- tests/test_evidence_contract.py locks the lint behavior with fixtures.
