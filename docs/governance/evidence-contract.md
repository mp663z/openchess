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

## Judgment-substituted human checkpoints (owner-delegated, 2026-09-19)

A task whose board verification mode is EXACTLY `human checkpoint` AND
whose board `checkpoint` is nonempty may complete on a judgment-
substituted verdict instead of a human PASS:

    Verification: human checkpoint - judgment-substituted per owner
    wamid.<id> (YYYY-MM-DD); provisional decision:
    evidence/substitutions/TNNNN.md; re-verify hooks: Txxxx, Tyyyy

The mode is pinned, not self-authorizing:

- `data/judgment-grants.yaml` is the grant registry; its sha256 is
  pinned in `tools/evidence_lint.py` (`GRANTS_SHA256`). Editing the
  registry without changing trusted code fails closed and every
  substitution is refused. The registry content is verified against the
  trusted owner-channel evidence (the named wamids) before the pin is
  set; the lint checks the pin, never the claim.
- The verdict must match the grant exactly: owner wamid, date,
  substitution-record path, and the full hook list.
- The record must exist, title-name the task, carry the grant wamid,
  and contain every required field: `Task:`, `What the human would
  have done:`, `Why no human pass happened:`, `Provisional substitute
  decision:`, `Re-verify hook:`.
- Every hook must be a real dag task distinct from the source task,
  not done, tagged `reverify:<task>` in `tasks/dag.json`, and carry a
  human/independent re-verification mode. When the hook later runs,
  its result supersedes the substitution.
- `auto + human`, `human/external`, `human/legal audit`,
  `independent verifier`, and every other mode are NOT substitutable.
  Widening the class is a trusted-code change.

First grants: T2357 (Week-1 product-code gate) per owner wamid
...MzI5NjgyNUIzREQA (2026-09-19), hooks T2228/T2288/T2356. See
evidence/substitutions/README.md.

### Registry governance (required process)

Changing `data/judgment-grants.yaml` and `GRANTS_SHA256` in one PR is
how a NEW grant is authorized, so the registry content can never be
self-authorizing: every new or changed grant entry must be verified
against the trusted owner channel by the main agent BEFORE the pin is
meaningful, and the PR evidence must record that verification (wamid,
owner-channel timestamp, scope). A GRANTS_SHA256 change without a
recorded trusted-channel verification is a review blocker. Process as
actually run for the first grant (T2357): main verified
wamid...MzI5NjgyNUIzREQA in the phone sink (owner, 2026-09-19
12:28:45 IST) and confirmed the registry entry matched the steering
scope; only then was the pin treated as authoritative.
