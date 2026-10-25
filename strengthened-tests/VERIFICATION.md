# Independent verification of Lane B strengthened-test commit 5960a36a (run 2026-10-26 00:07 IST)

Artifacts (sha256, all match relayed values):
- test file: deeac9029138106f9ebff48bcf6cc5435a0c32757482f621cb29f1902df80d85
- patch:     f7d41819b1236cd216a9d781d738a7af699aa64e94b3500009408410a2a102f1
- bundle:    d216391e2809d3f486ee54201ce348f07e740bd2f337e24c247362bca1d72464

Bundle: HEAD 5960a36a02c9ce8549f1d9d745e7400fef9a787f, parent
d5487a9a043831bd8ac662e877343d59879fae2f (base b91b689607a80df00b01886356218347633571d3).
Diff vs d5487a9: tests/test_t0353_restart_integration_restart.py only, +54 lines, tests-only.
Production source in bundle: server/jobs_restart.py sha256 5d1559f8... = pinned prod, unchanged.

Independent runs (pytest 9.1.1, full 47-case battery, new test file over pinned prod copies):
- orig (pinned prod):       47 passed
- mut54 (L54 MAX_BITS+1):   47 passed  (equivalence discrimination holds - suite does not kill the equivalent mutant)
- mut74 (ensure_ascii):     1 failed (test_restart_id_pinned_for_non_ascii_and_plus_receipt_fields), 46 passed
- mut113 (utf-7 scalar):    1 failed (test_payloads_with_lone_surrogate_strings_are_refused), 46 passed
- mut135 (dict-key guard):  1 failed (test_payloads_with_non_str_scalar_keys_are_refused), 46 passed
- mut203 (utf-7 state id):  1 failed (test_state_id_pinned_for_non_ascii_and_plus_payload_state), 46 passed
- mut207 (utf-7 restart id):1 failed (test_restart_id_pinned_for_non_ascii_and_plus_receipt_fields), 46 passed

Each of the 5 killable mutants is killed by exactly its targeted test; no collateral
failures; equivalent mutant survives. Author claim "all 5 target single kills, 47 green"
independently confirmed.
