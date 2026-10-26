# T0337 Jobs/dead_letter/independent verify - verification package

Scope: the COMPLETE jobs dead_letter chain (T0329-T0336) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion. The author of this package is the
builder lead lane. PROVENANCE DISCLOSURE: the author implemented the
instrument link of this chain (T0336, landed as b882f20c2f983bacb25214b105ec7772ff8d71b1) and the
T0291 instrument BaseException regression amendment; the other seven
links of this chain were built and reviewed by other lanes. The
author's role in one link is disclosed so reviewers can weigh it;
this package asserts no authority.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0329 | Jobs/dead_letter/contract | 2a9b9690befa90fcfa03b32c9f9361a4c2bbce25 |
| T0330 | Jobs/dead_letter/fixture | 6483fd981e367ff41cf52435ef075a44cf207320 |
| T0331 | Jobs/dead_letter/red test | 157292f94c20c8619148dd06b349de59c586bad1 |
| T0332 | Jobs/dead_letter/implement | c08cbd7eb718ddb114a930de1c1e2d30e0517bbd |
| T0333 | Jobs/dead_letter/unit/property | b53bab15f7b643ba4b55ff37ef9499e95a228001 |
| T0334 | Jobs/dead_letter/fuzz/fault | 236f5a330b62ac43c06f6c4a8b553499440193dc |
| T0335 | Jobs/dead_letter/integration/restart | 52736f7fade9e77c9b6473958a92ecfaf8d0f84c |
| T0336 | Jobs/dead_letter/instrument | b882f20c2f983bacb25214b105ec7772ff8d71b1 |

Every merge SHA was confirmed an ancestor of main at the review head
by the author (`git merge-base --is-ancestor` for each of the 8).
Every link was reviewed by independent verifiers before merge; the
per-link verdicts (exact reviewed head SHAs, scope statements,
remediation history, CI run links) are recorded in
evidence/T0329.md through evidence/T0336.md on main. This package
submits the chain inventory and reproducible artifacts only - it
does not certify itself and asserts no authority; the task closes
under the independent review and CI gates, not on this document.

## Content hashes at review head

- data/contracts/dead_letter.yaml: f35063ae89095471871b4b9e55b0153b49053884cc8d921134774fba1e9804b1
- server/jobs_dead_letter.py: 32d46dddc70785cfedb955685acd9a35c3a71a56ef200b285aa69dd3aad61ff1
- server/jobs_dead_letter_instrument.py: 36ec90ca84c3a6fcdc6b049adc3bd423dd2bdd7154c880f1f2ee876511ccff7e
- tests/fixtures/dead_letter/cases.json: 7512187f7a84849f0c7d6aaacde17a95da69842e102f012059d6831b36c76098
- main at package assembly: 0145449ee57ddeb956c940c35a74f008bd710ea1

## Full-chain suite result

Command: `python -m pytest tests/test_t0329_dead_letter_contract.py tests/test_t0330_dead_letter_fixture.py tests/test_t0331_dead_letter_red.py tests/test_t0332_dead_letter_runtime.py tests/test_t0333_dead_letter_unit_property.py tests/test_t0334_dead_letter_fuzz_fault.py tests/test_t0335_dead_letter_integration_restart.py tests/test_t0336_dead_letter_instrument.py -q`

Author's own rerun at the review head: 736 passed, 0 failed, in a
single pytest-xdist `-n 4` invocation of exactly the command's file
set; the instrument file additionally run serially (16 passed). Per
file: test_t0329_dead_letter_contract.py 173, test_t0330_dead_letter_fixture.py 296, test_t0331_dead_letter_red.py 25, test_t0332_dead_letter_runtime.py 6, test_t0333_dead_letter_unit_property.py 91, test_t0334_dead_letter_fuzz_fault.py 86, test_t0335_dead_letter_integration_restart.py 43, test_t0336_dead_letter_instrument.py 16.
Author environment: Linux sandbox, CPython 3.12.15 (the repo's
declared toolchain), pytest 9.1.1, PyYAML 6.0.3, ruff 0.17.2,
mypy 1.20.2, pytest-xdist. Build-side environments are recorded in
the per-link evidence files (Linux workspace, CPython 3.10.12/3.12.x,
pytest 9.1.1, ruff 0.17.x, PyYAML 6.0.3, plus GitHub Actions CI on
Python 3.12). The environments are distinct; the runs are reported
separately, not blended. `ruff check .` at the review head: clean.

Like the queue chain, the dead_letter red harness is permanent: it is
kept green against production, and T0332 proves the production
binding switch is exactly its two lines.

## Coverage summary (per link)

- contract: data/contracts/dead_letter.yaml is normative; the battery
  holds the contract-derived reference bury and proves happy,
  boundary, malformed and rollback behavior against it. A dead-letter entry is a pure function of one request
{op, job, reason, now}: job is exactly one job of the linked queue
contract. Only a job satisfying every queue job invariant with status
dead is buried; the entry binds the payload through a digest (never a
payload copy) and carries its own content-addressed entry_id.
  (The file itself proves contract consistency, not production
  behavior.)
- fixture: tests/fixtures/dead_letter/cases.json - 52 cases: 4 happy, 10 boundary, 35 malformed, 3 rollback.
  Pinned records were computed from the reference at authoring time,
  so any contract or derivation drift breaks the battery. Every
  malformed case is discriminating via its declared single-locus
  repair, except that a corrupt non-dead job
(ready or leased, flagged by minimal_repair.expect_failure) repairs
to a still-not-dead job that fails job_not_dead, pinning job
integrity ahead of the dead check. Rollback cases prove a rejected call
  leaves the supplied request type-exact structurally unchanged
  (snapshot equality over container types, keys and scalars - not
  byte or original-container identity) before a valid follow-up
  succeeds as pinned. Hostile-type rows are
  derived from every request the fixture accepts.
- red test: permanent battery driving every fixture row plus hostile
  totality probes and pinned semantic probes through the
  `bury(request)` callable and its typed error: exact records with ids
  re-derived independently from the contract preimage, pinned failure
  class and code, fresh errors (no __cause__ or __context__), request
  untouched and sharing no container with the record; each declared repair either is accepted or still
  rejects with a typed error whose failure class differs from the
  row's pinned one (the corrupt-non-dead-job repairs described in the
  fixture bullet intentionally still reject job_not_dead at the next
  validation stage); the battery pins only the different-class
  property, not acceptance; rollback rows refuse the rejected request
  as pinned, leaving it value-equal to its pre-call snapshot
  (deepcopy equality at this layer), then accept the valid follow-up
  as pinned on the same callable.
- implement: production server/jobs_dead_letter.py; the red battery's
  production binding switch proven to be exactly two lines;
  production never imports the test package; its public surface is
  the contract's; production and the T0329 reference agree (same
  record, or same failure class and code, request unchanged) on every
  fixture request and on a grid of hostile variants of each one.
- unit/property: an independent model with no shared code beyond the
  public id helpers, driven with seeded random streams, valid and
  defective; agreement on the record or the failure class, request
  byte-identical, result shares no container with it; unit rows pin
  every bound at both sides, the precedence between failure classes,
  and hostile values and subclasses. Seeded, so a failure replays.
- fuzz/fault: seeded campaign with the T0329 reference as the
  differential oracle; valid cases mutated at every reachable path
  with hostile values (armed objects whose every hook fails the test,
  attribute bombs, lying dicts, cycles, deep and wide values,
  oversize ints under a lowered interpreter int-digit limit), keys
  dropped, added and replaced, faults injected into the digest step;
  a rejected call leaves its input structurally identical under a
  hook-free iterative snapshot; production never runs caller code; a
  late fault leaves the input untouched and the next call clean.
- integration/restart: Burying appends one entry to the dead-letter log, so the durable value is
the log of entry ids; a restart must reproduce the same entry for the
same job and never a new one. A restart is modelled the
  way a host would - canonical state file under tmp_path, atomic
  replace, the production module executed anew in its own namespace
  (never reloaded), a few cut points in a real child interpreter;
  sampled cut points (12 every-step streams, 10 single random cuts,
  child-interpreter steps) give the same outcomes and the same
  durable value as the uninterrupted run and as the reference.
- instrument: the DeadLetterTracer is an opt-in, content-free wrapper over
  bury, not wired into any shipping path; the trace holds
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

The dead_letter engine takes no caller callback, so there is no
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
