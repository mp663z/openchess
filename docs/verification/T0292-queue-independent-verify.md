# T0292 Jobs/queue/independent verify - verification package

Scope: the COMPLETE jobs queue chain (T0284-T0291) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
independent verifier lane (D); it built no link of this chain.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0284 | Jobs/queue/contract | af515c8b32ef509629c14396a4a07ab5aefd492f |
| T0285 | Jobs/queue/fixture | 8a4209c4e385b8473da2c61f995c8f6c11907a88 |
| T0286 | Jobs/queue/red test | d98a9562f35dfc709c325b1272f39510ecbd94bf |
| T0287 | Jobs/queue/implement | 539c35c4b7a8886e8c37f3efe521c3d97519b581 |
| T0288 | Jobs/queue/unit/property | 9914ddd9219836ee6e31fbbc40b01771cefced6a |
| T0289 | Jobs/queue/fuzz/fault | 2861e0ddcdbae7f48dde2e2bd963fee1a42081ce |
| T0290 | Jobs/queue/integration/restart | 09770e78cd43838a9b21df09ac69bd82686ea485 |
| T0291 | Jobs/queue/instrument | 7231dfc537c6237ce4fb0a61c7b801045f5fee71 |

Every merge SHA was confirmed an ancestor of main at the review head
by the verifier (`git merge-base --is-ancestor` for each of the 8).
Every link was reviewed by independent verifiers before merge; the
per-link verdicts (exact reviewed head SHAs, scope statements,
remediation history, CI run links) are recorded in
evidence/T0284.md through evidence/T0291.md on main. This package
submits the chain inventory and reproducible artifacts only - it
does not certify itself and asserts no authority; the task closes
under the independent review and CI gates, not on this document.

## Content hashes at review head

- data/contracts/queue.yaml: e107958e139e72bd673ec92d825b5c4aa741e84eed6a0c23495079a779a4310a
- server/jobs_queue.py: 7568b0af3d06f11b56989e0a2d697668a5c0ef2b405afed47bf66da80603d6d4
- server/jobs_queue_instrument.py: 4dc0c16f3798ed204acbaf71458a3ac055cb6dbb9d3fcef1c994712fab3fdd36
- tests/fixtures/queue/cases.json: aaac61632d7d34777c1ebbb624f05e4399253ebb8c54f7280043eb0fab8914f0
- main at package assembly: 0612a67e6629052079694706dfd46bbfce0817c5

## Full-chain suite result

Command: `python -m pytest tests/test_t0284_queue_contract.py
tests/test_t0285_queue_fixture.py tests/test_t0286_queue_red.py
tests/test_t0287_queue_runtime.py tests/test_t0288_queue_unit_property.py
tests/test_t0289_queue_fuzz_fault.py
tests/test_t0290_queue_integration_restart.py
tests/test_t0291_queue_instrument.py -q`

Independent verifier's own rerun at the review head, from a fresh
clone of origin/main: 1105 passed, 0 failed. Per file: contract +
fixture 247, red battery 72, runtime + instrument 478,
unit/property 121, fuzz/fault 113, integration/restart 74. The
verifier sandbox caps single commands at 120s wall-clock, so the
files were run in slices of the same set and the slower files (red,
fuzz, integration) ran under pytest-xdist `-n 4`; the slices cover
exactly the command's file set, no more and no less. Verifier
environment: Linux sandbox, CPython 3.10.12, pytest 9.1.1,
PyYAML 6.0.3, ruff 0.17.2, mypy 2.4.0, pytest-xdist.
Build-side environments are recorded in the per-link evidence files
(Linux workspace, CPython 3.10.12/3.12.14, pytest 9.1.1,
ruff 0.17.0, PyYAML 6.0.3, plus GitHub Actions CI on Python 3.12).
The environments are distinct; the runs are reported separately,
not blended. `ruff check .` at the review head: clean.

Unlike the turn chain, the T0286 red harness is permanent: it is
kept green against production, and T0287 proves the production
binding switch is exactly its two lines.

## Coverage summary (per link)

- contract: data/contracts/queue.yaml is normative; the battery
  holds the contract-derived reference engine and proves happy,
  boundary, malformed and rollback behavior against it. Pure state
  machine over {jobs, next_seq} with an explicit logical clock;
  enqueue idempotent by dedupe key; claim by lowest priority then
  lowest seq with lease-expiry reclaim and max_attempts dead; ack
  and nack only by the lease owner while the lease is live; full
  state validated first, commit LAST in place, rejection leaves
  state and request bit-identical (T0284; the file itself states it
  proves contract consistency, not production behavior).
- fixture: tests/fixtures/queue/cases.json - 43 cases: 5 happy,
  11 boundary, 23 malformed, 4 rollback. Receipts and committed
  states pinned from the reference at authoring time; every
  malformed case is discriminating via its declared single-locus
  repair; rollback cases prove bit- and reference-identical input
  after rejection. The fixture docstring carries a provenance note:
  its two re-enqueue-at-capacity boundary rows follow the merged
  T0284 reference reading (dedupe before the seq/capacity checks),
  recorded at authoring time as awaiting owner confirmation
  (T0285).
- red test: permanent battery driving every fixture row plus a
  closed set of totality probes and pinned semantic probes through
  an engine constructed with no arguments; exact receipts, exact
  committed states, pinned failure class and code, fresh typed
  errors, aliasing and determinism pins, rollback on the same
  engine and state (T0286).
- implement: production server/jobs_queue.py; the red battery's
  production binding switch proven to be exactly two lines;
  production never imports the test package; public surface is the
  contract's; every fixture row and every totality probe agrees
  with the T0284 reference (T0287).
- unit/property: an independent model (plain list of dicts, no
  shared code beyond the public id helpers) driven with the same
  seeded random operation streams as the production engine;
  agreement on receipt or failure class and on state after every
  step; rejected calls leave state and request byte-identical;
  seeded for replay (T0288).
- fuzz/fault: seeded campaign with the T0284 reference as the
  differential oracle; valid streams, hostile requests, hostile
  states and faulting payload objects; production never runs caller
  code (hash, eq, repr, iteration hooks) on hostile objects
  (T0289).
- integration/restart: restart modelled as a host would - canonical
  state file under tmp_path, atomic replace, fresh engine; every
  cut point of the seeded streams equals the uninterrupted run;
  leases lapse by the injected clock and are re-claimed. Torn
  state bytes that no longer parse are a host-layer JSONDecodeError
  and never reach the engine (the battery asserts the host must
  not hand them over); parseable-but-malformed states - torn
  writes that still parse, or lenient host repairs - reach the
  engine and are refused as corrupt_queue, and the last good
  file still works. These rows assert the refusal class only:
  the torn loop discards the parsed candidate and the repaired
  rows apply a deepcopy without a retained comparison, so
  refusal purity is not pinned here. Sampled refusal purity
  ("a refusal never touches the caller's state") is pinned by
  the _refused rows via snapshot-compare, and rollback semantics
  by the T0286 red battery. Boundary rows re-cover a sample
  of validator edges at the restart level (queue-size bound
  through the file round trip, non-str payload keys, request-shape
  rows, hook-free subclass values) with the pinned failure
  classes: sampled coverage, with no exhaustive mutant-survival
  claim (T0290).
- instrument: QueueTracer is an opt-in, content-free wrapper over
  apply, not wired into any shipping path; exact argument, result
  and thrown-object identity preserved; detached trace views;
  foreign crash family; hostile trace-list hook suppression;
  parity with the bare engine on receipts, state mutation, request
  purity and refusal atomicity (T0291).

The queue engine takes no caller callback, so there is no
untrusted-component boundary and no forged-error battery in this
chain ("as applicable" in the task acceptance).

## Known environmental note

tests/test_install_checks.py fails 7 of 16 tests with Permission
errors in the verifier sandbox on pristine main at the review head
(7 failed, 9 passed), identical without any chain change. Cause:
the install checks exec bare `ruff`/`mypy` from PATH; without the
toolchain on PATH (and with a non-searchable PATH entry producing
EACCES rather than ENOENT) the subprocesses raise PermissionError.
The same 7-failure pattern is documented in the landed T0058
package for this sandbox class. Environmental, unrelated to the
chain. A full-toolchain pass of the install checks is not
certified in this package.

## Verifier statement

This package was authored by the independent verifier lane from a
fresh clone at the review head, with its own rerun of the full
chain suite and its own reading of every chain artifact. It reports
build-side results only as recorded in the per-link evidence files
and does not blend environments. It asserts no authority: the task
closes under the independent review and CI gates.
