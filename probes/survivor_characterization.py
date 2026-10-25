#!/usr/bin/env python3
"""T0353 survivor characterization probes - run 2026-10-25 ~23:15 IST.
Pinned source: server/jobs_restart.py sha256 5d1559f8492522c5a3a36550fdb224601b8e6bd61d561c0551c1692a92815959
(verified before probing). Each mutant is a single-line edit in an otherwise
byte-identical repo copy. Loads orig + mutant and compares function outputs
on candidate distinguishing inputs.
Repos used: repo-orig (pristine pinned clone), repo-mut54/74/113/135/203 (single-line edits).
"""
import sys, importlib.util, math

BASE = "/tmp/l135probe"
def load(name, repo):
    for mod in list(sys.modules):
        if mod == "tools" or mod.startswith("tools."): del sys.modules[mod]
    sys.path.insert(0, repo)
    spec = importlib.util.spec_from_file_location(name, repo + "/server/jobs_restart.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.path.pop(0)
    return m

o    = load("o",    BASE + "/repo-orig")
m54  = load("m54",  BASE + "/repo-mut54")    # MAX_BITS 13288 -> 13289
m74  = load("m74",  BASE + "/repo-mut74")    # _canon_receipt ensure_ascii True -> False
m113 = load("m113", BASE + "/repo-mut113")   # _scalar_ok encode utf-8 -> utf-7
m135 = load("m135", BASE + "/repo-mut135")   # dict-key guard or -> and  (repo-mut from 10:41 probe)
m203 = load("m203", BASE + "/repo-mut203")   # _state_id encode utf-8 -> utf-7

print("=== L54 MAX_BITS+1 (pre-screen) ===")
print("digits(2^13288) =", len(str(2**13288)), "vs MAX_DIGITS = 4000; ceil(4000*log2(10)) =", math.ceil(4000*math.log2(10)))
for label, n in [("2^13288 (bl 13289)", 2**13288), ("2^13289-1 (bl 13289)", 2**13289-1),
                 ("-(2^13288)", -(2**13288)), ("10^4000 (4001 digits)", 10**4000),
                 ("10^4000-1 (4000 digits)", 10**4000-1), ("2^13288-1 (bl 13288)", 2**13288-1), ("5", 5)]:
    ro, rm = o._scalar_ok(n), m54._scalar_ok(n)
    print(f"  {label:28s} orig={ro} mut={rm} {'DISTINGUISHES' if ro!=rm else 'same'}")
print("  VERDICT: no distinguishing input exists - every int with bit_length 13289 has >=4001 digits,")
print("  so the digit check rejects exactly what the relaxed pre-screen admits. EQUIVALENT (domain: all ints).")

print("=== L74 _canon_receipt ensure_ascii True->False ===")
for label, rec in [("non-ascii field", {"op":"restart","worker":"w1","note":"héllo"}),
                   ("plus field", {"op":"restart","worker":"w1","note":"a+b"}),
                   ("ascii only", {"op":"restart","worker":"w1","note":"abc"})]:
    ro, rm = o._canon_receipt(rec), m74._canon_receipt(rec)
    print(f"  {label:18s} {'DISTINGUISHES' if ro!=rm else 'same'}  orig={ro!r} mut={rm!r}")
print("  VERDICT: NON-EQUIVALENT - non-ASCII receipt fields change canonical bytes and restart-id hash.")

print("=== L113 _scalar_ok encode utf-8 -> utf-7 ===")
for label, s in [("lone surrogate \\ud800", "\ud800"), ("lone surrogate \\udc00", "\udc00"),
                 ("non-ascii 'héllo'", "héllo"), ("plus 'a+b'", "a+b"), ("plain 'abc'", "abc")]:
    ro, rm = o._scalar_ok(s), m113._scalar_ok(s)
    print(f"  {label:26s} orig={ro} mut={rm} {'DISTINGUISHES' if ro!=rm else 'same'}")
print("  VERDICT: NON-EQUIVALENT - utf-7 encodes lone surrogates (no error) where utf-8 raises;")
print("  mutant accepts malformed surrogate strings the original rejects. Validation bypass.")

print("=== L135 dict-key guard or -> and ===")
for label, payload in [("{5:'x'} int key", {5:"x"}), ("{1.5:'x'} float key", {1.5:"x"}),
                       ("{True:'x'} bool key", {True:"x"}), ("{None:'x'}", {None:"x"}),
                       ("{'a':{1:2}} nested int key", {"a":{1:2}}), ("{(1,2):'x'} tuple key", {(1,2):"x"}),
                       ("{10**5000:'x'} huge-int key", {10**5000:"x"}), ("{'a':'x'} str key", {"a":"x"})]:
    ro, rm = o._payload_ok(payload, set()), m135._payload_ok(payload, set())
    print(f"  {label:30s} orig={ro} mut={rm} {'DISTINGUISHES' if ro!=rm else 'same'}")
print("  VERDICT: NON-EQUIVALENT - non-str scalar keys (int/float/bool/None, incl. nested) pass the mutant.")

print("=== L203 _state_id encode utf-8 -> utf-7 ===")
states = {
  "payload non-ascii": {"jobs":[{"job_id":"j1","status":"queued","attempts":0,"seq":0,"payload":{"text":"héllo"}}]},
  "payload plus":      {"jobs":[{"job_id":"j1","status":"queued","attempts":0,"seq":0,"payload":{"text":"a+b"}}]},
  "payload ascii":     {"jobs":[{"job_id":"j1","status":"queued","attempts":0,"seq":0,"payload":{"text":"abc"}}]},
  "empty":             {"jobs":[]},
}
for label, st in states.items():
    ro, rm = o._state_id(st), m203._state_id(st)
    print(f"  {label:20s} {'DISTINGUISHES' if ro!=rm else 'same'}  orig={ro[:24]} mut={rm[:24]}")
print("  VERDICT: NON-EQUIVALENT - non-ASCII or '+' payload content changes the qs1 queue-state id;")
print("  same logical queue hashes differently (dedup/idempotency break). utf-7 also differs on '+'.")

print()
print("utf-7 vs utf-8 codec edge behavior (Python", sys.version.split()[0] + "):")
for label, s in [("lone high surrogate", "\ud800"), ("lone low surrogate", "\udc00"),
                 ("paired U+1D11E", "\U0001D11E"), ("non-ascii 'héllo'", "héllo"), ("plus 'a+b'", "a+b"), ("ascii", "abc")]:
    row = []
    for enc in ("utf-8", "utf-7"):
        try: row.append(f"{enc}={s.encode(enc)!r}")
        except UnicodeEncodeError as e: row.append(f"{enc}=RAISES")
    print(f"  {label:22s} {row[0]} | {row[1]}")
