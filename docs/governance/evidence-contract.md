# Evidence contract (T0007)

Done requires acceptance, an exact SHA, and a verifier manifest. UNVERIFIED
is never done.

A task is DONE on the board only when ALL of the following hold:

1. **Acceptance met** - the task's acceptance text (tasks/dag.json) is
   satisfied by the artifact at the recorded SHA.
2. **Exact SHA** - the board records the exact merge commit on main that
   carries the artifact (`dag.py complete --sha`). "On a branch", "in a PR",
   and "locally" are not done.
3. **Evidence manifest** - evidence/TNNNN.md passes tools/evidence_lint.py
   (strict field parsing, cross-checked against tasks/dag.json):
   - title naming the task id;
   - `Status: done | in-progress | pending` on its own line - prose claims
     mean nothing; done requires the board to say done;
   - `Recorded merge SHA on main: <40-hex>` (required when Status: done) and
     it must EQUAL the board's done_sha;
   - `Verification: <exact board verification mode> - <detail>`; done tasks
     in independent/human modes need a `PASS at <sha>` verdict naming the
     same SHA; UNVERIFIED is never done;
   - `Commands: \`<command>\` ... Environment: <env>` reproducing the evidence.
4. **Verifier manifest** - tasks whose verification mode is `independent
   review` / `independent verifier` / `auto + independent review` are done
   only after the independent verifier's PASS is recorded against the exact
   merge SHA. Auto-verified tasks are done on green CI plus the lint-clean
   evidence file. UNVERIFIED, self-asserted, or stale-SHA verdicts are not
   done.

## Pre-contract files

Grandfathering is FROZEN, not claimed: data/evidence-pre-contract.yaml pins
the 23 grandfathered paths to the sha256 of their bytes. A file is
grandfathered only when it carries an exact `pre-contract: true` footer line
AND is allowlisted AND its content hash matches - an inline prose mention of
the marker means nothing, and any edit invalidates grandfathering (the file
must then meet the full contract). The task-level rules (exact SHA on the
board, verifier PASS for independent-review tasks) applied when those tasks
completed.

## Enforcement

- tools/evidence_lint.py lints every evidence/*.md file; CI and the
  pre-push hook run it.
- tools/dag.py complete refuses without --sha and --evidence (existing).
- tests/test_evidence_lint.py locks the lint behavior with adversarial fixtures.
