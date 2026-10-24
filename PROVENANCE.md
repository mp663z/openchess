# Provenance classification (independent byte-level review, 2026-10-25 IST)
Archive: openchess-t0353-recovery-tooling-20261024.tar.gz, sha256 7536e0d7998f7e56f8447ac32ed9c49df6c76da88cf577f9ce137f829581fb00 (matches sender-reported hash).
Method: the payload was reconstructed after a workspace loss from the recorded write/edit history of the original work session. This review re-derived files byte-for-byte from that same recorded history (heredoc bodies and patch scripts) and compared hashes, and compared stale candidates against the final content that landed on main. An earlier size-based classification (recorded event lengths vs file bytes) was withdrawn: logged event lengths are escaped command text, not file bytes.

## VERIFIED-VERBATIM (byte-proven against an independent source)
- tests/test_t0352_restart_fuzz_fault.py.recovered (30,640 B) - byte-identical to this repository's history, commits 10b3652 and a771e50 (ac12086 is an older 25,730 B revision).
- iprobe.py, cls.py, isprobe.py, STATE.md, commit2.sh, commit3.sh, commit4.sh, commit5.sh, commit6.sh - byte-identical to their recorded creation bodies.
- gen4/extra_dead.txt (4,964 B) - byte-identical to replay of its recorded creation plus append.
- gen4/extra_restart.txt (5,175 B) - byte-identical to replay: recorded head literal + the blk block from test_t0352 @ 10b3652 (public git history) + recorded identifier-grammar append.

## VERIFIED-FINAL via landed-document parity
- gen4/docs4.py (2,034 B) - differs from its recorded creation (1,686 B); its template paragraphs match all four landed requirements docs on main (T0317/T0326/T0335/T0344.md). The edit event itself was not located, but the landed docs are ground truth for the final template.

## STALE (pre-edit versions; final content publicly recoverable from main)
- gen4/m317 - pre-edit; final text landed in docs/requirements/T0317.md.
- gen4/m326 - pre-edit; final text landed in docs/requirements/T0326.md.
- gen4/m335 - creation only (byte-verified as such); final "(9 sites, ...)" text landed in docs/requirements/T0335.md.
- gen4/extra_progress.txt (4,127 B) - missing the trailing-newlines edit; that content landed in tests/test_t0326_progress_integration_restart.py (which contains test_trailing_newlines_in_identifiers_are_refused_like_the_oracle).
- gen4/gen4.py and gen4/gen4.py.bak (both 32,362 B, identical) - patch-replay missing late cleanup/split patches and a docstring rephrase. Under-generates the limit task (8 test functions vs 15 landed) and the progress task (11 vs 12, via the stale extra_progress.txt above). Retry and dead-letter outputs match landed function counts. See restart-path equivalence below for the load-bearing check.
- Pattern: the recovery systematically missed several late edit events. The m* files are historical evidence narratives for already-landed tasks and carry no weight for T0353; their finals are on main.

## RESTART-PATH EQUIVALENCE (load-bearing for T0353)
- This archive's tooling (gen4/gen4.py + gen4/extra_restart.txt + extra_progress/extra_dead) was executed during review. It regenerates test_t0353_restart_integration_restart.py with the same 16 test functions (names identical, verified 1:1) and the same 43 collected tests as cp2/test_t0353_restart_integration_restart.py.
- The only deltas between this archive's regenerated restart file and CP2 are: cosmetic line wrapping, and IDS = ('state_id', 'restart_id') narrowed to IDS = ("restart_id",). That narrowing is NOT represented in this archive; it originates from a later change. Because both ids are content-addressed digests rather than uniqueness tokens, the narrowing needs justification from generated-stream semantics and independent exact-head review before any landing; a passing uniqueness probe alone is not sufficient.

## CP2 (regenerated T0353 candidate, added to this backup 2026-10-25)
- cp2/test_t0353_restart_integration_restart.py (20,757 B), sha256 775000be43314cbf746e21c104f6a763e0e110575642ee356c0b4c54bd345e9e (matches sender-reported hash; cp2/SHA256SUMS.txt).
- 16 test functions, 43 collected tests (12 plain + 31 parametrized rows) - counts verified by inspection. Carries the IDS narrowing above. Reported 43 PASS + lint clean by its author; not independently executed here. NOT landed, not main-ready.

## UNKNOWN (no reliable recorded original to compare against)
- gate.sh, mkwt.sh, ord.sh, probe.py, rf.sh, gen4/m344
- all gen2/* (mut.py, mut_all.py, mut_arith.py, mut_extra.py, mut_lit.py, mutdoc.py, prod.py, bind.py, udoc.py, launch.sh, req.sh, runtime.tmpl, m333/m342/m351, r333/r342/r351, u333/u342/u351, doc323/doc332/doc341/doc350)
- all gen3/* (gen.py, body.py, docs.py, lane.sh, m316/m325/m334/m343/m352)

## NON-PAYLOAD
- gen4/.ruff_cache/* - cache files, ignore
- The archive's original SHA256SUMS.txt was defective (self-referential entry); excluded here. SHA256SUMS.corrected.txt covers the 61 payload files and verifies 61/61 PASS.

## Review guidance
Independent reviewers should re-run the probes and re-derive evidence from this tooling rather than trusting archived outputs. For T0353 the restart path is covered by the equivalence check above; the open gate item is the IDS narrowing justification plus the standard exact-head dual verifier PASS and CI 2/2.
