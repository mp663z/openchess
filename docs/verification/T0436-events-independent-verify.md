# T0436 Contracts/events/independent verify - verification package

Scope: the control-plane operation-metadata event chain (T0428-T0435) on
the Python local outbox plus the opt-in consumer logical-call
instrumentation, submitted for independent adversarial review. This
package claims chain-level coherence only. It does NOT claim hosted
server emission, OAuth or hosted sync, server-side coverage of all
control-plane operations, Rust product parity, or whole-product
completion.

## Chain inventory (merge order)

Landed `done_sha` values read from live `tasks/dag.json` at package
assembly (main f748be5002a8dd556f55b39dc4a6e75fbc3dffa6):

| Task | Title | done_sha |
|---|---|---|
| T0428 | Contracts/events/contract | be2f0b3c3ab606d97ae49286218a5e21dd739d80 |
| T0429 | Contracts/events/fixture | 1d820311576f8733d7b4b3f7ce66d3c9fec59125 |
| T0430 | Contracts/events/red test | d356c4234fa185859b296a685cc7a4be21ba8222 |
| T0431 | Contracts/events/implement | 7ae94ed3bb85ec27b565fc52619a3ebd8c9ba82d |
| T0432 | Contracts/events/unit/property | 7362552873b174bfdc19455f09251d8ae278ad33 |
| T0433 | Contracts/events/fuzz/fault | 81eb0bf4d8896f67295f6efcc71313324c5ad556 |
| T0434 | Contracts/events/integration/restart | 966a394352dc48e45ec6fce372be83ec8cd47c10 |
| T0435 | Contracts/events/instrument | 911235ad43b7c6e5f66c97f6cee15930007aa748 |

Per-link build records and verifier history live in
evidence/T0428.md through evidence/T0435.md on main.

Separate post-T0435 contract-owner amendment, NOT a chain link: PR #365,
landed as f748be5002a8dd556f55b39dc4a6e75fbc3dffa6 ("Clarify opt-in
local event emission contract status"), moved the events.yaml
`role.status` string from `contract-only-no-shipped-emission-claimed` to
`opt-in-local-consumer-operation-emission-shipped-no-hosted-send`, with
the exact-pinned lint string and the T0428 expectation moved alongside.
No envelope, ordering, failure or catalog semantics changed.

This package submits the chain inventory and reproducible artifacts
only - it does not certify itself, asserts no authority, and does not
claim independent PASS or board completion. The two independent
exact-head adversarial verifiers' verdicts, CI and the batch delta
gates precede any main merge decision, and only a separate T0436
board/evidence closeout after those can truthfully record independent
PASS.

## Content hashes at review head (f748be5)

sha256, reproducible with `sha256sum <path>` at the review head:

- data/contracts/events.yaml: 513adcfcc0345a068eb192b8693ee7f2425fbe26dd293572158cfa1cb2928c1c
- data/contracts/control-plane.yaml (operation source): 6d155e98d890f8d2636cf48bac4ea322151bf6796ddb8489697f91ed14181934
- tests/fixtures/control-plane-events/cases.json (closed fixture): 88c164589f2f0ec49c8e464e232629a7310cfd7998cbbdbd96c6dc3993224ae1
- tools/events_fixture_generate.py (fixture generator): cf8a7a5acf6b2ec0cdd6e36a81352d0612e723dc1aa12089e32a9825e783a09a
- tools/events_contract_lint.py (exact-pinned contract lint): 59e4085441d5922f1e9782289410f14b7287be66d1d062525a099fed3b7aaf81
- server/control_plane_events.py (publisher): a602b1aae6c1bbb18a258b3a3260544f25393034cb2b732e330706c65b82dd03
- server/control_plane_client.py (consumer client): fc5653dbf68c77e68d1b437f23f9c45217389e0fc2c87cc71aaeb23730b831fc
- tests/test_t0435_events_instrument.py (instrument tests): 5baaaba8d1e6f89ad14f1bf93a0d7a5e645d8b65923f72975f23201d08c0c05b
- main at package assembly: f748be5002a8dd556f55b39dc4a6e75fbc3dffa6

## Suite results (build-side)

Commands run at the review head in the author sandbox (python 3.10.12,
pytest, PyYAML, ruff, zstandard):

- Targeted chain + adjacent consumer suites:
  `python -m pytest tests/test_t0428_events_contract.py tests/test_t0429_events_fixture.py tests/test_t0430_events_red.py tests/test_t0431_local_events.py tests/test_t0432_events_properties.py tests/test_t0433_events_fuzz_faults.py tests/test_t0434_events_integration.py tests/test_t0435_events_instrument.py tests/test_t0476_control_plane_client.py tests/test_t0477_control_plane_properties.py -q`
  Result: 698 passed, 21 subtests passed, 0 failed (~29s).
- Lint: `ruff check server/ tests/ tools/` - all checks passed.
- DAG/evidence: `python -m pytest tests/test_dag.py tests/test_evidence_lint.py -q` - 73 passed;
  `python tools/dag_reconcile.py` - "OK DAG reconcile: 288 done tasks
  agree across board, evidence, history".
- Diff hygiene: `git diff --check` - clean.
- Full suite (the CI gate's canonical invocation):
  `python -m pytest tests -q -n auto --dist loadfile --max-worker-restart 0`
  Result: 16746 passed, 5 skipped, 1 failed in 986.68s. The single
  failure
  (tests/test_t0205_atomic_edit_red.py::test_closure_kills_substitution_mutants[with-digests],
  outside this chain) coincided with an xdist worker node crash
  ("node down: Not properly terminated") in the 2-CPU/2GB sandbox; the
  same test passes serially (2 passed in 5.80s) and its whole file
  passes under the same xdist invocation in isolation (78 passed),
  which is consistent with an environment/resource issue but does not
  prove its cause. The full local suite is non-green; the isolated
  reruns do not establish the full gate. A purely serial full-suite run
  was not completed in the container (observed throughput projects
  multiple hours; the CI gate's canonical invocation is the xdist one
  above). Exact-head CI remains the full-gate decider.

No independent-verifier verdict exists at package time. The two
independent verifiers' own rerun environments and results will be
reported in their own future verdicts, separately; build-side and
independent results are not blended.

## Coverage summary (per link)

- contract (T0428): closed v1 envelope (8 fields, error_code required
  only on failure), 14-operation catalog with per-operation error
  scope, identifier grammar, ordering rules (whole-batch atomicity,
  replay no-op, conflict whole-batch refusal, no global order between
  independent batches), 5 failure classes with class-to-code mapping,
  exact-pinned machine lint.
- fixture (T0429): closed executable corpus
  (tests/fixtures/control-plane-events/cases.json) over validate() and
  Ledger.publish(): pinned happy/boundary/malformed/rollback rows,
  hostile-type probes derived from accepted rows, executed-row mutant
  kills.
- red (T0430): acceptance battery bound first to the T0428 test
  reference and flipped to the shipped publisher at T0431 per its
  documented hook; fixture/assertions/mutants never switched to the
  implementation's own oracle.
- implement (T0431): validate/new_event/Ledger - exact-type
  fail-closed validation with no caller-code invocation, opaque
  independent identifiers, SQLite transactional outbox (whole-batch
  atomicity, dedupe by full envelope, replay no-op, corrupt persisted
  row fails closed).
- unit/property (T0432): seeded, contract-derived behavior checks with
  no dependency on the production validator or ledger.
- fuzz/fault (T0433): classified fuzz/fault battery with measured
  mutant-kill witnesses at the mutant's own checkpoint (fresh IDs, no
  broad catches), independent contract-grammar identifier predicate
  with a measured regex-widening mutant kill.
- integration/restart (T0434): 8 tests - multi-process same-ledger,
  SIGKILL mid-transaction (no partial rows), cross-process EXCLUSIVE
  lock contention with typed refusal and retry, two-handle contention,
  uncommitted-batch rollback across restart, replay/conflict after
  restart, no-global-order vs strict within-batch order, cross-process
  malformed persisted-row fail-closed; bounded subprocess cleanup on
  every Popen path.
- instrument (T0435): opt-in `event_ledger` seam on the consumer
  client's logical call - one closed envelope per executed or
  contract-recovery-decided call (never per transport attempt, never
  on validation or token-preflight refusal), opaque independent IDs,
  exact contract error-code scope, typed fail-closed publisher
  refusal; parametrized rows pin all 12 auth-required operations under
  expired and absent tokens.

## Known limits (stated, not waived)

- The T0434 SIGKILL test stages uncommitted SQLite rows directly
  through ledger internals in the child process, then kills the child;
  it proves crash-atomicity of staged-but-uncommitted rows. Fault
  injection INSIDE production publish() at the commit boundary is
  owned and pinned by T0431/T0433 (fail_commit), not by T0434.
- Token-preflight refusals (no held token, locally expired token)
  intentionally record no event: they are consumer-side preflight,
  uniform with validation refusals. Executed auth failures (server
  401s) do record. identity.logout/identity.delete_account declare no
  auth_expired outcome, so the preflight refusal for them is never an
  event and never masks the caller's ControlPlaneError.
- A publisher failure after a committed effect propagates as typed
  control_plane_events.Refusal and does NOT roll back, hide or
  retro-publish the committed effect; on the failure path the refusal
  replaces the operation error (the audit failure dominates).
- The instrumentation is opt-in and local-only (SQLite outbox, no
  network sink). Nothing in this chain emits from a hosted server,
  covers all control-plane operations server-side, or touches
  OAuth/hosted sync.
- Python reference implementation only; no Rust parity claim.

## Results split

Build-side results appear above and come from the author environment.
Independent-verifier results belong to the two exact-head adversarial
verifiers and are recorded in their own verdicts; this package
deliberately contains no independent verdict and no board/evidence
mutation.
