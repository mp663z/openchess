# T0353 mutation-sweep survivor characterization (independent)

Sweep: 149 unique mutants on pinned source `server/jobs_restart.py`
sha256 `5d1559f8492522c5a3a36550fdb224601b8e6bd61d561c0551c1692a92815959`
(hash re-verified before every probe). Each mutant = single-line edit in an
otherwise byte-identical repo copy; orig and mutant loaded side-by-side and
compared on candidate distinguishing inputs. Author-run tallies (126 killed /
17 crash / 6 survivor) independently re-checked for row continuity and hash;
functional verdicts below are from my own probes, not the author's.

Final census: 5 non-equivalent coverage gaps (killable) + 1 equivalent.

| Line | Mutation | Verdict | Distinguishing input | Kill test |
|---|---|---|---|---|
| 54  | MAX_BITS 13288 -> 13289 | EQUIVALENT | none exists: every int with bit_length 13289 is >= 2^13288 (4001 digits > MAX_DIGITS=4000), so the digit check rejects exactly what the relaxed pre-screen admits; boundary-confirmed (2^13288, 2^13289-1, negatives, 10^4000 +/-1) | none (document as equivalent; a test pinning the constant is not behavioral) |
| 74  | _canon_receipt ensure_ascii True -> False | NON-EQUIVALENT | receipt field "héllo" or "a+b" -> different canonical bytes | assert canonical bytes / rst1 for non-ASCII receipt field |
| 113 | _scalar_ok encode utf-8 -> utf-7 | NON-EQUIVALENT | lone surrogate "퐀" -> utf-8 raises, utf-7 encodes b'+2AA-'; mutant accepts malformed strings | assert lone-surrogate payload rejected |
| 135 | dict-key guard or -> and | NON-EQUIVALENT | {5:"x"}, {1.5:"x"}, {True:"x"}, {None:"x"}, nested {"a":{1:2}} -> orig rejects, mutant accepts | assert int-keyed payload rejected |
| 203 | _state_id encode utf-8 -> utf-7 | NON-EQUIVALENT | payload "héllo" or "a+b" -> different qs1 hash (orig qs1:0e66e276 vs mut qs1:d6949c14) | assert qs1 for non-ASCII/'+' payload state |
| 207 | _restart_id encode utf-8 -> utf-7 | NON-EQUIVALENT | receipt field "héllo" or "a+b" -> different rst1 (orig rst1:e5e50423 vs mut rst1:79617b4c); lone-surrogate field also differs (json ensure_ascii escapes it, so no raise) | assert rst1 for non-ASCII receipt field |

Codec fact base (Python 3.10.12): utf-7 encodes lone surrogates without error
(b'+2AA-'), encodes non-ASCII differently (h+AOk-llo), and escapes '+' as '+-';
utf-8 raises on lone surrogates and keeps ASCII bytes unchanged. utf-7 is not a
safe utf-8 stand-in for validation, canonicalization, or hashing.

Artifacts: survivor_characterization.py (+ _output.txt) covers L54/74/113/135/203
end-to-end; survivor_characterization_l207.py (+ _output.txt) covers L207.
Pins: prod 5d1559f8..., test 775000be... (CP2), mutator 77945999... (v2),
git head d5487a9a (base b91b6896).
