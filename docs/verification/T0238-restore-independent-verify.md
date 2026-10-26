# T0238 Store/restore/independent verify - verification package

Scope: the COMPLETE store restore chain (T0230-T0237) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
builder lead lane. PROVENANCE DISCLOSURE: the author implemented NO link
of this chain; all eight links were built and reviewed by other lanes,
and every link commit carries the shared "Instinct Agent
<agent@instinct.com>" authorship, distinct from the package author's
builder identity (openchess-builder). This package asserts no authority.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0230 | Store/restore/contract | ffa471c2dd0dc9549af898061bc1411214eeebd0 |
| T0231 | Store/restore/fixture | 0644734b03caa240dee34325130cf25da7b74674 |
| T0232 | Store/restore/red test | 32a2a96d03c80e850396c9d7b3672c3ccce5daff |
| T0233 | Store/restore/implement | 6207b27dc75b1782595328a9c28390ed08094852 |
| T0234 | Store/restore/unit/property | 55b681cc14e0e5fac7570a4a9eae78cf75d71997 |
| T0235 | Store/restore/fuzz/fault | bb3c047457989b21508085b319457b1e2076c57b |
| T0236 | Store/restore/integration/restart | af61e9dfb1942e96c7a248329aa7a37f17da2dc5 |
| T0237 | Store/restore/instrument | 262c5972f4760eaa15d69eed14e6949618fea78f |

Every merge SHA was confirmed an ancestor of main at the review head by
the author (`git merge-base --is-ancestor` for each of the 8). Every
link was reviewed by independent verifiers before merge; the per-link
verdicts (exact reviewed head SHAs, scope statements, remediation
history, CI run links) are recorded in evidence/T0230.md through
evidence/T0237.md on main. This package submits the chain inventory and
reproducible artifacts only - it does not certify itself and asserts no
authority; the task closes under the independent review and CI gates,
not on this document.

## Content hashes at review head

- data/contracts/restore.yaml: 59c1fac278de4d6eb95121ea122559c1c92539ec2b74ff315808330abbee50df
- store/restore.py: fce13d39ddea3d520517c502d0f13bfcb4a937c74a6c6f31c1ed68201f82452c
- store/restore_instrument.py: 46782f8c5d7a13e0f558f365ab5a1c53f9d90c3889227eb4c5a5ccf6836248ea
- tests/fixtures/restore/cases.json: deac797e251f9873211f53342f9510640045a906752096cd35bc960e4a83c0a0
- main at package assembly: 205bf35dec6a60ed88cce124c985cbbc4ab155a3

## Full-chain suite result

Command: `python -m pytest tests/test_t0230_restore_contract.py tests/test_t0231_restore_fixture.py tests/test_t0232_restore_red.py tests/test_t0233_restore_runtime.py tests/test_t0234_restore_properties.py tests/test_t0235_restore_fuzz_fault.py tests/test_t0236_restore_integration_restart.py tests/test_t0237_restore_instrument.py -q`

Author's own rerun at the review head: 640 passed, 0 failed, in a single
pytest-xdist `-n 4` invocation of exactly the command's file set; the
instrument file additionally run serially (49 passed). Per file:
test_t0230_restore_contract.py 51, test_t0231_restore_fixture.py 33,
test_t0232_restore_red.py 41, test_t0233_restore_runtime.py 6,
test_t0234_restore_properties.py 297, test_t0235_restore_fuzz_fault.py
137, test_t0236_restore_integration_restart.py 26,
test_t0237_restore_instrument.py 49. Author environment: Linux sandbox,
CPython 3.12.15 (the repo's declared toolchain), pytest 9.1.1, PyYAML
6.0.3, ruff 0.17.2, mypy 1.20.2, pytest-xdist. Build-side environments
are recorded in the per-link evidence files. The environments are
distinct; the runs are reported separately, not blended. `ruff check .`
at the review head: clean.

The restore red harness is permanent: it is kept green against
production, and the production binding switch is exactly its two lines
(proven by T0233).

## Coverage summary (per link)

- contract: data/contracts/restore.yaml is normative; the battery holds the
  contract-derived reference restore engine - verified restore of
  content-addressed backup bundles into a canonical graph state - and
  proves happy, boundary, malformed and rollback behavior against it.
  The backup receipt verifies through the LINKED backup machinery
  (imported, never restated) BEFORE any parse; the bundle parser is
  untrusted input behind the boundary (single evaluation per restore,
  frozen receipt fields, exact built-in-mapping output validation); the
  parsed state is validated record-by-record through the linked node
  machinery and its recomputed id must equal the receipt's state id; and
  the battery pins that a rejected restore leaves every input bit-
  identical. (The file itself proves contract consistency, not
  production behavior.)
- fixture: tests/fixtures/restore/cases.json - 23 cases: 3 happy, 3 boundary, 17
  malformed, 3 rollback. Pinned restore receipts were computed from the
  reference at authoring time, so any contract or derivation drift
  breaks the battery. Every malformed case is discriminating via its
  declared single-locus repair. Rollback cases prove a rejected restore
  leaves the input receipt bit-identical. Same DESIGN CAUTION:
  fixture/contract CONSISTENCY, not production behavior.
- red test: permanent standalone battery driving every fixture row, a closed set
  of totality probes and a pinned canonical-order probe through an
  engine class constructed with a bundle parser. happy/boundary: the
  pinned restore record exactly (the restored state compared order-
  sensitively), deterministic by value over fresh copies (never the same
  object, no shared container), exactly ONE parser call with the
  receipt's own bundle, and the receipt untouched by value AND object
  identity (exact snapshot: type, id, length and key order of every
  container, key identity, scalar type+value) with no container shared
  between the result and the input. malformed: the original receipt
  rejects with the pinned failure class and its mapped code, the receipt
  untouched, and the declared minimal repair is accepted WITH THE RECORD
  THE REFERENCE DERIVES for the repaired input. rollback: a rejection
  leaves the receipt untouched, then the valid follow-up - on the SAME
  engine instance when the parser is unchanged, a fresh one otherwise -
  returns the pinned record. verify-first: a receipt-level rejection
  never calls the parser. totality: hostile receipts, keys, values, dict
  subclasses and hostile parser behavior (raising any BaseException,
  non-mapping output, hostile state keys, records, record keys and
  values, cyclic/deep/huge values, parsers that mutate the caller's
  receipt mid-call or keep references to what they return) each reject
  with the pinned failure class or, for accept-probes, return the pinned
  record, receipt untouched; any other BaseException escaping is a
  failure. Closure standard: ordered per-section manifests, per-row
  semantic pins and closed per-tag checks over the original row data,
  whole-row ROW_DIGESTS keyed to the manifests, and the substitution-
  mutant kill test run with digests live AND neutralized.
- implement: production store/restore.py; the red battery runs against it with only
  its two binding lines switched, and this file proves the switch is
  exactly those two lines; production never imports the test package and
  links the production backup; its record validation agrees with the
  production WAL's; production reproduces every pinned fixture restore
  record.
- unit/property: seeded deterministic properties over production only (no tests.*
  helpers): receipts from the shipped store.backup over logs built with
  the shipped store.wal from records made by the shipped graph.node
  runtime; restores checked against an independent put/delete model,
  graph.diff, an independent state-id and restore-id derivation and an
  independent bundle serialization. Forged receipts are self-consistent
  (they pass the local backup verify) so every restore-side guard is
  reached on its own. Seeded, so a failure replays.
- fuzz/fault: seeded generators build backup receipts from an independent model of
  backup.yaml and restore.yaml (the bundle serialization and the backup-
  id and restore-id derivations restated, never taken from production)
  and damage them: receipt, container, bundle, parser and live-parser
  classes. Against production: no raw escape (only a typed RestoreError
  from the closed class set with its mapped code); the same outcome as
  the contract reference engine, and where independently known, that
  outcome; the receipt identical by value, exact type, key order and
  object identity at every level on every exit; a successful restore
  equal to the model and detached from the parser output; no poisoning;
  determinism. The mutation probe pins every MUTANTS entry red and every
  _EQUIVALENT_EDITS entry green, guarded by an identity mutant.
- integration/restart: the durable form is a JSON backup receipt (as store.backup emits it
  for a JSON-lines WAL log); a restart is a fresh interpreter
  (subprocess) that loads the receipt, builds a new RestoreEngine and
  restores. Every scenario checks the fresh process against the
  uninterrupted in-process run, an independent model of
  {wal,backup,restore}.yaml and, on the happy path, the contract
  reference. Coverage: happy (every prefix, a backup -> restore process
  pipeline, repeated restores, hash-seed determinism), boundary (empty
  backup, long log, a self-consistent receipt at the entry_count
  ceiling), malformed (receipts the linked backup verify rejects; self-
  consistent forged receipts whose content is wrong, so only restore's
  own checks catch them) and rollback (every rejection leaves the
  persisted receipt unchanged; a clean restore after a rejection, in the
  same process and after a restart, equals the model). The bundle parser
  is the only except-BaseException oracle boundary: every RestoreError
  and BackupError class is forged from it and must fail closed as
  divergent_parse, never as the forged object. One-edit production
  mutants are pinned RED on their own scenario (MUTANTS).
- instrument: RestoreTracer (store.restore_instrument) wraps RestoreEngine.restore.
  Proven: neutrality, diagnostics, totality and one-edit-mutant kills in
  the same four classes as the WAL battery. Scope note: the swallow-
  scope narrowing witness and the data-descriptor _trace getter probe
  added to the JOBS instrument batteries after this chain landed are NOT
  part of this battery; this is a scope statement, not a verdict.

## Known environmental note

tests/test_install_checks.py did not complete within a 110-second
bound in the author sandbox at the review head (killed by timeout; no
result recorded). At the previous review head
(0145449ee57ddeb956c940c35a74f008bd710ea1, the Jobs wave) the same
file completed with 7 failed, 9 passed from an environmental cause:
the install checks exec bare `ruff`/`mypy` from PATH, which this
sandbox class does not provide (documented in the landed T0058 and
T0292 packages). Environmental, unrelated to the chain. A
full-toolchain pass of the install checks is not certified in this
package.

## Author statement

This package was authored by the builder lead lane from origin/main at
the review head, with its own rerun of the full chain suite and its own
reading of every chain artifact (including the assertion bodies behind
every coverage claim above, not only the module docstrings). It reports
build-side results only as recorded in the per-link evidence files and
does not blend environments. The author holds no implementation role in
any link of this chain; this document asserts no authority: the task
closes under the independent review and CI gates.
