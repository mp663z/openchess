# T0355 Jobs/restart/independent verify - verification package

Scope: the COMPLETE jobs restart chain (T0347-T0354) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
builder lead lane. PROVENANCE DISCLOSURE: the author implemented the
instrument link of this chain (T0354, landed as 850956de482d1291d3a879b234bea0e805afdfa8) and the
T0291 instrument BaseException regression amendment; the other seven
links of this chain were built and reviewed by other lanes. The
author's role in one link is disclosed so reviewers can weigh it;
this package asserts no authority.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0347 | Jobs/restart/contract | 84b26717777778fb12eb5f9609c96a1f63917078 |
| T0348 | Jobs/restart/fixture | bb578f33f49957f6090614d2e8abb6e62c2632c3 |
| T0349 | Jobs/restart/red test | 6e18c2f445271cd37ea4984abb40e44979a5634c |
| T0350 | Jobs/restart/implement | d227462ff61b5d3b22113bd9226e4df8e309f8b5 |
| T0351 | Jobs/restart/unit/property | b061d7b502ebca3abf3f23bf7dd2b8083d7812a9 |
| T0352 | Jobs/restart/fuzz/fault | 10b36529ee8da6298284ae2b0cc5ca8e10ca0540 |
| T0353 | Jobs/restart/integration/restart | 466001068f3d8a1c699622461fabad4b31c292e1 |
| T0354 | Jobs/restart/instrument | 850956de482d1291d3a879b234bea0e805afdfa8 |

Every merge SHA was confirmed an ancestor of main at the review head
by the author (`git merge-base --is-ancestor` for each of the 8).
Every link was reviewed by independent verifiers before merge; the
per-link verdicts (exact reviewed head SHAs, scope statements,
remediation history, CI run links) are recorded in
evidence/T0347.md through evidence/T0354.md on main. This package
submits the chain inventory and reproducible artifacts only - it
does not certify itself and asserts no authority; the task closes
under the independent review and CI gates, not on this document.

## Content hashes at review head

- data/contracts/restart.yaml: de3828bb8b44f15b6b9bfe88250de0ae35d05dfabdd292fd071164b204eaf1de
- server/jobs_restart.py: 5d1559f8492522c5a3a36550fdb224601b8e6bd61d561c0551c1692a92815959
- server/jobs_restart_instrument.py: 02e426f1558f9104db241f285c7ae1f7277f52655e079263a98f5e3e73719b8e
- tests/fixtures/restart/cases.json: cf9d17f119637c2dda31cbae187a23218293821eeaf08a42819e4a8ce44e141e
- main at package assembly: 0145449ee57ddeb956c940c35a74f008bd710ea1

## Full-chain suite result

Command: `python -m pytest tests/test_t0347_restart_contract.py tests/test_t0348_restart_fixture.py tests/test_t0349_restart_red.py tests/test_t0350_restart_runtime.py tests/test_t0351_restart_unit_property.py tests/test_t0352_restart_fuzz_fault.py tests/test_t0353_restart_integration_restart.py tests/test_t0354_restart_instrument.py -q`

Author's own rerun at the review head: 914 passed, 0 failed, in a
single pytest-xdist `-n 4` invocation of exactly the command's file
set; the instrument file additionally run serially (16 passed). Per
file: test_t0347_restart_contract.py 235, test_t0348_restart_fixture.py 401, test_t0349_restart_red.py 30, test_t0350_restart_runtime.py 6, test_t0351_restart_unit_property.py 89, test_t0352_restart_fuzz_fault.py 90, test_t0353_restart_integration_restart.py 47, test_t0354_restart_instrument.py 16.
Author environment: Linux sandbox, CPython 3.12.15 (the repo's
declared toolchain), pytest 9.1.1, PyYAML 6.0.3, ruff 0.17.2,
mypy 1.20.2, pytest-xdist. Build-side environments are recorded in
the per-link evidence files (Linux workspace, CPython 3.10.12/3.12.x,
pytest 9.1.1, ruff 0.17.x, PyYAML 6.0.3, plus GitHub Actions CI on
Python 3.12). The environments are distinct; the runs are reported
separately, not blended. `ruff check .` at the review head: clean.

Like the queue chain, the restart red harness is permanent: it is
kept green against production, and T0350 proves the production
binding switch is exactly its two lines.

## Coverage summary (per link)

- contract: data/contracts/restart.yaml is normative; the battery
  holds the contract-derived reference restart and proves happy,
  boundary, malformed and rollback behavior against it. A restart is a pure transition of one queue state (the linked queue
contract's state) for one request {op, worker, now}: every job leased
by that worker whose lease is still live (now strictly before its
expiry) returns to ready with no lease owner or expiry. The receipt
names the released job ids in seq order, binds the state before and
after through the queue's own state_id, and carries its own
content-addressed restart_id. The new state is committed last, in
place. The strict expiry boundary and the kept attempts are pinned,
and a rejected call leaves state and request unchanged while an
accepted one changes only the unexpired leases of the named worker.
  (The file itself proves contract consistency, not production
  behavior.)
- fixture: tests/fixtures/restart/cases.json - 65 cases: 4 happy, 17 boundary, 42 malformed, 2 rollback.
  Pinned receipts and states were computed from the reference at authoring time,
  so any contract or derivation drift breaks the battery. Every
  malformed case is discriminating via its declared single-locus
  repair. Repairs are single-locus (of the
state or of the request), except the precedence row, which has a bad
request AND a corrupt state and repairs both. Rollback cases prove a
  rejected call leaves the rejected state and request type-exact
  structurally unchanged (snapshot equality over topology and types -
  not byte or original-container identity), then a valid follow-up on
  the row's own state returns the pinned receipt and commits the
  pinned state, copy-on-write: no inner container of the committed
  state is one of the call's pre-call containers (held alive against
  id reuse; the in-place root stays the caller's). Hostile-type rows are
  derived from every request the fixture accepts.
- red test: permanent battery driving every fixture row plus hostile
  totality probes and pinned semantic probes through the
  `restart(state, request)` callable and its typed error: each request
  in a row applied in order to the SAME state object (commit in
  place), returning the pinned receipt exactly and ending in the
  pinned state; state_id and restart_id re-derived here from the
  contract preimages; refusal with the pinned class and code, fresh
  (no __cause__ or __context__), the rejected state and
  request left value-equal to their pre-call deepcopy snapshots
  (value equality at this layer; the type-exact structural check
  lives in the fixture battery); each declared repair is accepted on
  the repaired state and request without error (the resulting receipt
  and state are not pinned); rollback: a rejected restart refuses as
  pinned, then the valid follow-up runs on a fresh copy of the row's
  own pinned state - a different object graph from the rejected one -
  and returns the pinned receipt and pinned state. Pinned semantics: only the
  worker's unexpired leases are released, in ascending seq order,
  attempts are kept, expiry at now is untouched, restart is
  idempotent, jobs conserve.
- implement: production server/jobs_restart.py; the red battery's
  production binding switch proven to be exactly two lines;
  production never imports the test package; its public surface is
  the contract's; production and the T0347 reference agree (same
  record, or same failure class and code, request unchanged) on every
  fixture request and on a grid of hostile variants of each one.
- unit/property: an independent model with no shared code beyond the
  public id helpers, driven with seeded random streams, valid and
  defective; agreement on the record or the failure class, request
  byte-identical, result shares no container with it; unit rows pin
  every bound at both sides, the precedence between failure classes,
  and hostile values and subclasses. Seeded, so a failure replays.
- fuzz/fault: seeded campaign with the T0347 reference as the
  differential oracle; valid cases mutated at every reachable path
  with hostile values (armed objects whose every hook fails the test,
  attribute bombs, lying dicts, cycles, deep and wide values,
  oversize ints under a lowered interpreter int-digit limit), keys
  dropped, added and replaced, faults injected into the digest step;
  a rejected call leaves its input structurally identical under a
  hook-free iterative snapshot; production never runs caller code; a
  late fault leaves the input untouched and the next call clean.
- integration/restart: The engine holds no memory between calls: its whole state is the queue
state {jobs, next_seq}; streams mix queue operations (enqueue, claim,
ack, nack through the production queue) with restarts, and the host
persists the queue state between them. A restart is modelled the
  way a host would - canonical state file under tmp_path, atomic
  replace, the production module executed anew in its own namespace
  (never reloaded), a few cut points in a real child interpreter;
  sampled cut points (12 every-step streams, 10 single random cuts,
  child-interpreter steps) give the same outcomes and the same
  durable value as the uninterrupted run and as the reference.
- instrument: the RestartTracer is an opt-in, content-free wrapper over
  restart, not wired into any shipping path; the trace holds
  only sequence, fixed operation name and accept/reject/crash; exact
  argument, result and thrown-object identity preserved; detached
  trace views; foreign KeyError/AssertionError/KeyboardInterrupt
  identity; hostile trace-list hook suppression; a
  BaseException-raising diagnostics descriptor hook (data-descriptor
  `_trace` getter) proven to fire and never change the policy result;
  parity with the bare module on records, request purity and refusal
  atomicity, state mutation; curated mutants killed by oracle assertions,
  including the swallow-scope witness (except BaseException narrowed
  to except Exception dies by AssertionError, not by crash).

The restart engine takes no caller callback, so there is no
untrusted-component boundary and no forged-error battery in this
chain ("as applicable" in the task acceptance).

## Known environmental note

tests/test_install_checks.py fails 7 of 16 tests with Permission
errors in the author sandbox on pristine main at the review head
(7 failed, 9 passed), identical without any chain change. Cause:
the install checks exec bare `ruff`/`mypy` from PATH; without the
toolchain on PATH (and with a non-searchable PATH entry producing
EACCES rather than ENOENT) the subprocesses raise PermissionError.
The same 7-failure pattern is documented in the landed T0058 and
T0292 packages for this sandbox class. Environmental, unrelated to
the chain. A full-toolchain pass of the install checks is not
certified in this package.

## Author statement

This package was authored by the builder lead lane from origin/main
at the review head, with its own rerun of the full chain suite and
its own reading of every chain artifact. It reports build-side
results only as recorded in the per-link evidence files and does not
blend environments. The author's implementation role in the
instrument link is disclosed above; this document asserts no
authority: the task closes under the independent review and CI gates.
