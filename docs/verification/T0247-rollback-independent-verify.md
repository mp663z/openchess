# T0247 Store/rollback/independent verify - verification package

Scope: the COMPLETE store rollback chain (T0239-T0246) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
builder lead lane. PROVENANCE DISCLOSURE: the author ATTESTS that it
implemented no link of this chain, on the basis of its own work history
(branch pushes, commit records and task transcripts searched for any
contribution to these links, none found). The commit authorship
metadata on all eight links is the shared "Instinct Agent
<agent@instinct.com>" identity, which does NOT by itself prove person-
or lane-independence; the attestation and history search above are the
basis of this disclosure, not the git identity. This package asserts
no authority.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0239 | Store/rollback/contract | 3d60e5aeea0021c8c2d1617f530ee580840a2a5e |
| T0240 | Store/rollback/fixture | d26109b2c8d14a870f0fa6d18a1a8f1f868fb05e |
| T0241 | Store/rollback/red test | 20afa72cf0bd4a5eb0ae3ef1e426e6687e31a4bf |
| T0242 | Store/rollback/implement | ca915fcae934b9e72baddd622841617bdf995839 |
| T0243 | Store/rollback/unit/property | df9c205434b574232a2ae1fa32d2f3a05821c5b6 |
| T0244 | Store/rollback/fuzz/fault | 8244ad26c93051bcf9706a4c19cda9ef04eebfb1 |
| T0245 | Store/rollback/integration/restart | 5dada925cfd0df37e2f17fcf2fbffbfec4fcec2a |
| T0246 | Store/rollback/instrument | 62aa6badc26d3639841efd4a61c619c7643ba7fc |

Every merge SHA was confirmed an ancestor of main at the review head by
the author (`git merge-base --is-ancestor` for each of the 8). Every
link was reviewed by independent verifiers before merge; the per-link
verdicts (exact reviewed head SHAs, scope statements, remediation
history, CI run links) are recorded in evidence/T0239.md through
evidence/T0246.md on main. This package submits the chain inventory and
reproducible artifacts only - it does not certify itself and asserts no
authority; the task closes under the independent review and CI gates,
not on this document.

## Content hashes at review head

- data/contracts/rollback.yaml: 39e6b394d6aaff7747a5d6a0a61fe2a1b83c7b7956f5b1c0f9c65061a16c210d
- store/rollback.py: 4bd7988336e14c556ed809beb8581bd15135f29bcc17e31ad3ad5753c671b523
- store/rollback_instrument.py: bee57b9a1696effffebd990da204be6d77f6ada51db533d5b6bc5fc1d703c43c
- tests/fixtures/rollback/cases.json: 3a1b9f420c07c878f7b932daf0762409be6427634181e7f6657192feb78ad400
- main at package assembly: 205bf35dec6a60ed88cce124c985cbbc4ab155a3

## Full-chain suite result

Command: `python -m pytest tests/test_t0239_rollback_contract.py tests/test_t0240_rollback_fixture.py tests/test_t0241_rollback_red.py tests/test_t0242_rollback_runtime.py tests/test_t0243_rollback_properties.py tests/test_t0244_rollback_fuzz_fault.py tests/test_t0245_rollback_integration_restart.py tests/test_t0246_rollback_instrument.py -q`

Author's own rerun at the review head: 619 passed, 0 failed, in a single
pytest-xdist `-n 4` invocation of exactly the command's file set; the
instrument file additionally run serially (50 passed). Per file:
test_t0239_rollback_contract.py 60, test_t0240_rollback_fixture.py 31,
test_t0241_rollback_red.py 49, test_t0242_rollback_runtime.py 6,
test_t0243_rollback_properties.py 271, test_t0244_rollback_fuzz_fault.py
125, test_t0245_rollback_integration_restart.py 27,
test_t0246_rollback_instrument.py 50. Author environment: Linux sandbox,
CPython 3.12.15 (the repo's declared toolchain), pytest 9.1.1, PyYAML
6.0.3, ruff 0.17.2, mypy 1.20.2, pytest-xdist. Build-side environments
are recorded in the per-link evidence files. The environments are
distinct; the runs are reported separately, not blended. `ruff check .`
at the review head: clean.

The rollback red harness is permanent: it is kept green against
production, and the production binding switch is exactly its two lines
(proven by T0242).

## Coverage summary (per link)

- contract: data/contracts/rollback.yaml is normative; the battery holds the
  contract-derived reference rollback engine - verified truncation
  rollback of a WAL-logged store to a prior position - and proves happy,
  boundary, malformed and rollback behavior against it. The source log
  validates through the LINKED WAL machinery (imported, never restated);
  the tail archiver is untrusted input behind the boundary (single
  evaluation per rollback, detached argument copy, frozen log and
  request, exact built-in-str pinned-grammar UTF-8-encodable output);
  the commit removes exactly the tail, only after full validation and
  archival; and the battery pins that a rejected rollback leaves every
  input bit-identical. (The file itself proves contract consistency, not
  production behavior.)
- fixture: tests/fixtures/rollback/cases.json - 24 cases: 3 happy, 3 boundary, 15
  malformed, 3 rollback. Pinned rollback receipts were computed from the
  reference at authoring time, so any contract or derivation drift
  breaks the battery. Every malformed case is discriminating via its
  declared single-locus repair. Rollback cases prove a rejected rollback
  leaves the exact supplied log and request bit-identical before the
  valid follow-up commits. Same DESIGN CAUTION: fixture/contract
  CONSISTENCY, not production behavior.
- red test: permanent standalone battery driving every fixture row, a closed set
  of totality probes and a pinned tail-order probe through an engine
  class constructed with a tail archiver. happy/boundary: the pinned
  receipt exactly (exact field types), the log truncated IN PLACE to
  exactly the surviving prefix (same list object, same prefix entry
  objects, each _snap-identical), the request untouched by value AND
  identity, deterministic by value over fresh copies (never the same
  object), and exactly ONE archiver call with a DETACHED copy of the
  exact tail, in order. malformed: the original input rejects with the
  pinned failure class and its mapped code, log and request untouched,
  the archiver never called unless the class is divergent_archive, and
  the declared minimal repair is accepted WITH THE RECEIPT THE REFERENCE
  DERIVES for the repaired input. rollback: a rejection leaves log and
  request untouched, then the valid follow-up - on the SAME engine
  instance when the archiver is unchanged - returns the pinned receipt
  and truncates exactly the tail. totality: hostile requests, request
  keys/values, hostile logs, entries, entry keys/values, payloads,
  records, dict/list subclasses, cyclic/deep/huge values and hostile
  archiver behavior (raising any BaseException, non-str/str-
  subclass/bad-grammar/unbound/unencodable output, archivers that mutate
  the caller's log, request or their own argument mid-call, or keep what
  they are handed) each reject with the pinned failure class or, for
  accept-probes, return the pinned receipt with exactly the tail
  removed; any other BaseException escaping is a failure. Closure
  standard as in the restore battery (manifests, semantic pins, closed
  tags, ROW_DIGESTS, substitution-mutant kill test with digests live and
  neutralized).
- implement: production store/rollback.py; the red battery runs against it with
  only its two binding lines switched, and this file proves the switch
  is exactly those two lines; production never imports the test package
  and links the production WAL through its public snapshot/restore
  helpers; production reproduces every pinned fixture receipt with the
  honest archiver.
- unit/property: seeded deterministic properties over production only (no tests.*
  helpers): source logs built with the shipped store.wal from records
  made by the shipped graph.node runtime; receipts checked against the
  WAL chain, an independent archive-token derivation and an independent
  rollback-id derivation, and committed logs against the original prefix
  by value and object identity. Seeded, so a failure replays.
- fuzz/fault: seeded generators build WAL logs and rollback requests from an
  independent model of wal.yaml and rollback.yaml (the entry-id,
  archive-token and rollback-id derivations restated, never taken from
  production) and damage them: request, container, source, archiver and
  live-archiver classes. Against production: no raw escape (only a typed
  RollbackError from the closed class set with its mapped code, never
  the forged object); the same outcome as the contract reference engine,
  and where independently known, that outcome; a rejected call leaves
  log and request identical by value, exact type, key order and object
  identity at every level; a successful rollback truncates exactly the
  tail, keeps every surviving entry by identity, leaves the request
  untouched and equals the model receipt; one archiver call on a
  detached copy; no poisoning; determinism. The mutation probe pins
  every MUTANTS entry red and every _EQUIVALENT_EDITS entry green,
  guarded by an identity mutant.
- integration/restart: the durable form is a JSON-lines WAL log file; a restart is a fresh
  interpreter (subprocess) that loads those bytes, builds a new
  RollbackEngine (and a fresh linked WalEngine to continue the log) and
  rolls back, appends or replays; the child reports the durable bytes of
  the log afterwards. Every scenario checks the fresh process against
  the uninterrupted in-process run, an independent model of
  {wal,rollback}.yaml and, on the happy path, the contract reference.
  Coverage: happy (every (length, target) pair, rollback -> restart ->
  continue -> rollback chains, hash-seed determinism), boundary (target
  0, target == len, the empty log, target just outside either end),
  malformed (requests, containers, tampered durable logs, precedence
  between them) and rollback (every rejection leaves the durable bytes
  and the request unchanged; a clean rollback after a rejection, in the
  same process and after a restart, equals the model). The tail archiver
  is the only except-BaseException oracle boundary: every RollbackError
  and WalError class is forged from it and must fail closed as
  divergent_archive, never as the forged object. One-edit production
  mutants are pinned RED on their own scenario (MUTANTS).
- instrument: RollbackTracer (store.rollback_instrument) wraps
  RollbackEngine.rollback. Proven: neutrality, diagnostics, totality and
  one-edit-mutant kills in the same four classes as the WAL battery.
  Scope note: the swallow-scope narrowing witness and the data-
  descriptor _trace getter probe added to the JOBS instrument batteries
  after this chain landed are NOT part of this battery; this is a scope
  statement, not a verdict.

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
does not blend environments. The author attests it holds no
implementation role in any link of this chain (see the provenance
disclosure for its basis); this document asserts no authority: the task
closes under the independent review and CI gates.
