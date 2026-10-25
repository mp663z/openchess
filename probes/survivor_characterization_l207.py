import sys, importlib.util
BASE = "/tmp/l135probe"
def load(name, repo):
    for mod in list(sys.modules):
        if mod == "tools" or mod.startswith("tools."): del sys.modules[mod]
    sys.path.insert(0, repo)
    spec = importlib.util.spec_from_file_location(name, repo + "/server/jobs_restart.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    sys.path.pop(0); return m
o = load("o", BASE+"/repo-orig"); m = load("m207", BASE+"/repo-mut207")
cases = [
  ("non-ascii worker field", {"op":"restart","worker":"w1","note":"héllo","ts":1}),
  ("plus field",             {"op":"restart","worker":"w1","note":"a+b","ts":1}),
  ("ascii only",             {"op":"restart","worker":"w1","note":"abc","ts":1}),
]
for label, rec in cases:
    ro, rm = o._restart_id(rec), m._restart_id(rec)
    print(f"{label:24s} {'DISTINGUISHES' if ro!=rm else 'same'}  orig={ro[:26]} mut={rm[:26]}")
# surrogate edge: ensure_ascii=True escapes lone surrogates in json, so utf-8 encode may not raise here
rec = {"op":"restart","worker":"w1","note":"\ud800","ts":1}
try:
    ro = o._restart_id(rec); rm = m._restart_id(rec)
    print(f"{'lone-surrogate field':24s} {'DISTINGUISHES' if ro!=rm else 'same'}  orig={ro[:26]} mut={rm[:26]} (json ensure_ascii escapes it, so no raise)")
except Exception as e:
    print("raise:", type(e).__name__, e)
