# T0220 Store/WAL/independent verify - verification package

Scope: the COMPLETE store WAL chain (T0212-T0219) on the Python
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
| T0212 | Store/WAL/contract | 0783256c788d021c944e2a7d2bab800c27984323 |
| T0213 | Store/WAL/fixture | 21e7693898828b3644c366bb8e90eb5a1ee8bb3d |
| T0214 | Store/WAL/red test | 18adad556f123dbbb530e509be26b8cf34b0d43a |
| T0215 | Store/WAL/implement | 77cbe10de6612e602c3efd6e489ce600f583c78b |
| T0216 | Store/WAL/unit/property | c4b83a95b3e9729dc80a694bbcf4c765a96371b4 |
| T0217 | Store/WAL/fuzz/fault | 6dd74481be996cdbb9d8b1d6101bf7fba554b5be |
| T0218 | Store/WAL/integration/restart | 180bc4d4586d986200ae43c66b05fe14a4a1ccf5 |
| T0219 | Store/WAL/instrument | e704dba853e3c589d2cd7e18ff1e818974909a7e |

Every merge SHA was confirmed an ancestor of main at the review head by
the author (`git merge-base --is-ancestor` for each of the 8). Every
link was reviewed by independent verifiers before merge; the per-link
verdicts (exact reviewed head SHAs, scope statements, remediation
history, CI run links) are recorded in evidence/T0212.md through
evidence/T0219.md on main. This package submits the chain inventory and
reproducible artifacts only - it does not certify itself and asserts no
authority; the task closes under the independent review and CI gates,
not on this document.

## Content hashes at review head

- data/contracts/wal.yaml: 79a07e8da9c244aab8cf71f5a46a9f64b28f365dbde3987771478e704182facd
- store/wal.py: 98bcd165a463943e4558e88e2ca150cdfd2940205c5b987a4201f90f6e6a8faa
- store/wal_instrument.py: c32274c4784eaaffb9d4053bc64685efea5a2bd1ade339c1dc9fb0633b06c879
- tests/fixtures/wal/cases.json: af17522d366425740cae0527c94e33bf008190d0d85b08d7b849fe727ff0b663
- main at package assembly: 205bf35dec6a60ed88cce124c985cbbc4ab155a3

## Full-chain suite result

Command: `python -m pytest tests/test_t0212_wal_contract.py tests/test_t0213_wal_fixture.py tests/test_t0214_wal_red.py tests/test_t0215_wal_runtime.py tests/test_t0216_wal_properties.py tests/test_t0217_wal_fuzz_fault.py tests/test_t0218_wal_restart.py tests/test_t0219_wal_instrument.py -q`

Author's own rerun at the review head: 962 passed, 0 failed, in a single
pytest-xdist `-n 4` invocation of exactly the command's file set; the
instrument file additionally run serially (47 passed). Per file:
test_t0212_wal_contract.py 91, test_t0213_wal_fixture.py 34,
test_t0214_wal_red.py 127, test_t0215_wal_runtime.py 10,
test_t0216_wal_properties.py 453, test_t0217_wal_fuzz_fault.py 160,
test_t0218_wal_restart.py 40, test_t0219_wal_instrument.py 47. Author
environment: Linux sandbox, CPython 3.12.15 (the repo's declared
toolchain), pytest 9.1.1, PyYAML 6.0.3, ruff 0.17.2, mypy 1.20.2,
pytest-xdist. Build-side environments are recorded in the per-link
evidence files. The environments are distinct; the runs are reported
separately, not blended. `ruff check .` at the review head: clean.

The WAL red harness is permanent: it is kept green against production,
and the production binding switch is exactly its one import line (proven
by T0215).

## Coverage summary (per link)

- contract: data/contracts/wal.yaml is normative; the battery holds the contract-
  derived reference WAL - a single write-ahead log over content-
  addressed graph-state mutations (put/delete of exact node records) -
  and proves happy, boundary, malformed and rollback behavior against
  it. Entry payloads validate through the LINKED transposition-node
  machinery (imported, never restated); the payload canonicalizer is
  untrusted input behind the boundary (single evaluation per entry per
  operation, frozen log and request snapshots, exact built-in-str output
  validation); append is atomic with rollback, and the battery pins via
  input-preservation snapshots that a rejected append or replay leaves
  every input bit-identical. (The file itself proves contract
  consistency, not production behavior.)
- fixture: tests/fixtures/wal/cases.json - 24 cases: 3 happy, 3 boundary, 16
  malformed, 2 rollback. Pinned receipts and replay results were
  computed from the reference at authoring time, so any contract or
  derivation drift breaks the battery. Every malformed case is
  discriminating via its declared single-locus repair. Rollback cases
  prove a rejected append/replay leaves log and request byte-identical.
  The battery carries its own DESIGN CAUTION: the reference is derived
  from the same contract document, so the fixture proves
  fixture/contract CONSISTENCY, not production behavior.
- red test: permanent standalone battery, green against the T0212 reference over
  the fixture vectors; the later production binding switch is confined
  to its "production bindings" block. happy/boundary: every append
  receipt and the final replay equal the pins, a second run is byte-
  identical, inputs never mutate. malformed: the original input rejects
  with the pinned failure class and code, leaving log and request
  unchanged by value AND by deep object identity (value equality plus
  the captured id set); the declarative single-locus repair (one closed
  form) is then accepted - for append the returned entry's field set and
  sequence are pinned and the log re-validates, for replay the applied
  count and head are pinned. rollback: the rejected call leaves the very
  objects handed to it bit- and reference-identical, then the pinned
  follow-up commits. Also pinned: every corrupt replay-row log also
  rejects a valid append with the row's class (append validates the
  whole log); the committed entry never aliases the request's payload;
  the receipt is never the log's own entry object. Totality (54 in-file
  probes, production bindings only): hostile HK/SK keys, int-subclass
  values, dict/list subclasses, self-referencing containers, 10**5-level
  nesting, a 5000-digit sequence, hostile canonicalizers (every
  BaseException kind, hash-raising str, non-str) and canonicalizers
  mutating or clearing the live request/log mid-call - each rejects with
  a pinned typed WalError class and code, never a raw escape, inputs
  identical by value and object id, or, for the mid-call mutators,
  returns the honest receipt with the request restored. Closure: ordered
  manifests, per-row semantic pins over the original data, closed per-
  tag checks (an unknown tag raises), a whole-row sha256 ROW_DIGESTS
  table keyed to the manifests, a substitution-mutant kill test run with
  and without digests, and black-box engine mutants each with a non-
  vacuous witness.
- implement: production store/wal.py; the red battery runs against it with only the
  production-bindings import switched, and this file proves the switch
  is exactly that one line; production never imports the test package;
  the memoized linked derivation stays detached and exact.
- unit/property: seeded deterministic properties over production only (no tests.*
  helpers): op sequences generated from records built by the shipped
  graph.node runtime, checked against an independent model fold, the
  graph.diff state id and an independent entry-id derivation. Seeded, so
  a failure replays.
- fuzz/fault: seeded generators build logs from an independent model of wal.yaml
  (the entry-id derivation is restated, never taken from production) and
  damage them: shape, chain, request, container, canonicalizer fault,
  and a live canonicalizer editing the caller's log/request. Against
  production: no raw escape (only a typed WalError from the closed class
  set with its mapped code); the same outcome as the contract reference
  engine, and where independently known, that outcome; atomicity (a
  rejected call and every replay leave log and request identical by
  value, key order and object identity at every level; a successful
  append adds exactly one detached entry); no poisoning (a fresh engine
  still replays a clean log to the model state after the rejection);
  determinism. The mutation probe pins every MUTANTS entry red and every
  _EQUIVALENT_EDITS entry green, guarded by an identity mutant that must
  pass.
- integration/restart: the durable form is a JSON-lines log file, one entry per line; a
  restart is a fresh interpreter (subprocess) that loads those bytes,
  builds a new WalEngine and replays or continues the log. Every
  scenario checks the fresh process against the uninterrupted in-process
  run, an independent model of wal.yaml (fold, entry-id derivation,
  graph.diff state id) and the contract reference as the differential
  oracle. Rejections are typed WalErrors from the closed set, leave the
  in-memory log intact and leave the durable bytes unchanged. The
  payload canonicalizer is the module's only except-BaseException oracle
  boundary: every WalError class is forged from it (on replay and on
  append, first and last call) and must fail closed as a FRESH
  divergent_canonicalization; pass-through mutants per class are pinned
  in MUTANT_TARGETS.
- instrument: WalTracer (store.wal_instrument) wraps WalEngine.append and
  WalEngine.replay. Proven: neutrality - over a seeded trajectory and
  every failure class, a traced call returns what the bare call returns
  or raises the same typed class (the same exception object) and never
  changes an argument beyond what the bare call itself changes;
  diagnostics - one record per call with operation, argument snapshot,
  outcome (accept with summary / reject with failure class and mapped
  code / crash with the error type) and arguments_unchanged on failure,
  in seq order, append-only, isolated views, deterministic JSONL;
  totality - hostile error attributes, a faulting trace container and
  pathological depth never change the wrapped result, and a hostile log
  or request records an opaque marker instead of dispatching; one-edit
  mutants of the instrument are each red on their own check. Scope note:
  the swallow-scope narrowing witness (except BaseException narrowed to
  except Exception) and the data-descriptor _trace getter probe that
  were added to the JOBS instrument batteries after this chain landed
  are NOT part of this battery; this is a scope statement, not a
  verdict.

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
