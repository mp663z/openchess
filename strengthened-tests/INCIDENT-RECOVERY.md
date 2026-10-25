# mut_extra incident recovery - owner adjudication (2026-10-26 ~01:23 IST)

Incident (author-reported, owner-adjudicated): Lane B ran `git checkout server/`
in its sweep worktree at ~01:15:24 while mut_extra had a mutant applied. The
battery's _fresh() re-reads the file per case, so the live cycle could read
restored original content. Lane B withdrew its initial "exactly row 43" scope
proof: the live-cycle identity is unrecoverable from surviving timestamps/pids.

Immutable artifact: progress/rex-incident-61-bcbf879b.tsv
sha256 ad3a2ac9d6a24ba1a2880874b4df6f4cb4b3555634fc1aa7bd8dcd00ad96e804 (matches relay).
61 rows. Rows 1-25 byte-identical to pre-incident cp25 (relayed 00:45, before the
01:15 incident): provably clean. At-risk range: rows 26-61.

Scope argument (evidence-backed, not functional convenience):
1. Interference mode: restoring ORIGINAL content. The original passes the
   strengthened battery by definition, so this interference can only manufacture
   FALSE SURVIVORS. It cannot manufacture a kill or crash verdict.
2. At-risk survivors: exactly rows 26 (L102), 27 (L106), 43 (L136).
3. Rows 26/27 independently proven EQUIVALENT by owner control-flow proof +
   boundary probes on pinned source (5d1559f8):
   - L102 (init ok=False->True in int branch): dead store; try body reassigns ok
     unconditionally whenever reached; except only fires on ValueError.
   - L106 (except ok=False->True): except branch unreachable under the MAX_BITS
     guard - bit_length <= 13288 implies <= 4001 digits, below CPython's default
     4300-digit str limit that is the only ValueError source. 0 diffs on boundary
     set {2^13288, +/-, 10^4000+/-1, 2^13289, 10^5000, 10^4300, 10^4301, 5, 0, -1,
     True, None, 1.5, "x"}. Caveat for docs: equivalence assumes the default
     sys.get_int_max_str_digits()=4300; a deployment lowering it below 4001 makes
     the except reachable and L106 non-equivalent (verified: lowered-limit 3000,
     3500-digit int -> orig False, mut True).
   Recorded survivor verdicts for 26/27 are CORRECT regardless of contamination.
4. Row 43 (L136, dict-key guard "return False"->"return True") clean owner re-run
   on a fresh pinned-prod copy + strengthened battery deeac902: KILLED
   (test_payloads_with_non_str_scalar_keys_are_refused + oracle subclass test;
   2 failed / 45 passed). The recorded "survivor" was the contamination artifact.
5. Boundary rows reproduced by owner clean re-run: row 42 (L135 isinstance-misuse)
   -> TypeError "isinstance expected 2 arguments, got 1" = recorded crash class;
   row 44 (L139 "for value not in") -> SyntaxError at line 139 = recorded killed
   class (author col5 shows the SyntaxError file/line; classification mapping
   verified: crash = non-assertion runtime error, killed = assertion/syntax).

Adjudication: exactly one invalid artifact row (43). Corrected verdict: KILLED.
Rows 26-61 otherwise stand as author-run; rows 1-25 provably pre-incident.
The contaminated TSV stays immutable; the final mut_extra TSV must carry row 43's
corrected verdict with explicit provenance (incident, owner re-run, this commit).

Also noted: Lane B's corrected process history - the 00:46->01:05 gap was the
sweep process DOWN ~19 min (parent-restarted 01:05:02, resume clean), not a
spin-hang; its earlier "hung pytest child" reading was a pid-recycling misread.
