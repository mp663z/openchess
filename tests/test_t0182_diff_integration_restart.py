"""T0182: diff integration/restart - the production graph-state diff
(graph/diff.py) composed end-to-end and across a REAL process restart.

Integration: a seeded trajectory of graph states S0..Sn over linked node
records (graph.node.make_record, keyed by graph.node.record_identity).
Every step computes diff(Si, Si+1) and checks it against an independent
model of data/contracts/diff.yaml (sections and the gs1 state-id
derivation are restated here, never taken from production), validates
it, applies it to Si and must land exactly on Si+1; chaining every diff
from S0 reaches Sn. The trajectory is pinned (state ids + SHA-256).

Restart: the states and diffs are persisted as JSON; a FRESH python
process imports graph.diff cold, recomputes the remaining diffs and
applies them. The restarted run must be bit-identical to the
uninterrupted one: states, state ids and diffs survive exactly, in
canonical key order, regardless of the persisted key order.
Malformed persisted diffs and bases are rejected IN THE FRESH PROCESS
with the declared failure class and mapped code (never a traceback).
A rejected apply before the restart leaves no trace in the persisted
base (rollback survives the restart).
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from graph import diff
from graph.node import make_record, record_identity

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "diff.yaml").read_text())["contract"]
MAPPING = CONTRACT["failures"]["mapping"]
MDR, CB, UI, DT = (
    "malformed_diff_record",
    "conflicting_base",
    "unknown_identity",
    "divergent_target",
)
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
KEYS = tuple(record_identity(r) for r in RECORDS)
POOL = dict(zip(KEYS, RECORDS, strict=True))
STEPS = 24

CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from graph import diff
p = json.load(sys.stdin)
out = []
try:
    state = p["base"]
    for item in p["steps"]:
        if "validate" in item:  # the persisted diff alone, no base involved
            diff.validate_diff(item["validate"])
            out.append({"validated": True})
            continue
        if "state_id_of" in item:  # a persisted state read back on its own
            out.append({"state_id": diff.state_id(item["state_id_of"])})
            continue
        if "target" in item:
            d = diff.compute(state, item["target"])
        else:
            d = item["diff"]
        state = diff.apply(d, state)
        out.append({"diff": d, "state": state, "state_id": diff.state_id(state)})
    print(json.dumps({"ok": True, "out": out}))
except diff.DiffError as e:
    print(json.dumps({"ok": False, "failure_class": e.failure_class, "code": e.code,
                      "done": len(out)}))
except BaseException as e:
    print(json.dumps({"ok": False, "crash": type(e).__name__}))
"""


def _child(payload):
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# -- independent model ------------------------------------------------------------------


def _model_id(state):
    parts = []
    for key in sorted(state):
        rec = state[key]
        body = "|".join(f"{f}={rec[f]}" for f in sorted(rec))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256("".join(parts).encode()).hexdigest()


def _model_diff(base, target):
    return {
        "base_id": _model_id(base),
        "target_id": _model_id(target),
        "added": {k: target[k] for k in sorted(target) if k not in base},
        "removed": {k: base[k] for k in sorted(base) if k not in target},
        "changed": {},
    }


def _state(keys):
    return {k: copy.deepcopy(POOL[k]) for k in sorted(keys)}


def _trajectory(seed=0, steps=STEPS):
    rng = random.Random(seed)
    keys = set(rng.sample(KEYS, 3))
    states = [_state(keys)]
    for _ in range(steps):
        op = rng.randrange(4)
        if op == 0 and len(keys) < len(KEYS):
            keys.add(rng.choice(sorted(set(KEYS) - keys)))
        elif op == 1 and keys:
            keys.discard(rng.choice(sorted(keys)))
        elif op == 2:  # swap: one out, one in
            if keys:
                keys.discard(rng.choice(sorted(keys)))
            keys.add(rng.choice(sorted(set(KEYS) - keys)))
        # op 3: no change (empty diff)
        states.append(_state(keys))
    return states


def _dump(obj):
    return json.dumps(obj, sort_keys=False, separators=(",", ":"))


# -- integration --------------------------------------------------------------------------


def test_integration_trajectory_matches_model_and_is_pinned():
    states = _trajectory()
    kinds = set()
    state = copy.deepcopy(states[0])
    log = []
    for a, b in zip(states, states[1:], strict=False):
        d = diff.compute(a, b)
        assert d == _model_diff(a, b)
        assert list(d) == ["base_id", "target_id", "added", "removed", "changed"]
        for section in ("added", "removed"):
            assert list(d[section]) == sorted(d[section])
        diff.validate_diff(d)
        snap = copy.deepcopy(a)
        assert diff.apply(d, a) == b and a == snap
        assert diff.state_id(b) == _model_id(b)
        state = diff.apply(d, state)
        kinds.add(("add" if d["added"] else "") + ("rm" if d["removed"] else "") or "empty")
        log.append(d["target_id"])
    assert state == states[-1]
    assert {"add", "rm", "addrm", "empty"} <= kinds
    assert hashlib.sha256("\n".join(log).encode()).hexdigest() == PINNED_TRAJECTORY_SHA256


PINNED_TRAJECTORY_SHA256 = "fad66076638acbad4712bdb78be9ac9d3cd60cdb0db03ba157acbc7700242adf"


def test_integration_diff_composition_and_inverse():
    states = _trajectory(seed=5)
    for a, b in zip(states, states[1:], strict=False):
        forward, back = diff.compute(a, b), diff.compute(b, a)
        assert diff.apply(back, diff.apply(forward, a)) == a
        assert forward["added"] == back["removed"] and forward["removed"] == back["added"]
    direct = diff.compute(states[0], states[-1])
    assert diff.apply(direct, states[0]) == states[-1]


# -- restart ----------------------------------------------------------------------------


def test_restart_continuity_bit_identical():
    states = _trajectory()
    cut = len(states) // 2
    uninterrupted = [diff.compute(a, b) for a, b in zip(states, states[1:], strict=False)]
    base = json.loads(_dump(states[cut]))
    payload = {"base": base, "steps": [{"target": s} for s in states[cut + 1 :]]}
    got = _child(payload)
    assert got["ok"], got
    assert len(got["out"]) == len(states) - cut - 1
    for i, item in enumerate(got["out"]):
        assert item["diff"] == uninterrupted[cut + i]
        assert _dump(item["diff"]) == _dump(uninterrupted[cut + i])  # canonical key order
        assert item["state"] == states[cut + 1 + i]
        assert item["state_id"] == diff.state_id(states[cut + 1 + i]) == _model_id(item["state"])


def test_restart_persisted_key_order_never_changes_ids():
    rng = random.Random(9)
    for state in _trajectory(seed=9):
        items = list(state.items())
        rng.shuffle(items)
        shuffled = json.loads(_dump(dict(items)))
        got = _child({"base": shuffled, "steps": [{"target": state}]})
        assert got["ok"], got
        assert got["out"][0]["state_id"] == diff.state_id(state)
        assert got["out"][0]["diff"]["base_id"] == got["out"][0]["diff"]["target_id"]


def test_restart_applies_parent_persisted_diffs_exactly():
    states = _trajectory(seed=3)
    diffs = [diff.compute(a, b) for a, b in zip(states, states[1:], strict=False)]
    got = _child({"base": states[0], "steps": [{"diff": json.loads(_dump(d))} for d in diffs]})
    assert got["ok"], got
    assert [o["state"] for o in got["out"]] == states[1:]


# -- malformed persisted input rejected in the fresh process --------------------------


def _one_step():
    states = _trajectory(seed=11)
    for a, b in zip(states, states[1:], strict=False):
        d = diff.compute(a, b)
        if d["added"] and d["removed"] and set(a) - set(d["removed"]):
            return a, b, d
    raise AssertionError("no add+remove+keep step")


def _mut_base_id(a, d):
    d["base_id"] = "gs1:" + "0" * 64
    return a, d


def _mut_removed_record(a, d):
    k = next(iter(d["removed"]))
    other = next(r for r in RECORDS if r != d["removed"][k])
    d["removed"][k] = dict(other)
    return a, d


def _mut_added_present(a, d):
    k = next(iter(d["added"]))
    a = {**a, k: POOL[k]}
    d["base_id"] = _model_id(a)
    return a, d


def _mut_removed_absent(a, d):
    k = next(iter(d["removed"]))
    a = {x: v for x, v in a.items() if x != k}
    d["base_id"] = _model_id(a)
    return a, d


def _mut_target_id(a, d):
    d["target_id"] = "gs1:" + "f" * 64
    return a, d


def _mut_digest(a, d):
    k = next(iter(d["added"]))
    d["added"][k] = {**d["added"][k], "digest": "pdv1:" + "0" * 64}
    return a, d


def _mut_extra_field(a, d):
    d["note"] = "x"
    return a, d


def _mut_clock(a, d):
    k = next(iter(d["added"]))
    d["added"][k] = {**d["added"][k], "snapshot_fen": d["added"][k]["snapshot_fen"][:-3] + "0 2"}
    return a, d


def _mut_key_mismatch(a, d):
    k = next(iter(d["added"]))
    d["added"] = {k + "x": d["added"][k]}
    return a, d


def _mut_base_state_corrupt(a, d):
    k = next(iter(a))
    a = {**a, k: {**a[k], "variant": "chess960"}}
    return a, d


def _mut_overlap(a, d):
    k = next(iter(d["removed"]))
    d["added"][k] = d["removed"][k]
    return a, d


def _mut_empty_unequal(a, d):
    d["added"], d["removed"] = {}, {}
    return a, d


def _mut_nonempty_equal_ids(a, d):
    d["target_id"] = d["base_id"]
    return a, d


def _kept_key(a, d):
    return next(k for k in a if k not in d["removed"])


def _mut_changed_equal_witness(a, d):
    k = _kept_key(a, d)
    d["changed"] = {k: {"base": dict(a[k]), "target": dict(a[k])}}
    return a, d


def _mut_witness_base_other_identity(a, d):
    # the witness target matches its key; its base carries another identity
    k = _kept_key(a, d)
    other = next(r for key, r in POOL.items() if key != k)
    d["changed"] = {k: {"base": dict(other), "target": dict(a[k])}}
    return a, d


def _mut_witness_target_other_identity(a, d):
    # the mirror: the base matches its key; the target carries another identity
    k = _kept_key(a, d)
    other = next(r for key, r in POOL.items() if key != k)
    d["changed"] = {k: {"base": dict(a[k]), "target": dict(other)}}
    return a, d


def _mut_base_rekeyed(a, d):
    # a valid record re-keyed under another identity's key in the base
    k = _kept_key(a, d)
    other = next(r for key, r in POOL.items() if key != k)
    return {**a, k: dict(other)}, d


MUTATIONS = [
    ("base-id-forged", _mut_base_id, CB),
    # every record field is identity-derived, so a different removed record
    # carries another identity under the key: malformed, never conflicting
    ("removed-record-other-identity", _mut_removed_record, MDR),
    ("added-identity-present", _mut_added_present, CB),
    ("removed-identity-absent", _mut_removed_absent, UI),
    ("target-id-lies", _mut_target_id, DT),
    ("record-digest-foreign", _mut_digest, MDR),
    ("diff-extra-field", _mut_extra_field, MDR),
    ("record-clock-not-normalized", _mut_clock, MDR),
    ("record-key-mismatch", _mut_key_mismatch, MDR),
    ("base-state-corrupt", _mut_base_state_corrupt, MDR),
    ("sections-overlap", _mut_overlap, MDR),
    ("empty-diff-unequal-ids", _mut_empty_unequal, DT),
    ("nonempty-diff-equal-ids", _mut_nonempty_equal_ids, MDR),
    # a changed witness whose base and target are the same record
    ("changed-witness-equal", _mut_changed_equal_witness, MDR),
    ("changed-witness-base-other-identity", _mut_witness_base_other_identity, MDR),
    ("changed-witness-target-other-identity", _mut_witness_target_other_identity, MDR),
    ("base-record-rekeyed", _mut_base_rekeyed, MDR),
]

# rows whose class the persisted diff decides on its own (validate_diff,
# no base): the empty/non-empty vs id boundary and the witness rule
DIFF_ONLY = {
    "empty-diff-unequal-ids",
    "nonempty-diff-equal-ids",
    "changed-witness-equal",
    "changed-witness-base-other-identity",
    "changed-witness-target-other-identity",
}


@pytest.mark.parametrize("name", sorted(DIFF_ONLY))
def test_persisted_diff_boundary_decided_by_validation_in_fresh_process(name):
    mutate, cls = next((m, c) for n, m, c in MUTATIONS if n == name)
    a, _b, d = _one_step()
    _base, bad = mutate(json.loads(_dump(a)), json.loads(_dump(d)))
    got = _child({"base": {}, "steps": [{"validate": bad}]})
    assert got == {"ok": False, "failure_class": cls, "code": MAPPING[cls], "done": 0}


def test_persisted_malformed_state_rejected_by_state_id_in_fresh_process():
    a, _b, d = _one_step()
    base, _d = _mut_base_rekeyed(json.loads(_dump(a)), json.loads(_dump(d)))
    snap = _dump(base)
    got = _child({"base": {}, "steps": [{"state_id_of": base}]})
    assert got == {"ok": False, "failure_class": MDR, "code": MAPPING[MDR], "done": 0}
    with pytest.raises(diff.DiffError) as err:
        diff.state_id(base)
    assert err.value.failure_class == MDR
    assert _dump(base) == snap


@pytest.mark.parametrize("name,mutate,cls", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_malformed_persisted_input_rejected_in_fresh_process(name, mutate, cls):
    a, _b, d = _one_step()
    base, bad = mutate(json.loads(_dump(a)), json.loads(_dump(d)))
    got = _child({"base": base, "steps": [{"diff": bad}]})
    assert got == {"ok": False, "failure_class": cls, "code": MAPPING[cls], "done": 0}
    # the same persisted input rejects identically in-process
    with pytest.raises(diff.DiffError) as err:
        diff.apply(bad, base)
    assert (err.value.failure_class, err.value.code) == (cls, MAPPING[cls])


def test_rejection_mid_run_keeps_earlier_steps_and_stops():
    states = _trajectory(seed=2)
    diffs = [diff.compute(a, b) for a, b in zip(states, states[1:], strict=False)]
    bad = json.loads(_dump(diffs[3]))
    bad["target_id"] = "gs1:" + "e" * 64
    steps = [{"diff": d} for d in diffs[:3]] + [{"diff": bad}] + [{"diff": d} for d in diffs[4:]]
    got = _child({"base": states[0], "steps": steps})
    if bad["added"] or bad["removed"]:
        assert got == {"ok": False, "failure_class": DT, "code": MAPPING[DT], "done": 3}
    else:
        assert got["ok"] is False and got["done"] == 3


# -- rollback survives restart ----------------------------------------------------------


def test_rollback_survives_restart():
    states = _trajectory(seed=4)
    base = copy.deepcopy(states[0])
    persisted_before = _dump(base)
    a, _b, d = _one_step()
    for _name, mutate, cls in MUTATIONS:
        mb, bad = mutate(json.loads(_dump(a)), json.loads(_dump(d)))
        snaps = copy.deepcopy((mb, bad, base))
        for target in (mb, base):
            with pytest.raises(diff.DiffError) as err:
                diff.apply(bad, target)
            if target is mb:
                assert err.value.failure_class == cls
        assert (mb, bad, base) == snaps
    assert _dump(base) == persisted_before
    got = _child(
        {"base": json.loads(persisted_before), "steps": [{"target": s} for s in states[1:]]}
    )
    assert got["ok"], got
    assert got["out"][-1]["state"] == states[-1]
    assert [o["state_id"] for o in got["out"]] == [diff.state_id(s) for s in states[1:]]


def test_child_outputs_do_not_alias_parent_inputs():
    states = _trajectory(seed=6)
    a, b = states[0], states[1]
    out = diff.apply(diff.compute(a, b), a)
    for k in out:
        out[k]["variant"] = "poison"
    assert a == _trajectory(seed=6)[0]
    assert not _alias_red(diff)


def _poison(section):
    for rec in section.values():
        rec["variant"] = "poison"


def _alias_red(mod):
    """compute output never holds live input records; apply output never
    holds live diff records, in either direction."""
    a, b, _d = _one_step()
    a0, b0 = copy.deepcopy(a), copy.deepcopy(b)
    d = mod.compute(a, b)
    _poison(d["added"])
    _poison(d["removed"])
    if (a, b) != (a0, b0):
        return True
    d = mod.compute(a, b)
    d0 = copy.deepcopy(d)
    out = mod.apply(d, a)
    _poison(out)
    if d != d0:
        return True
    out = mod.apply(d, a)
    out0 = copy.deepcopy(out)
    _poison(d["added"])
    _poison(d["removed"])
    return out != out0


def _shuffled(state):
    return dict(reversed(list(state.items())))


def _order_states():
    # the seeded walk, then jumps that add and remove several keys at once
    return [*_trajectory(seed=5), _state(KEYS), _state(KEYS[:1]), _state(KEYS[2:])]


def test_reversed_insertion_order_gives_the_canonical_diff_in_fresh_process():
    states = _order_states()
    got = _child(
        {"base": _shuffled(states[0]), "steps": [{"target": _shuffled(s)} for s in states[1:]]}
    )
    assert got["ok"], got
    for (a, b), o in zip(zip(states, states[1:], strict=False), got["out"], strict=True):
        assert _dump(o["diff"]) == _dump(diff.compute(a, b))
        for section in ("added", "removed", "changed"):
            assert list(o["diff"][section]) == sorted(o["diff"][section])
    assert not _order_red(diff)


def _order_red(mod):
    states = _order_states()
    for a, b in zip(states, states[1:], strict=False):
        if _dump(mod.compute(_shuffled(a), _shuffled(b))) != _dump(_model_diff(a, b)):
            return True
    return False


def _boundary_red(mod):
    """Each diff-only row must reject with its class from validate_diff
    and apply; the base-rekeyed row must reject from apply and state_id."""
    a, _b, d = _one_step()
    for name, mutate, cls in MUTATIONS:
        if name not in DIFF_ONLY | {"base-record-rekeyed"}:
            continue
        base, bad = mutate(json.loads(_dump(a)), json.loads(_dump(d)))
        calls = [lambda base=base, bad=bad: mod.apply(bad, base)]
        if name in DIFF_ONLY:
            calls.append(lambda bad=bad: mod.validate_diff(bad))
        else:
            calls.append(lambda base=base: mod.state_id(base))
        for call in calls:
            try:
                call()
                return True
            except mod.DiffError as err:
                if err.failure_class != cls:
                    return True
    return False


# -- in-file mutants: the restart/rollback checks are RED on one-edit mutants ---------


def _mutant(old, new):
    src = (ROOT / "graph" / "diff.py").read_text()
    assert src.count(old) == 1, old
    mod = types.ModuleType("diff_mutant")
    mod.__file__ = str(ROOT / "graph" / "diff.py")
    exec(compile(src.replace(old, new), mod.__file__, "exec"), mod.__dict__)  # noqa: S102
    return mod


def _rollback_red(mod):
    a, _b, d = _one_step()
    for _name, mutate, _cls in MUTATIONS:
        mb, bad = mutate(json.loads(_dump(a)), json.loads(_dump(d)))
        snap = copy.deepcopy(mb)
        try:
            mod.apply(bad, mb)
            return True
        except mod.DiffError:
            pass
        if mb != snap:
            return True
    return False


def _key_order_red(mod):
    rng = random.Random(9)
    for state in _trajectory(seed=9):
        items = list(state.items())
        rng.shuffle(items)
        if mod.state_id(dict(items)) != _model_id(state):
            return True
    return False


RESTART_MUTANTS = [
    (
        "apply-stages-in-place",
        "    staged = copy.deepcopy(base)",
        "    staged = base",
        _rollback_red,
    ),
    ("state-id-unsorted", "    for key in sorted(state):", "    for key in state:", _key_order_red),
    (
        "compute-added-holds-target-records",
        "    added = {key: copy.deepcopy(target[key]) for key in target if key not in base}",
        "    added = {key: target[key] for key in target if key not in base}",
        _alias_red,
    ),
    (
        "compute-removed-holds-base-records",
        "    removed = {key: copy.deepcopy(base[key]) for key in base if key not in target}",
        "    removed = {key: base[key] for key in base if key not in target}",
        _alias_red,
    ),
    (
        "apply-stores-diff-added-records",
        "        staged[key] = copy.deepcopy(record)",
        "        staged[key] = record",
        _alias_red,
    ),
    (
        "compute-added-unsorted",
        '        "added": dict(sorted(added.items())),',
        '        "added": added,',
        _order_red,
    ),
    (
        "empty-unequal-ids-not-divergent",
        '    if empty and diff["base_id"] != diff["target_id"]:\n'
        '        _fail("divergent_target")\n',
        "",
        _boundary_red,
    ),
    (
        "nonempty-equal-ids-not-malformed",
        '    if not empty and diff["base_id"] == diff["target_id"]:\n'
        '        _fail("malformed_diff_record")\n',
        "",
        _boundary_red,
    ),
    (
        "changed-witness-equal-accepted",
        '        if witness["base"] == witness["target"]:\n'
        '            _fail("malformed_diff_record")\n',
        "",
        _boundary_red,
    ),
    (
        "witness-base-identity-inverted",
        '        if _record_identity(witness["base"]) != key or',
        '        if _record_identity(witness["base"]) == key or',
        _boundary_red,
    ),
    (
        "witness-target-identity-inverted",
        ' or _record_identity(witness["target"]) != key:',
        ' or _record_identity(witness["target"]) == key:',
        _boundary_red,
    ),
    (
        "state-id-skips-validation",
        "def state_id(state):\n    _validate_state(state)\n",
        "def state_id(state):\n",
        _boundary_red,
    ),
]


@pytest.mark.parametrize("name,old,new,red", RESTART_MUTANTS, ids=[m[0] for m in RESTART_MUTANTS])
def test_restart_checks_green_on_production(name, old, new, red):
    assert not red(diff)


@pytest.mark.parametrize("name,old,new,red", RESTART_MUTANTS, ids=[m[0] for m in RESTART_MUTANTS])
def test_restart_checks_red_on_mutant(name, old, new, red):
    assert red(_mutant(old, new))
