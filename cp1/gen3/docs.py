import sys, re
task, name, title, modname, reftest, refname, unit = sys.argv[1:8]
mut_file = sys.argv[8]
tests_file = sys.argv[9]
txt = open(tests_file).read()
names = re.findall(r"^def (test_\w+)", txt, re.M)
def grp(*keys):
    return [n for n in names if any(k in n for k in keys)]
used=set()
def fmt(ns):
    return ", ".join(f"`{n}`" for n in ns)
sections = [
 ("Differential fuzz (oracle: the contract reference)", grp("bases_are_valid","every_path_of_every","dropping_any","seeded_multi","boundary_value","string_field_boundary","key_order")),
 ("Fault objects and hostile shapes (no caller code, always typed, input untouched)", grp("armed","non_dict_and_hostile","hundred_thousand","huge_ints")),
 ("Injected faults", grp("a_fault_at_any_digest","rejected_requests_never_reach")),
]
rest=[n for n in names if not any(n in s[1] for s in sections)]
sections.append(("Module-specific streams and sweeps", rest))
mapping = "\n".join(f"- {t}: {fmt(ns)}." for t, ns in sections if ns)
out = f"""# {task} {title}

## Normative requirements

1. `tests/test_{task.lower()}_{name}_fuzz_fault.py` proves happy, boundary, malformed and rollback behavior of the production `server.{modname}` under seeded fuzz and injected faults, with the contract's reference implementation (`tests.{reftest}`) as the differential oracle. It adds no production code.
2. Every valid case is mutated at every reachable path with {len(re.findall('lambda', txt.split('HOSTILE_FACTORIES = (')[1].split('NASTY_KEYS')[0]))} hostile value factories (every gated type, boundary ints and strings, armed objects whose every hook fails the test, attribute bombs, lying dicts, cycles, 100000-deep and wide values), keys are dropped, added and replaced by non-str or armed keys, and oversize ints run under a lowered `sys.set_int_max_str_digits`. Production and the reference must return the same record or the same failure class and code, with `retryable` false and no cause or context.
3. A rejected call leaves its input structurally identical, checked with an iterative, hook-free snapshot. An accepted call leaves its request identical. Production never runs caller code: any hook of an armed object fails the test.
4. A fault injected at each sha256 step (the only post-validation step) propagates, leaves the input untouched, and the next call returns the baseline outcome; rejected requests never reach the digest step. Pathological shapes (100000 levels deep, 100000 wide, 5 MB strings, 100000-digit ints) are handled in bounded time without recursion.
5. Module-specific seeded streams are compared with the oracle step by step and checked against independent invariants (see the mapping).

## Requirement-to-test mapping

{mapping}

## Mutation check

{open(mut_file).read().strip()}
"""
open(f"/home/sandbox/openchess/requirements/tasks/{task}.md", "w").write(out)
