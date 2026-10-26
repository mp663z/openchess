# T0229 Store/backup/independent verify - verification package

Scope: the COMPLETE store backup chain (T0221-T0228) on the Python
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
| T0221 | Store/backup/contract | 257bd5f8bf0d31fd65fa235a03a1f0776ba9d5e6 |
| T0222 | Store/backup/fixture | e54c6265517abb3efe6855d2bedc9178e992cd6f |
| T0223 | Store/backup/red test | bf9062e82e5116b35cbc15efdd689cc32cd4f284 |
| T0224 | Store/backup/implement | 6e6163363b25f0392b2bdc23ef5db42084fedfaa |
| T0225 | Store/backup/unit/property | 8208e114747948a6bd8e22db948ffd92799488e1 |
| T0226 | Store/backup/fuzz/fault | b50023d9d96c11a685df45e94e259b0ff0ab0caf |
| T0227 | Store/backup/integration/restart | 24bcbae07cf02f1955541cbdb029af8f203d0b31 |
| T0228 | Store/backup/instrument | 9a33418aff75d368422272cb55489a523d2f1313 |

Every merge SHA was confirmed an ancestor of main at the review head by
the author (`git merge-base --is-ancestor` for each of the 8). Every
link was reviewed by independent verifiers before merge; the per-link
verdicts (exact reviewed head SHAs, scope statements, remediation
history, CI run links) are recorded in evidence/T0221.md through
evidence/T0228.md on main. This package submits the chain inventory and
reproducible artifacts only - it does not certify itself and asserts no
authority; the task closes under the independent review and CI gates,
not on this document.

## Content hashes at review head

- data/contracts/backup.yaml: 6adbfe37b7da3f14aec8d89ea9f67087bc3bdfc9a58e6fee132e62ce4db548f6
- store/backup.py: 78ac5afc991d00ac5bf95a0b695ae1046e2b6f7af11916c2e6558a032317fcf8
- store/backup_instrument.py: 750f897ff00330238366375dbc3673e5ab615e49cb6bd770acfd3fcc5975a5d0
- tests/fixtures/backup/cases.json: b3fde47eb8e174794884902024c7caaa5e894da27cbab8b9f7605213f74b9fa8
- main at package assembly: 205bf35dec6a60ed88cce124c985cbbc4ab155a3

## Full-chain suite result

Command: `python -m pytest tests/test_t0221_backup_contract.py tests/test_t0222_backup_fixture.py tests/test_t0223_backup_red.py tests/test_t0224_backup_runtime.py tests/test_t0225_backup_properties.py tests/test_t0226_backup_fuzz_fault.py tests/test_t0227_backup_integration_restart.py tests/test_t0228_backup_instrument.py -q`

Author's own rerun at the review head: 689 passed, 0 failed, in a single
pytest-xdist `-n 4` invocation of exactly the command's file set; the
instrument file additionally run serially (49 passed). Per file:
test_t0221_backup_contract.py 90, test_t0222_backup_fixture.py 32,
test_t0223_backup_red.py 170, test_t0224_backup_runtime.py 6,
test_t0225_backup_properties.py 254, test_t0226_backup_fuzz_fault.py 52,
test_t0227_backup_integration_restart.py 36,
test_t0228_backup_instrument.py 49. Author environment: Linux sandbox,
CPython 3.12.15 (the repo's declared toolchain), pytest 9.1.1, PyYAML
6.0.3, ruff 0.17.2, mypy 1.20.2, pytest-xdist. Build-side environments
are recorded in the per-link evidence files. The environments are
distinct; the runs are reported separately, not blended. `ruff check .`
at the review head: clean.

The backup red harness is permanent: it is kept green against
production, and the production binding switch touches only its bindings
block (proven by T0224).

## Coverage summary (per link)

- contract: data/contracts/backup.yaml is normative; the battery holds the
  contract-derived reference backup engine - content-addressed backup
  receipts over a WAL-logged canonical graph state - and proves happy,
  boundary, malformed and rollback behavior against it. The source log
  validates and replays through the LINKED WAL machinery (imported,
  never restated); the bundle serializer is untrusted input behind the
  boundary (single evaluation per backup, frozen log and state
  snapshots, exact built-in-str output validation); verification is
  LOCAL (no oracle): the recomputed backup id must equal the stored one,
  with pinned head/count consistency; and the battery pins that a
  rejected backup or verify leaves every input bit-identical. (The file
  itself proves contract consistency, not production behavior.)
- fixture: tests/fixtures/backup/cases.json - 22 cases: 3 happy, 3 boundary, 16
  malformed, 3 rollback. Pinned receipts were computed from the
  reference at authoring time, so any contract or derivation drift
  breaks the battery. Every malformed case is discriminating via its
  declared single-locus repair. Rollback cases prove a rejected
  backup/verify leaves every input bit-identical. Same DESIGN CAUTION as
  the WAL fixture: fixture/contract CONSISTENCY, not production
  behavior.
- red test: permanent standalone battery, green against the T0221 reference; the
  production binding switch is confined to its bindings block.
  happy/boundary: the pinned receipt with exactly one serializer call,
  the log left identical by value and object id; the receipt verifies
  locally with zero serializer calls; verify returns a fresh object and
  leaves its input identical; a second run is byte-identical. malformed:
  the original input rejects with the pinned failure class and code,
  inputs identical by value and object id (identity-and-value snapshot);
  the declared minimal repair is accepted - for backup rows the receipt
  is valid, its entry_count equals the repaired log length and it
  verifies locally, for verify rows verify returns an equal-but-fresh
  receipt object; when the repair keeps the oracle, the SAME rejecting
  instance runs the follow-up first. rollback: the rejected call leaves
  the objects handed to it identical by value and object id, then the
  pinned follow-up commits (same rejecting instance first when the
  oracle is unchanged). same-instance recovery: after every rejection
  the same engine still verifies a known-good receipt into a fresh equal
  object, still rejects every FORGED_MANIFEST receipt plus one cross-
  receipt splice with their pinned classes and codes, and, with an
  honest serializer, backs up every happy and boundary row log, the
  rollback two-put log and a pinned six-entry log (longer than any
  fixture log) in sequence, bracketed by the empty log, each to its
  pinned receipt - closed over the fixture, never a hand-picked sample.
  Also pinned: self-consistent forgeries reject with pinned classes; the
  integrity splice (every field of every valid receipt swapped in from a
  different valid receipt) fails verify; the serializer's state argument
  is a fresh copy and the receipt shares no container with the log.
  Totality (in-file builders, production bindings only): hostile logs
  and receipts (HK/SK keys, subclasses, self-references, 10**5-level
  nesting, a 5000-digit sequence, entry counts past the int64 ceiling)
  and hostile serializers (every BaseException kind, non-str, lone low
  surrogate, mid-call mutators) - each rejects with a pinned typed
  BackupError class and code, never a raw escape, inputs identical by
  value and object id, or, for mid-call mutators, returns the honest
  receipt with inputs restored; the ceiling is pinned from below - a
  self-consistent receipt at entry_count 2**63 - 1 must verify to a
  fresh equal object. Closure standard: ordered manifests, per-row
  semantic pins, closed per-tag checks, whole-row ROW_DIGESTS,
  substitution-mutant kill test with and without digests, black-box
  engine mutants with non-vacuous witnesses.
- implement: production store/backup.py; the red battery runs against it with only
  the production-bindings block switched, and this file proves the
  switch touches only that block; production never imports the test
  package and links the production WAL; the public derive_backup_id
  reproduces every pinned fixture receipt.
- unit/property: seeded deterministic properties over production only (no tests.*
  helpers): source logs built with the shipped store.wal from records
  made by the shipped graph.node runtime; receipts checked against the
  WAL replay, graph.diff, an independent bundle serialization and an
  independent backup-id derivation. Seeded, so a failure replays.
- fuzz/fault: seeded generators drive production; every input log is built from an
  independent model of wal.yaml and backup.yaml (entry-id derivation,
  put/delete fold, bundle serialization and backup-id derivation
  restated, never taken from production); the contract reference engine
  is the differential oracle. The bundle serializer is the module's only
  except-BaseException oracle boundary: it forges a BackupError of EVERY
  class and a WalError (the linked module's error) of EVERY class; each
  must fail closed as a FRESH divergent_snapshot, never as the forged
  object; pass-through mutants per class are pinned in FORGE_TARGETS.
- integration/restart: the durable forms are a JSON-lines WAL log file and a JSON receipt; a
  restart is a fresh interpreter (subprocess) that loads those bytes,
  builds a new BackupEngine (and, for continued logs, a new linked
  WalEngine) and backs up, verifies or appends. Every scenario checks
  the fresh process against the uninterrupted in-process run, an
  independent model of wal.yaml + backup.yaml and the contract
  reference. Coverage: happy (every prefix, process chains, persisted
  receipts, hash-seed determinism), boundary (empty log, long log, torn
  tail, the entry_count domain from both sides), malformed (tampered
  durable logs, tampered and self-consistently forged persisted
  receipts, non-list sources) and rollback (every rejection leaves the
  durable bytes and the in-memory log unchanged; a clean backup after a
  rejection, in the same process and after a restart, equals the model).
  The bundle serializer is the only except-BaseException oracle
  boundary: every BackupError and WalError class is forged from it and
  must fail closed as divergent_snapshot, never as the forged object.
  One-edit production mutants are pinned RED on their own scenario
  (MUTANTS).
- instrument: BackupTracer (store.backup_instrument) wraps BackupEngine.backup and
  BackupEngine.verify. Proven: neutrality (a traced call returns what
  the bare call returns or raises the same typed class - the same
  exception object - and never changes an argument); diagnostics (one
  record per call with operation, argument snapshot, outcome and
  arguments_unchanged on failure, in seq order; append-only, isolated
  views, deterministic JSONL); totality (hostile error attributes, a
  faulting trace container, a hostile log and pathological depth never
  change the wrapped result); one-edit mutants of the instrument are
  each red on their own check. Scope note: the swallow-scope narrowing
  witness and the data-descriptor _trace getter probe added to the JOBS
  instrument batteries after this chain landed are NOT part of this
  battery; this is a scope statement, not a verdict.

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
