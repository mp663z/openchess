# T0067 Chess/castling/independent verify - verification package

Scope: the COMPLETE castling chain (T0059-T0066) on the Python
reference runtime, submitted for independent adversarial review. This
package claims chain-level coherence only - not Rust product runtime
parity and not board completion.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0059 | Chess/castling/contract | ed88e3b5cd524f66930f0274caefb0a2602b6304 |
| T0060 | Chess/castling/fixture | e0b59b19aefdbd090c074e788f899544c72a2c2e |
| T0061 | Chess/castling/red test | e5586942cbcefcac4da965ab30dbc33a5cbf88e6 |
| T0062 | Chess/castling/implement | cc6548544c12cac1e97c7124a884041eb9d3f051 |
| T0063 | Chess/castling/unit/property | 1285410f7920b69cf426d12195c9e0892c2081c9 |
| T0064 | Chess/castling/fuzz/fault | 96f55a3b43fc8dc7f7c833b3a3550b2e78dd4fd4 |
| T0065 | Chess/castling/integration/restart | 140b38862a8ecb8db81594ec34a793d8c386d626 |
| T0066 | Chess/castling/instrument | b94900cd2ebaa5921d8199e2dc0116996f90c604 |

Every link was reviewed by the independent verifier before merge;
the per-link verdicts (exact reviewed head SHAs, scope statements,
v1/v2/v3 remediation history) are recorded in evidence/T0059.md
through evidence/T0066.md on main. This package submits the chain
inventory and reproducible artifacts only - it does not certify
itself and asserts no authority; the independent verifier's verdict
on this PR is the task's verification.

## Content hashes at review head

- data/contracts/castling.yaml: f84a1a257bc99a82344b1244002b5e1f046a0a9bd13d4c7e02400c98c5fdb073
- tools/castling_runtime.py: 76e1fb8f9181dfc974d0720b829ea01bb81918e726a3a638805c62586ecd4b5d
- tools/castling_instrument.py: 75a443b2541675605f2b26d518000ba7e313b637d41f80987af524a20cf4e248
- main at package assembly: 47475565e181de1b49633b8fc524c1c90a56b616

## Full-chain suite result

Command: `python -m pytest tests/test_t0059_castling_contract.py
tests/test_t0060_castling_fixture.py tests/test_castling_behavior.py
tests/test_t0062_continuity.py tests/test_t0063_castling_property.py
tests/test_t0064_castling_fuzz_fault.py
tests/test_t0065_castling_integration_restart.py
tests/test_t0066_castling_instrument.py -q`
Build-side run (author environment): 126 passed, 0 failed - python
3.12 sandbox, pytest, PyYAML, ruff. The independent verifier's own
rerun environment is reported in its verdict separately; the two are
not blended.
The T0061 red harness was retired at the T0062 green flip per its
hook (same lifecycle as the turn chain).

## Coverage summary (per link)

- contract: closed enum, 5 failure classes, normative class-to-code
  mapping, rights grammar/identity/turn-linkage rules (T0059, linted
  at import).
- fixture: reviewed happy/boundary/malformed/rollback cases with byte
  pins (T0060).
- runtime: contract-derived, provenance-linted, exact error shape
  enforced at construction, pinned structural public-API choices
  (T0062; green flip of the red suite + continuity pins).
- unit/property: seeded property battery + 600-case 16-category
  fail-closed fuzz + 72-row structural sibling matrix + mutant
  replays (T0063).
- fuzz/fault: 2500-case classified campaign over 16 categories,
  guaranteed-invalidity oracle mirroring runtime structural validity,
  6 owned fault injections, class-to-code sweep, 36-row boundary
  corpus, turn_effect fuzz (T0064).
- integration/restart: 120-event adaptive trajectory + fresh-process
  restart, castle lifecycle across the boundary, exact identity
  continuity, malformed persisted states classified, rollback
  survives restart (T0065).
- instrument: CastlingTracer strict neutrality (non-dispatching
  structural snapshots), append-only trace, crash family, adversarial
  copy-hook suites (T0066).

## Known environmental note

7 tests in tests/test_install_checks.py fail with Permission errors
on pristine main in this sandbox (environmental, reproduce
identically without any chain change; flagged in prior PR bodies).
