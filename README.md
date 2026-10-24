# Recovery tooling backup - UNVERIFIED, not main-ready

Purpose: durable off-workspace preservation of the test-generation and mutation-testing tooling recovered after a workspace loss during the T0353 (jobs_restart integration tests) work, plus the regenerated T0353 test candidate (cp2/). This branch is a backup only. Nothing here has passed the landing gates; main remains fast-forward-only after independent verification and full CI.

Date: 2026-10-25 (IST).

## Contents
- openchess-t0353-recovery-tooling-20261024.tar.gz - the original recovery archive, sha256 7536e0d7998f7e56f8447ac32ed9c49df6c76da88cf577f9ce137f829581fb00 (matches the sender-reported hash).
- cp1/ - extracted payload (61 files; the archive's self-referential SHA256SUMS.txt is excluded as defective - it cannot hash itself).
- SHA256SUMS.corrected.txt - corrected manifest over the 61 payload files; verified 61/61 PASS at backup time.
- cp2/ - regenerated T0353 test candidate with its own manifest.
- PROVENANCE.md - per-file provenance classification (byte-level) and review guidance.

## Verification state
- Verified: the archive hash above; the 61-file payload manifest; tests/test_t0352_restart_fuzz_fault.py.recovered byte-matches this repository's own history (commits 10b3652, a771e50); 11 further payload files byte-verified verbatim against the recorded work history (probes, STATE.md, commit scripts, extra_dead/extra_restart), gen4/m335 byte-verified as its creation version, gen4/docs4.py verified final via landed-document parity; the archive's tooling regenerates the same 16 test functions / 43 collected tests for T0353 as the cp2/ candidate, differing only in formatting and the IDS tuple.
- Stale-but-final-on-main: gen4/m317, gen4/m326, gen4/m335, gen4/extra_progress.txt, and gen4/gen4.py itself miss late edits whose final content landed on main in the T0317/T0326/T0335/T0344 test files and requirements docs (see PROVENANCE.md).
- Open: the cp2/ candidate narrows restart receipt ids to restart_id alone. Both ids are content-addressed digests, not uniqueness tokens; that change needs justification from generated-stream semantics and independent exact-head review before any landing.
- Nothing here is evidence of green gates. T0353 remains unlanded.
