# T0328 Jobs/progress/independent verify - verification package

Scope: the COMPLETE jobs progress chain (T0320-T0327) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
builder lead lane. PROVENANCE DISCLOSURE: the author implemented the
instrument link of this chain (T0327, landed as 5c29edfee7228a6a64573c0d1c08d6798deaefb3) and the
T0291 instrument BaseException regression amendment; the other seven
links of this chain were built and reviewed by other lanes. The
author's role in one link is disclosed so reviewers can weigh it;
this package asserts no authority.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0320 | Jobs/progress/contract | e379240cc85ed719277c5367f4b89c647987bd07 |
| T0321 | Jobs/progress/fixture | ff19d9606bb244cd1cdbe3191d11f32fd97af90f |
| T0322 | Jobs/progress/red test | 90bd36fa90daf2d6449e81e442928f963f3d26cd |
| T0323 | Jobs/progress/implement | 104a16014dbe315c867105cf8b73c76ebbd42b64 |
| T0324 | Jobs/progress/unit/property | 082780af219647aa408074785d8733ed0c8cadd3 |
| T0325 | Jobs/progress/fuzz/fault | 37effa51295a6bb4a85381a4dde9b3483a2bf675 |
| T0326 | Jobs/progress/integration/restart | af16e192feb013c66bfe1f74ac14083ac7eba6a7 |
| T0327 | Jobs/progress/instrument | 5c29edfee7228a6a64573c0d1c08d6798deaefb3 |

Every merge SHA was confirmed an ancestor of main at the review head
by the author (`git merge-base --is-ancestor` for each of the 8).
Every link was reviewed by independent verifiers before merge; the
per-link verdicts (exact reviewed head SHAs, scope statements,
remediation history, CI run links) are recorded in
evidence/T0320.md through evidence/T0327.md on main. This package
submits the chain inventory and reproducible artifacts only - it
does not certify itself and asserts no authority; the task closes
under the independent review and CI gates, not on this document.

## Content hashes at review head

- data/contracts/progress.yaml: c32b5d109183f8fd7b0e6364631b550f2c052af0238d987843b992cffe7f0165
- server/jobs_progress.py: 3ad37df5e9037c6a8c3d7026ff428d0040c0058b608523349cbd8249e6dd0f15
- server/jobs_progress_instrument.py: 2d973971ea7f48ea2ca23e45543bf93bddd82b623493a2640af3441683ad85bf
- tests/fixtures/progress/cases.json: c05b29344c55be48f96cebe10c8863bee07469ce54edcf06877275865a90a3b3
- main at package assembly: 0145449ee57ddeb956c940c35a74f008bd710ea1

## Full-chain suite result

Command: `python -m pytest tests/test_t0320_progress_contract.py tests/test_t0321_progress_fixture.py tests/test_t0322_progress_red.py tests/test_t0323_progress_runtime.py tests/test_t0324_progress_unit_property.py tests/test_t0325_progress_fuzz_fault.py tests/test_t0326_progress_integration_restart.py tests/test_t0327_progress_instrument.py -q`

Author's own rerun at the review head: 729 passed, 0 failed, in a
single pytest-xdist `-n 4` invocation of exactly the command's file
set; the instrument file additionally run serially (16 passed). Per
file: test_t0320_progress_contract.py 171, test_t0321_progress_fixture.py 283, test_t0322_progress_red.py 26, test_t0323_progress_runtime.py 6, test_t0324_progress_unit_property.py 104, test_t0325_progress_fuzz_fault.py 84, test_t0326_progress_integration_restart.py 39, test_t0327_progress_instrument.py 16.
Author environment: Linux sandbox, CPython 3.12.15 (the repo's
declared toolchain), pytest 9.1.1, PyYAML 6.0.3, ruff 0.17.2,
mypy 1.20.2, pytest-xdist. Build-side environments are recorded in
the per-link evidence files (Linux workspace, CPython 3.10.12/3.12.x,
pytest 9.1.1, ruff 0.17.x, PyYAML 6.0.3, plus GitHub Actions CI on
Python 3.12). The environments are distinct; the runs are reported
separately, not blended. `ruff check .` at the review head: clean.

Like the queue chain, the progress red harness is permanent: it is
kept green against production, and T0323 proves the production
binding switch is exactly its two lines.

## Coverage summary (per link)

- contract: data/contracts/progress.yaml is normative; the battery
  holds the contract-derived reference report and proves happy,
  boundary, malformed and rollback behavior against it. A progress report is a pure function of one request
{op, job_id, worker, done, total, now, previous}: previous is null on
the first report and otherwise exactly the previous progress record.
The record carries percent_bp = floor(done * 10000 / total), the
report time, and the chain link previous_id; the total is fixed by
the first report and a changed total is a conflict.
  (The file itself proves contract consistency, not production
  behavior.)
- fixture: tests/fixtures/progress/cases.json - 47 cases: 4 happy, 8 boundary, 32 malformed, 3 rollback.
  Pinned records were computed from the reference at authoring time,
  so any contract or derivation drift breaks the battery. Every
  malformed case is discriminating via its declared single-locus
  repair. Rollback cases prove a rejected call
  leaves the exact supplied request bit- and value-identical before
  a valid follow-up succeeds as pinned. Hostile-type rows are
  derived from every request the fixture accepts.
- red test: permanent battery driving every fixture row plus hostile
  totality probes and pinned semantic probes through the
  `report(request)` callable and its typed error: exact records with ids
  re-derived independently from the contract preimage, pinned failure
  class and code, fresh errors (no __cause__ or __context__), request
  untouched and sharing no container with the record, and each
  declared repair accepted; rollback rows run on the same engine and
  state.
- implement: production server/jobs_progress.py; the red battery's
  production binding switch proven to be exactly two lines;
  production never imports the test package; its public surface is
  the contract's; production and the T0320 reference agree (same
  record, or same failure class and code, request unchanged) on every
  fixture request and on a grid of hostile variants of each one.
- unit/property: an independent model with no shared code beyond the
  public id helpers, driven with seeded random streams, valid and
  defective; agreement on the record or the failure class, request
  byte-identical, result shares no container with it; unit rows pin
  every bound at both sides, the precedence between failure classes,
  and hostile values and subclasses. Seeded, so a failure replays.
- fuzz/fault: seeded campaign with the T0320 reference as the
  differential oracle; valid cases mutated at every reachable path
  with hostile values (armed objects whose every hook fails the test,
  attribute bombs, lying dicts, cycles, deep and wide values,
  oversize ints under a lowered interpreter int-digit limit), keys
  dropped, added and replaced, faults injected into the digest step;
  a rejected call leaves its input structurally identical under a
  hook-free iterative snapshot; production never runs caller code; a
  late fault leaves the input untouched and the next call clean.
- integration/restart: A progress record chains through previous, so the durable value is the
last good record; the host persists it between runs. A restart is modelled the
  way a host would - canonical state file under tmp_path, atomic
  replace, the production module executed anew in its own namespace
  (never reloaded), a few cut points in a real child interpreter;
  sampled cut points (12 every-step streams, 10 single random cuts,
  child-interpreter steps) give the same outcomes and the same
  durable value as the uninterrupted run and as the reference.
- instrument: the ProgressTracer is an opt-in, content-free wrapper over
  report, not wired into any shipping path; the trace holds
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

The progress engine takes no caller callback, so there is no
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
