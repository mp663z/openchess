# T0319 Jobs/retry/independent verify - verification package

Scope: the COMPLETE jobs retry chain (T0311-T0318) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
builder lead lane. PROVENANCE DISCLOSURE: the author implemented the
instrument link of this chain (T0318, landed as 4ea6b7ed2dbbba23d9c7c25df86ba2f789a4b428) and the
T0291 instrument BaseException regression amendment; the other seven
links of this chain were built and reviewed by other lanes. The
author's role in one link is disclosed so reviewers can weigh it;
this package asserts no authority.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0311 | Jobs/retry/contract | 93c0f5b83ea65ff6f6285b36234c4d7f7a1cf9ba |
| T0312 | Jobs/retry/fixture | c7275d80203fb49ba2c6b5b93ce50ceb8273e10a |
| T0313 | Jobs/retry/red test | 7c2e839f5b2187b7ba9adaa908cd5995aa0fb9d9 |
| T0314 | Jobs/retry/implement | 4197a251d6ed57b441d21ceee5df33a2ef35984f |
| T0315 | Jobs/retry/unit/property | 7134c1470223353e141506c4c8bfc061dcb1a39c |
| T0316 | Jobs/retry/fuzz/fault | 1cfdd91a4f92295fe3052e78fa259c5313f0d032 |
| T0317 | Jobs/retry/integration/restart | ba0130d420d901b5d38c6d9a8641c76d456a97be |
| T0318 | Jobs/retry/instrument | 4ea6b7ed2dbbba23d9c7c25df86ba2f789a4b428 |

Every merge SHA was confirmed an ancestor of main at the review head
by the author (`git merge-base --is-ancestor` for each of the 8).
Every link was reviewed by independent verifiers before merge; the
per-link verdicts (exact reviewed head SHAs, scope statements,
remediation history, CI run links) are recorded in
evidence/T0311.md through evidence/T0318.md on main. This package
submits the chain inventory and reproducible artifacts only - it
does not certify itself and asserts no authority; the task closes
under the independent review and CI gates, not on this document.

## Content hashes at review head

- data/contracts/retry.yaml: a5dd2e642f08791ea6d7393ed4163c0c300d62e7acf407faf7754ce317f25268
- server/jobs_retry.py: 89fb9b2562a1d18103c7a9a8379aa900f843f7dd47a8b27d5e44e93bd0fefe11
- server/jobs_retry_instrument.py: cf9a49503bd051f8797306f143e415c09ff44ddb471c65e93f3bba6ee30837eb
- tests/fixtures/retry/cases.json: de4e24c4297f4619952cef030b31d3511c4c29282adaa3f86f06fa573f72ee1a
- main at package assembly: 0145449ee57ddeb956c940c35a74f008bd710ea1

## Full-chain suite result

Command: `python -m pytest tests/test_t0311_retry_contract.py tests/test_t0312_retry_fixture.py tests/test_t0313_retry_red.py tests/test_t0314_retry_runtime.py tests/test_t0315_retry_unit_property.py tests/test_t0316_retry_fuzz_fault.py tests/test_t0317_retry_integration_restart.py tests/test_t0318_retry_instrument.py -q`

Author's own rerun at the review head: 649 passed, 0 failed, in a
single pytest-xdist `-n 4` invocation of exactly the command's file
set; the instrument file additionally run serially (16 passed). Per
file: test_t0311_retry_contract.py 110, test_t0312_retry_fixture.py 233, test_t0313_retry_red.py 26, test_t0314_retry_runtime.py 10, test_t0315_retry_unit_property.py 129, test_t0316_retry_fuzz_fault.py 84, test_t0317_retry_integration_restart.py 41, test_t0318_retry_instrument.py 16.
Author environment: Linux sandbox, CPython 3.12.15 (the repo's
declared toolchain), pytest 9.1.1, PyYAML 6.0.3, ruff 0.17.2,
mypy 1.20.2, pytest-xdist. Build-side environments are recorded in
the per-link evidence files (Linux workspace, CPython 3.10.12/3.12.x,
pytest 9.1.1, ruff 0.17.x, PyYAML 6.0.3, plus GitHub Actions CI on
Python 3.12). The environments are distinct; the runs are reported
separately, not blended. `ruff check .` at the review head: clean.

Like the queue chain, the retry red harness is permanent: it is
kept green against production, and T0314 proves the production
binding switch is exactly its two lines.

## Coverage summary (per link)

- contract: data/contracts/retry.yaml is normative; the battery
  holds the contract-derived reference decide and proves happy,
  boundary, malformed and rollback behavior against it. A retry decision is a pure function of one request
{op, job_id, attempts, failure, now, policy}: a permanent failure or
an exhausted job (attempts == max_attempts) is dead; any other failure
is retried after min(max_delay_ms, base_delay_ms * multiplier **
(attempts - 1)) ms at now + delay, with no jitter; a retry_at past
2**53 - 1 is clock_overflow, never clamped and never turned into dead.
  (The file itself proves contract consistency, not production
  behavior.)
- fixture: tests/fixtures/retry/cases.json - 40 cases: 4 happy, 9 boundary, 24 malformed, 3 rollback.
  Pinned records were computed from the reference at authoring time,
  so any contract or derivation drift breaks the battery. Every
  malformed case is discriminating via its declared single-locus
  repair. Rollback cases prove a rejected call
  leaves the supplied request type-exact structurally unchanged
  (snapshot equality over container types, keys and scalars - not
  byte or original-container identity) before a valid follow-up
  succeeds as pinned. Hostile-type rows are
  derived from every request the fixture accepts.
- red test: permanent battery driving every fixture row plus hostile
  totality probes and pinned semantic probes through the
  `decide(request)` callable and its typed error: exact records with ids
  re-derived independently from the contract preimage, pinned failure
  class and code, fresh errors (no __cause__ or __context__), request
  untouched and sharing no container with the record; each declared repair is accepted with its decision
  pinned to retry or dead (the repaired record is not pinned
  field-by-field); rollback rows refuse the rejected request as
  pinned, leaving it value-equal to its pre-call snapshot (deepcopy
  equality at this layer), then accept the valid follow-up as pinned
  on the same callable.
- implement: production server/jobs_retry.py; the red battery's
  production binding switch proven to be exactly two lines;
  production never imports the test package; its public surface is
  the contract's; production and the T0311 reference agree (same
  record, or same failure class and code, request unchanged) on every
  fixture request and on a grid of hostile variants of each one.
- unit/property: an independent model with no shared code beyond the
  public id helpers, driven with seeded random streams, valid and
  defective; agreement on the record or the failure class, request
  byte-identical, result shares no container with it; unit rows pin
  every bound at both sides, the precedence between failure classes,
  and hostile values and subclasses. Seeded, so a failure replays.
- fuzz/fault: seeded campaign with the T0311 reference as the
  differential oracle; valid cases mutated at every reachable path
  with hostile values (armed objects whose every hook fails the test,
  attribute bombs, lying dicts, cycles, deep and wide values,
  oversize ints under a lowered interpreter int-digit limit), keys
  dropped, added and replaced, faults injected into the digest step;
  a rejected call leaves its input structurally identical under a
  hook-free iterative snapshot; production never runs caller code; a
  late fault leaves the input untouched and the next call clean.
- integration/restart: A retry decision depends on nothing but its request, so the durable value
is the job's attempt count, clock and policy; the host persists the
decision log between runs. A restart is modelled the
  way a host would - canonical state file under tmp_path, atomic
  replace, the production module executed anew in its own namespace
  (never reloaded), a few cut points in a real child interpreter;
  sampled cut points (12 every-step streams, 10 single random cuts,
  child-interpreter steps) give the same outcomes and the same
  durable value as the uninterrupted run and as the reference.
- instrument: the RetryTracer is an opt-in, content-free wrapper over
  decide, not wired into any shipping path; the trace holds
  only sequence, fixed operation name and accept/reject/crash; exact
  argument, result and thrown-object identity preserved; detached
  trace views; foreign KeyError/AssertionError/KeyboardInterrupt
  identity; hostile trace-list hook suppression; a
  BaseException-raising diagnostics descriptor hook (data-descriptor
  `_trace` getter) proven to fire and never change the policy result;
  parity with the bare module on records, request purity and refusal
  atomicity; curated mutants killed by oracle assertions,
  including the swallow-scope witness (except BaseException narrowed
  to except Exception dies by AssertionError, not by crash).

The retry engine takes no caller callback, so there is no
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
