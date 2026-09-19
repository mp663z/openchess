# T0058 Chess/turn/independent verify - verification package

Scope: the COMPLETE turn chain (T0050-T0057) on the Python reference
runtime, submitted for independent adversarial review. This package
claims chain-level coherence only - not Rust product runtime parity
and not board completion.

## Chain inventory (merge order)

| Task | Title | Merge SHA on main |
|---|---|---|
| T0050 | Chess/turn/contract | 22073cbc0350648c9f0cd7670d97333056e211b5 |
| T0051 | Chess/turn/fixture | ab6ebc0de7a22e7c3fffa6679199caa8ca229e06 |
| T0052 | Chess/turn/red test | 80e55877b3ed9a34a305e4c5245f9c59c7970791 |
| T0053 | Chess/turn/implement | 94757ebf25a024cec4d0efe412197132237621b6 |
| T0054 | Chess/turn/unit/property | 5b7d499abcc76112e4d6a459e0fc5b120eb97d02 |
| T0055 | Chess/turn/fuzz/fault | 490c778ce0237f109a9b2f9d1d89e2c201d842a6 |
| T0056 | Chess/turn/integration/restart | 426a30af93620935caed46098e7e72ea8fbb6849 |
| T0057 | Chess/turn/instrument | 206b19e446267555d4a7cc69d7183f10112db5e6 |

Every link was reviewed by the independent verifier before merge;
the per-link verdicts (exact reviewed head SHAs, scope statements,
v1/v2/v3 remediation history) are recorded in evidence/T0050.md
through evidence/T0057.md on main. This package submits the chain
inventory and reproducible artifacts only - it does not certify
itself and asserts no authority; the independent verifier's verdict
on this PR is the task's verification.

## Content hashes at review head

- data/contracts/turn.yaml: 2476387626360c3a9e775b2625130d677c3145a31b99636cc0354bdc97a6a17c
- tools/turn_runtime.py: e0e3f1e87cef3099978167d6c2198ba4c746e453d29e7a9322556facfc39361a
- tools/turn_instrument.py: 3c29e2ebd1acb32f6b05d427bd57032f5ad48bf4e087558cca2cb2789486746d
- main at package assembly: 3b99659f149d69b99cb0981508426cd97542b37a

## Full-chain suite result

Command: `python -m pytest tests/test_t0050_turn_contract.py
tests/test_t0051_fixture.py tests/test_t0053_continuity.py
tests/test_t0054_turn_property.py tests/test_t0055_turn_fuzz_fault.py
tests/test_t0056_turn_integration_restart.py
tests/test_t0057_turn_instrument.py tests/test_turn_behavior.py -q`
Build-side run (author environment): 204 passed, 0 failed - python
3.12 sandbox, pytest, PyYAML, ruff.
Independent verifier's own rerun (v1 review): 204 passed - Python
3.10.12, pytest 9.1.1, PyYAML 6.0.3. The two environments are
distinct; both runs are reported separately, not blended.
The T0052 red harness was retired at the T0053 green flip per its
hook (same lifecycle as later chains).

## Coverage summary (per link)

- contract: closed enum, failure classes, normative class-to-code
  mapping, identity/termination rules (T0050, linted at import).
- fixture: 19 reviewed cases, 4 happy / 5 boundary / 8 malformed /
  2 rollback (T0051).
- runtime: contract-derived, provenance-linted, exact error shape
  enforced at construction (T0053; green flip of the red suite).
- unit/property: seeded property battery incl. identity and
  termination properties (T0054).
- fuzz/fault: 3000-case classified campaign, fault injection detected
  by owning checks, class-to-code sibling sweep (T0055).
- integration/restart: 240-move trajectory + fresh-process restart,
  exact identity continuity both sides, malformed persisted states
  classified, rollback survives restart (T0056).
- instrument: TurnTracer strict neutrality (non-dispatching
  structural snapshots), append-only trace, crash family, adversarial
  copy-hook suites (T0057).

## Known environmental note

7 tests in tests/test_install_checks.py fail with Permission errors
on pristine main in this sandbox (environmental, reproduce
identically without any chain change; flagged in prior PR bodies).
