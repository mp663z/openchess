# T0346 Jobs/limit/independent verify - verification package

Scope: the COMPLETE jobs limit chain (T0338-T0345) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
builder lead lane. PROVENANCE DISCLOSURE: the author implemented the
instrument link of this chain (T0345, landed as a5552d154597ee47a18420a40ece9c422cedcdc0) and the
T0291 instrument BaseException regression amendment; the other seven
links of this chain were built and reviewed by other lanes. The
author's role in one link is disclosed so reviewers can weigh it;
this package asserts no authority.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0338 | Jobs/limit/contract | 83eab67c14413267da222415ad3bb52436884bb6 |
| T0339 | Jobs/limit/fixture | 037706ee3764cd16738c07714ee9be1bfbc52397 |
| T0340 | Jobs/limit/red test | 04fc6a63c07f7ef3fa46a6bd482098fc1f47398d |
| T0341 | Jobs/limit/implement | 089d84859b5b2263c7295e3d0aca6d82c452015c |
| T0342 | Jobs/limit/unit/property | 183ba0b46246e554e7a00a1f38f159be0ab22a1c |
| T0343 | Jobs/limit/fuzz/fault | 6fdb73883fee00f2a5d8f32f039862db3a2a62e0 |
| T0344 | Jobs/limit/integration/restart | 44e10e90dc22c36872bb57f790313de84e8dd5b4 |
| T0345 | Jobs/limit/instrument | a5552d154597ee47a18420a40ece9c422cedcdc0 |

Every merge SHA was confirmed an ancestor of main at the review head
by the author (`git merge-base --is-ancestor` for each of the 8).
Every link was reviewed by independent verifiers before merge; the
per-link verdicts (exact reviewed head SHAs, scope statements,
remediation history, CI run links) are recorded in
evidence/T0338.md through evidence/T0345.md on main. This package
submits the chain inventory and reproducible artifacts only - it
does not certify itself and asserts no authority; the task closes
under the independent review and CI gates, not on this document.

## Content hashes at review head

- data/contracts/limit.yaml: c0216681ff68ad24049510e683e860ec5c63644978c4b6394b9898eb1f8c62d7
- server/jobs_limit.py: c6973831d1b5d8ae85bfdc154fdd36e181199abcebc280d494b84a13845b67cd
- server/jobs_limit_instrument.py: 81ec97b685445ae0bebdf64b1d3a6114caee94af21cbc7a2c5ce302205b45035
- tests/fixtures/limit/cases.json: 43c243673ea0c22699db42e883c1d22504a19424f8f65f9ee7451203276c005c
- main at package assembly: 0145449ee57ddeb956c940c35a74f008bd710ea1

## Full-chain suite result

Command: `python -m pytest tests/test_t0338_limit_contract.py tests/test_t0339_limit_fixture.py tests/test_t0340_limit_red.py tests/test_t0341_limit_runtime.py tests/test_t0342_limit_unit_property.py tests/test_t0343_limit_fuzz_fault.py tests/test_t0344_limit_integration_restart.py tests/test_t0345_limit_instrument.py -q`

Author's own rerun at the review head: 971 passed, 0 failed, in a
single pytest-xdist `-n 4` invocation of exactly the command's file
set; the instrument file additionally run serially (16 passed). Per
file: test_t0338_limit_contract.py 219, test_t0339_limit_fixture.py 423, test_t0340_limit_red.py 29, test_t0341_limit_runtime.py 6, test_t0342_limit_unit_property.py 149, test_t0343_limit_fuzz_fault.py 87, test_t0344_limit_integration_restart.py 42, test_t0345_limit_instrument.py 16.
Author environment: Linux sandbox, CPython 3.12.15 (the repo's
declared toolchain), pytest 9.1.1, PyYAML 6.0.3, ruff 0.17.2,
mypy 1.20.2, pytest-xdist. Build-side environments are recorded in
the per-link evidence files (Linux workspace, CPython 3.10.12/3.12.x,
pytest 9.1.1, ruff 0.17.x, PyYAML 6.0.3, plus GitHub Actions CI on
Python 3.12). The environments are distinct; the runs are reported
separately, not blended. `ruff check .` at the review head: clean.

Like the queue chain, the limit red harness is permanent: it is
kept green against production, and T0341 proves the production
binding switch is exactly its two lines.

## Coverage summary (per link)

- contract: data/contracts/limit.yaml is normative; the battery
  holds the contract-derived reference admit and proves happy,
  boundary, malformed and rollback behavior against it. A limit decision is a pure function of one request
{op, key, cost, now, policy, previous}: a keyed token bucket of
capacity tokens gains one token every refill_ms; previous is null for
a fresh full bucket and otherwise exactly the previous bucket record.
The request is admitted when the refilled tokens cover the cost,
otherwise denied with the exact retry_after_ms; unit/property also
proves the admitted cost over any window never exceeds capacity plus
the refill earned, and that a deny's retry_after_ms is exact
(admitted at now + retry, denied one millisecond earlier).
  (The file itself proves contract consistency, not production
  behavior.)
- fixture: tests/fixtures/limit/cases.json - 74 cases: 4 happy, 14 boundary, 51 malformed, 5 rollback.
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
  `admit(request)` callable and its typed error: exact records with ids
  re-derived independently from the contract preimage, pinned failure
  class and code, fresh errors (no __cause__ or __context__), request
  untouched and sharing no container with the record; each declared repair either is accepted or still
  rejects with a typed error whose failure class differs from the
  row's pinned one (it reaches a later validation stage); the
  contract-derived reference accepts all 51 repairs, but the battery
  pins only the different-class property, not acceptance; rollback
  rows refuse the rejected request as pinned, leaving it value-equal
  to its pre-call snapshot (deepcopy equality at this layer), then
  accept the valid follow-up as pinned on the same callable.
- implement: production server/jobs_limit.py; the red battery's
  production binding switch proven to be exactly two lines;
  production never imports the test package; its public surface is
  the contract's; production and the T0338 reference agree (same
  record, or same failure class and code, request unchanged) on every
  fixture request and on a grid of hostile variants of each one.
- unit/property: an independent model with no shared code beyond the
  public id helpers, driven with seeded random streams, valid and
  defective; agreement on the record or the failure class, request
  byte-identical, result shares no container with it; unit rows pin
  every bound at both sides, the precedence between failure classes,
  and hostile values and subclasses. Seeded, so a failure replays.
- fuzz/fault: seeded campaign with the T0338 reference as the
  differential oracle; valid cases mutated at every reachable path
  with hostile values (armed objects whose every hook fails the test,
  attribute bombs, lying dicts, cycles, deep and wide values,
  oversize ints under a lowered interpreter int-digit limit), keys
  dropped, added and replaced, faults injected into the digest step;
  a rejected call leaves its input structurally identical under a
  hook-free iterative snapshot; production never runs caller code; a
  late fault leaves the input untouched and the next call clean.
- integration/restart: A token bucket chains by the previous bucket record, so the durable value
is the last good record; a restart continues the bucket from the file. A restart is modelled the
  way a host would - canonical state file under tmp_path, atomic
  replace, the production module executed anew in its own namespace
  (never reloaded), a few cut points in a real child interpreter;
  sampled cut points (12 every-step streams, 10 single random cuts,
  child-interpreter steps) give the same outcomes and the same
  durable value as the uninterrupted run and as the reference.
- instrument: the LimitTracer is an opt-in, content-free wrapper over
  admit, not wired into any shipping path; the trace holds
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

The limit engine takes no caller callback, so there is no
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
