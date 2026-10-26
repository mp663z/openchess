# T0256 Store/export/independent verify - verification package

Scope: the COMPLETE store export chain (T0248-T0255) on the Python
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
| T0248 | Store/export/contract | b35e89527bbceea91f81132f33627293dc778778 |
| T0249 | Store/export/fixture | cab5352e67560e87ab235e0c8f5d90ca5c292dbb |
| T0250 | Store/export/red test | 7ad8b4d5c39448f1932bb5df668f50f726d09ab6 |
| T0251 | Store/export/implement | 4ec9696663d1d5e2727ee38ffaba7aa3a1c0115f |
| T0252 | Store/export/unit/property | 87ffd32f73b90e37c432d68300ea71f1eb116e12 |
| T0253 | Store/export/fuzz/fault | a1e5d7951947730089c64c625d22917a27758ea9 |
| T0254 | Store/export/integration/restart | 6875ef3147d330c44c22a9e2d0c9f50053c2f6b8 |
| T0255 | Store/export/instrument | 06be204a82e39d9abe445b2089f9eac6b5229ae7 |

Every merge SHA was confirmed an ancestor of main at the review head by
the author (`git merge-base --is-ancestor` for each of the 8). Every
link was reviewed by independent verifiers before merge; the per-link
verdicts (exact reviewed head SHAs, scope statements, remediation
history, CI run links) are recorded in evidence/T0248.md through
evidence/T0255.md on main. This package submits the chain inventory and
reproducible artifacts only - it does not certify itself and asserts no
authority; the task closes under the independent review and CI gates,
not on this document.

## Content hashes at review head

- data/contracts/export.yaml: 253b9627e71f9a972098e43e87a6931013bb1dd6e81d4bb8b609db89178a09d3
- store/export.py: 1108ebfdd3168a4b82fbf45f804d938cd9f30bb20decf4918188ca7a8c09d855
- store/export_instrument.py: fd5bda484b7b055a350ffaba8e5a052c484171b58592315e567c5101d52dd4d6
- tests/fixtures/export/cases.json: 55d2cd98132a71bb66b9690dced09d16ec61e8face7b32064acf7a0387166656
- main at package assembly: 205bf35dec6a60ed88cce124c985cbbc4ab155a3

## Full-chain suite result

Command: `python -m pytest tests/test_t0248_export_contract.py tests/test_t0249_export_fixture.py tests/test_t0250_export_red.py tests/test_t0251_export_runtime.py tests/test_t0252_export_properties.py tests/test_t0253_export_fuzz_fault.py tests/test_t0254_export_integration_restart.py tests/test_t0255_export_instrument.py -q`

Author's own rerun at the review head: 689 passed, 0 failed, in a single
pytest-xdist `-n 4` invocation of exactly the command's file set; the
instrument file additionally run serially (50 passed). Per file:
test_t0248_export_contract.py 69, test_t0249_export_fixture.py 67,
test_t0250_export_red.py 52, test_t0251_export_runtime.py 6,
test_t0252_export_properties.py 270, test_t0253_export_fuzz_fault.py
145, test_t0254_export_integration_restart.py 30,
test_t0255_export_instrument.py 50. Author environment: Linux sandbox,
CPython 3.12.15 (the repo's declared toolchain), pytest 9.1.1, PyYAML
6.0.3, ruff 0.17.2, mypy 1.20.2, pytest-xdist. Build-side environments
are recorded in the per-link evidence files. The environments are
distinct; the runs are reported separately, not blended. `ruff check .`
at the review head: clean.

The export red harness is permanent: it is kept green against
production, and the production binding switch is exactly its two lines
(proven by T0251).

## Coverage summary (per link)

- contract: data/contracts/export.yaml is normative; the battery holds the
  contract-derived reference export engine - a canonical, read-only
  export of the current state of a WAL-logged graph store - and proves
  happy, boundary, malformed and rollback behavior against it. The
  source log validates and replays through the LINKED WAL machinery
  (imported, never restated); the state id is the linked migration state
  digest; the document exporter is untrusted input behind the boundary
  (single evaluation per export, detached argument copy, frozen log,
  state and request, exact built-in-str UTF-8-encodable output bound
  byte-for-byte to the LOCAL canonical rendering). Export is read-only:
  the battery pins that every export, accepted or rejected, leaves the
  log and the request bit-identical. (The file itself proves contract
  consistency, not production behavior.)
- fixture: tests/fixtures/export/cases.json - 23 cases: 2 happy, 3 boundary, 15
  malformed, 3 rollback. Pinned export receipts were computed from the
  reference at authoring time, so any contract or derivation drift
  breaks the battery. Every malformed case is discriminating via its
  declared single-locus repair (log, request or exporter locus).
  Rollback cases prove a rejected export leaves the exact supplied log
  and request bit-identical before the valid follow-up export succeeds
  with the pinned receipt. Same DESIGN CAUTION: fixture/contract
  CONSISTENCY, not production behavior.
- red test: permanent standalone battery driving every fixture row, a closed set
  of totality probes and a pinned identity-order probe through an engine
  class constructed with a document exporter. happy/boundary: the pinned
  receipt exactly (exact field types), the log and request untouched by
  value AND identity (export is read-only: every container and key
  _snap-identical), determinism by value over fresh copies on the SAME
  instance (never the same object), and exactly ONE exporter call with a
  DETACHED by-value copy of the replayed state and the exact format.
  malformed: the original input rejects with the pinned failure class
  and its mapped code, log and request untouched, the exporter never
  called unless the class is divergent_export, and the declared single-
  locus repair is accepted WITH THE RECEIPT THE REFERENCE DERIVES for
  the repaired input (a fresh engine when the repair changes the
  exporter). rollback: the hostile exporter bound to the caller's live
  inputs is rejected with the pinned class, log and request RESTORED by
  value and identity, then the valid follow-up on the SAME objects
  returns the pinned receipt. totality: hostile requests, request
  keys/values, hostile logs, entries (first AND last), entry
  keys/values, payloads and records, dict/list subclasses,
  cyclic/deep/huge values and hostile exporter behavior (raising any
  BaseException, non-str/str-subclass/unencodable/divergent output,
  exporters that mutate the caller's log, request or their own argument
  at every depth mid-call) each reject with the pinned failure class or,
  for accept-probes, return the pinned receipt with the inputs untouched
  - and, on the SAME instance, still reject a bad request typed and
  still accept a clean export; any other BaseException escaping is a
  failure. Closure standard as in the restore battery (manifests,
  semantic pins, closed tags, ROW_DIGESTS, substitution-mutant kill test
  with digests live and neutralized).
- implement: production store/export.py; the red battery runs against it with only
  its two binding lines switched, and this file proves the switch is
  exactly those two lines; production never imports the test package and
  links the production WAL through its public snapshot/restore helpers;
  production reproduces every pinned fixture receipt with the honest
  exporter.
- unit/property: seeded deterministic properties over production only (no tests.*
  helpers): source logs built with the shipped store.wal from records
  made by the shipped graph.node runtime; receipts checked against the
  WAL replay, graph.diff, an independent put/delete model, an
  independent canonical rendering and an independent export-id
  derivation. Seeded, so a failure replays.
- fuzz/fault: seeded generators build WAL logs and export requests from an
  independent model of wal.yaml and export.yaml (the entry-id, state-id,
  canonical document and export-id derivations restated, never taken
  from production) and damage them: request, container, source, exporter
  and live-exporter classes. Against production: no raw escape (only a
  typed ExportError from the closed class set with its mapped code,
  never the forged object); the same outcome as the contract reference
  engine, and where independently known, that outcome; log and request
  identical by value, exact type, key order and object identity at every
  level on every exit; no exporter call on a request or source
  rejection; on success exactly one exporter call on a detached copy
  equal to the model state, and the model receipt; no poisoning;
  determinism. The mutation probe pins every MUTANTS entry red and every
  _EQUIVALENT_EDITS entry green, guarded by an identity mutant.
- integration/restart: the durable form is a JSON-lines WAL log file; a restart is a fresh
  interpreter (subprocess) that loads those bytes, builds a new
  ExportEngine (and, to continue or truncate the log, a fresh linked
  WalEngine / RollbackEngine) and exports; the child reports the durable
  bytes of the log afterwards. Every scenario checks the fresh process
  against the uninterrupted in-process run, an independent model of
  {wal,export}.yaml and, on the happy path, the contract reference.
  Coverage: happy (an export of every log prefix, export -> restart ->
  continue -> restart -> truncate -> export chains, hash-seed
  determinism), boundary (the empty log, a log whose state is empty
  after deletes, repeated exports), malformed (requests, formats,
  containers, tampered durable logs, precedence between them) and
  rollback (export is read-only: every export, accepted or rejected,
  leaves the durable bytes and the request unchanged; a clean export
  after a rejection, in the same process and after a restart, equals the
  model). The document exporter is the only except-BaseException oracle
  boundary: every ExportError and WalError class, and
  UnicodeEncodeError, is forged from it and must fail closed as
  divergent_export, never as the forged object. One-edit production
  mutants are pinned RED on their own scenario (MUTANTS).
- instrument: ExportTracer (store.export_instrument) wraps ExportEngine.export.
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
does not blend environments. The author attests it holds no
implementation role in any link of this chain (see the provenance
disclosure for its basis); this document asserts no authority: the task
closes under the independent review and CI gates.
