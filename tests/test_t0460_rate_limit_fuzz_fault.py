"""T0460: seeded fuzz/fault campaign against the production admission decision
(server.identity_admission, T0458).

Duties:

1. CLASSIFICATION: every fuzzed request ends exactly one of admit/replay with a
   detached snapshot, or AdmissionRefusal with a closed code. Any other
   exception is a crash. An independent predicate (no production code) decides
   whether a generated request is well formed.
2. ROLLBACK: every refusal leaves the caller's request and state bit-identical.
3. HOSTILE VALUES: subclasses, bool-as-int, equality/truthiness liars and
   non-container state are injected into every field and must refuse closed.
4. FAULT INJECTION: one-edit mutants of the production source are run through
   the same campaign detector; each must be caught.
5. DETERMINISM: the campaign is pure in its seed.
"""

from __future__ import annotations

import copy
import inspect
import random
import types

import pytest

import server.identity_admission as prod
from server.identity_admission import decide

CODES = {"rate_limited", "malformed_request", "idempotency_conflict", "internal"}
OPS = ("identity.login", "identity.register")


class LiarEq:
    def __eq__(self, other):
        return True

    def __hash__(self):
        return 1

    def __bool__(self):
        return True


class StrSub(str):
    pass


class IntSub(int):
    pass


class DictSub(dict):
    pass


def _good():
    return dict(
        operation="identity.login",
        account="opaque:a",
        source="opaque:s",
        now=10,
        policy={
            "version": 1,
            "account": {"capacity": 2, "window_ms": 10},
            "source": {"capacity": 2, "window_ms": 10},
        },
        state={},
    )


def _hostile_values(rng):
    return rng.choice(
        [
            None, 1, 0, -1, 2**53, 2**53 - 1, 1.5, True, False, "", "x", b"b",
            "opaque:", [], {}, (), object(), LiarEq(), StrSub("opaque:z"),
            IntSub(3), DictSub(), float("nan"), float("inf"),
        ]
    )  # fmt: skip


class Crash(Exception):
    """A foreign exception from the decision: never a refusal, never a kill. It is not an
    AssertionError, so a campaign that meets one fails the test instead of counting a catch."""


def _run(module_decide, request):
    """Return (kind, snapshot) or (code, None); a foreign exception raises Crash."""
    try:
        return module_decide(**request)
    except Exception as error:  # noqa: BLE001
        # name check lets mutant modules (which define their own class) classify too
        if type(error).__name__ != "AdmissionRefusal":
            raise Crash(repr(error)) from error
        assert error.code in CODES
        return error.code, None


def _well_formed(request):
    """Independent shape predicate for the request (state and policy sane)."""
    r = request
    if r.get("replay", "new") != "new" or type(r.get("replay", "new")) is not str:
        return False
    if type(r.get("body_same", True)) is not bool:
        return False
    if type(r["operation"]) is not str or r["operation"] not in OPS:
        return False
    for k in ("account", "source"):
        if type(r[k]) is not str or not r[k].startswith("opaque:"):
            return False
    if type(r["now"]) is not int or not 0 <= r["now"] <= 2**53 - 1:
        return False
    p = r["policy"]
    if type(p) is not dict or set(p) != {"version", "account", "source"}:
        return False
    if type(p["version"]) is not int or p["version"] < 1:
        return False
    for s in ("account", "source"):
        b = p[s]
        if type(b) is not dict or set(b) != {"capacity", "window_ms"}:
            return False
        if any(type(b[k]) is not int or not 1 <= b[k] <= 2**53 - 1 for k in b):
            return False
    return type(r["state"]) is dict


def _mutate_request(rng):
    request = _good()
    request.update({"replay": "new", "body_same": True})
    path = rng.choice(
        [("operation",), ("account",), ("source",), ("now",), ("policy",), ("state",),
         ("policy", "version"), ("policy", "account"), ("policy", "source"),
         ("policy", "account", "capacity"), ("policy", "source", "window_ms"),
         ("replay",), ("body_same",), ("store_available",), ("effect_ok",)]
    )  # fmt: skip
    target = request
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = _hostile_values(rng)
    if rng.random() < 0.3 and type(request["policy"]) is dict and request["policy"]:
        request["policy"].pop(rng.choice(sorted(request["policy"])), None)
    return request


def _freeze(x):
    """Identity-faithful structural snapshot (NaN-safe, equality-liar-safe)."""
    if type(x) in (dict, DictSub):
        return (type(x).__name__, tuple((_freeze(k), _freeze(v)) for k, v in x.items()))
    if type(x) in (list, tuple):
        return (type(x).__name__, tuple(_freeze(v) for v in x))
    return (type(x).__name__, repr(x))


def _model_snapshot(request):
    """Independent expected snapshot for an admitted request: the prior state plus exactly one
    (slot, count) entry per scope, nothing else."""
    out = dict(request["state"])
    for scope in ("account", "source"):
        window = request["policy"][scope]["window_ms"]
        key = (request["operation"], scope, request[scope])
        slot = request["now"] // window
        previous_slot, previous_count = out.get(key, (slot, 0))
        out[key] = (slot, (previous_count if previous_slot == slot else 0) + 1)
    return out


def campaign(module_decide, seed, n=400):
    log = []
    rng = random.Random(seed)
    for _ in range(n):
        request = _mutate_request(rng)
        before = _freeze(request)
        kind, snap = _run(module_decide, request)
        # every path, admit or refuse, leaves the whole request untouched
        assert _freeze(request) == before, (seed, kind)
        flags_ok = (
            request.get("store_available", True) is True and request.get("effect_ok", True) is True
        )
        if kind == "replay":
            assert type(request["replay"]) is str and request["replay"] == "cached"
            assert request.get("body_same", True) is True
            assert snap is not request["state"]
        elif kind == "admit":
            assert _well_formed(request) and flags_ok
            assert snap is not request["state"]
            assert snap == _model_snapshot(request), (seed, snap)
        elif _well_formed(request) and flags_ok:
            # empty state, well formed, unfaulted: a refusal would be a false refusal
            raise AssertionError(f"false refusal {kind}: {before}")
        else:
            assert kind in CODES
        log.append(kind)
    return log


def stateful_campaign(module_decide, seed, steps=300):
    """Well-formed sequences judged by an independent ledger; must hit rate_limited."""
    rng = random.Random(seed)
    policy = {
        "version": 1,
        "account": {"capacity": 2, "window_ms": 6},
        "source": {"capacity": 3, "window_ms": 6},
    }
    state, ledger, log, now = {}, [], [], 0
    for _ in range(steps):
        now += rng.choice((0, 0, 1, 3, 6))
        op = rng.choice(OPS)
        acc, src = f"opaque:a{rng.randrange(2)}", f"opaque:s{rng.randrange(2)}"
        request = dict(operation=op, account=acc, source=src, now=now, policy=policy, state=state)
        before = _freeze(request)
        room = all(
            sum(1 for o, a, s, t in ledger if o == op and (a if sc == "account" else s) == k
                and t // 6 == now // 6) < policy[sc]["capacity"]
            for sc, k in (("account", acc), ("source", src))
        )  # fmt: skip
        kind, snap = _run(module_decide, request)
        assert _freeze(request) == before
        assert kind == ("admit" if room else "rate_limited"), (seed, now, kind)
        if room:
            assert snap == _model_snapshot(request), (seed, now, snap)
            assert len(snap) == len({k for k in snap}) and set(snap) >= set(state)
            state = snap
            ledger.append((op, acc, src, now))
        log.append(kind)
    return log


@pytest.mark.parametrize("seed", [60, 460, 4600, 20261005])
def test_campaign_classifies_every_case_and_never_admits_bad_input(seed):
    log = campaign(decide, seed)
    assert set(log) <= {"admit", "replay"} | CODES
    assert "malformed_request" in log and "internal" in log


@pytest.mark.parametrize("seed", [5, 50, 500])
def test_stateful_campaign_matches_ledger_and_hits_rate_limit(seed):
    log = stateful_campaign(decide, seed)
    assert "rate_limited" in log and "admit" in log


def test_campaign_is_deterministic_in_its_seed():
    assert campaign(decide, 460) == campaign(decide, 460)
    assert campaign(decide, 460) != campaign(decide, 461)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_refusal_rolls_back_state_exactly(seed):
    rng = random.Random(seed)
    policy = _good()["policy"]
    state = {}
    for _step in range(60):
        request = _good()
        request.update(
            now=rng.randrange(0, 40),
            account=f"opaque:a{rng.randrange(2)}",
            source=f"opaque:s{rng.randrange(2)}",
            operation=rng.choice(OPS),
            policy=policy,
            state=state,
        )
        if rng.random() < 0.3:
            request[rng.choice(("store_available", "effect_ok"))] = False
        before = _freeze(request)
        kind, snap = _run(decide, request)
        assert _freeze(request) == before
        if kind == "admit":
            state = snap


def test_hostile_state_entries_refuse_internal_without_mutation():
    bad_states = [
        {("identity.login", "account", "opaque:a"): (1,)},
        {("identity.login", "account"): (1, 1)},
        {"k": (1, 1)},
        {"abc": (1, 1)},  # a non-tuple key with the right length
        {("identity.login", "account", "opaque:a"): (True, 1)},
        {("identity.login", "account", "opaque:a"): (1, -1)},
        {("identity.login", "account", "opaque:a"): [1, 1]},
        {("identity.login", "account", "opaque:a"): (99, 1)},  # future slot
        {("identity.login", "account", "opaque:a"): (1, 99)},  # over capacity
        DictSub(),
        [],
    ]
    for state in bad_states:
        request = _good()
        request["state"] = state
        before = copy.deepcopy(state)
        kind, _ = _run(decide, request)
        assert kind == "internal", state
        assert state == before


def test_cached_replay_and_conflict_boundaries():
    for body_same, expected in ((True, "replay"), (False, "idempotency_conflict")):
        request = _good()
        request.update(replay="cached", body_same=body_same)
        assert _run(decide, request)[0] == expected
    for replay in (LiarEq(), StrSub("cached"), 1, None, "CACHED"):
        request = _good()
        request["replay"] = replay
        assert _run(decide, request)[0] == "malformed_request"
    for body_same in (1, 0, None, LiarEq()):
        request = _good()
        request.update(replay="cached", body_same=body_same)
        assert _run(decide, request)[0] == "malformed_request"


def test_boundary_rows_hold_on_production():
    assert _boundary_rows_fail(decide) is False


def test_previous_count_boundary_at_capacity_and_one_over():
    key = ("identity.login", "account", "opaque:a")
    for count, expected in ((2, "rate_limited"), (3, "internal")):
        request = _good()  # account capacity 2, window 10: now 10 is slot 1
        request["state"] = {key: (1, count)}
        assert _run(decide, request)[0] == expected, count
    request = _good()
    request["state"] = {key: (1, 1)}
    assert _run(decide, request)[0] == "admit"


def test_cached_replay_snapshot_is_detached_from_the_state():
    state = {("identity.login", "account", "opaque:a"): (1, 1)}
    request = _good()
    request.update(replay="cached", state=state)
    kind, snap = _run(decide, request)
    assert kind == "replay" and snap == state and snap is not state
    snap[("identity.login", "account", "opaque:b")] = (1, 1)
    assert list(state) == [("identity.login", "account", "opaque:a")]


def _boundary_rows_fail(d):
    """Acceptance rows with literal expectations, one violation each. True when any row's
    outcome differs from its expectation. A foreign exception raises Crash (never a kill)."""
    key = ("identity.login", "account", "opaque:a")
    rows = [
        ({key: (1, 2)}, {}, "rate_limited"),  # count == capacity
        ({key: (1, 3)}, {}, "internal"),  # count == capacity + 1
        ({key: (1, 1)}, {}, "admit"),
        ({"abc": (1, 1)}, {}, "internal"),  # non-tuple key, 3 chars
        ({key: (1, 1)}, {"replay": "cached"}, "replay"),
    ]
    for state, extra, expected in rows:
        request = dict(_good(), state=state, **extra)
        kind, snap = _run(d, request)
        if kind != expected:
            return True
        if kind == "replay" and (snap is state or snap != state):
            return True
    return False


def _validation_rows():
    """(name, request overrides, expected code). One violation per row, except the last
    (which pins that policy validation precedes the state check)."""
    key = ("identity.login", "account", "opaque:a")
    good_bucket = {"capacity": 2, "window_ms": 10}
    return [
        (
            "policy-bucket-dict-subclass",
            {"policy": {"version": 1, "account": DictSub(good_bucket), "source": good_bucket}},
            "malformed_request",
        ),
        ("account-str-subclass", {"account": StrSub("opaque:a")}, "malformed_request"),
        ("account-without-prefix", {"account": "plain"}, "malformed_request"),
        ("source-str-subclass", {"source": StrSub("opaque:s")}, "malformed_request"),
        ("source-without-prefix", {"source": "plain"}, "malformed_request"),
        ("state-negative-number", {"state": {key: (1, -1)}}, "internal"),
        ("state-bool-number", {"state": {key: (True, 1)}}, "internal"),
        (
            "policy-capacity-zero",
            {
                "policy": {
                    "version": 1,
                    "account": {"capacity": 0, "window_ms": 10},
                    "source": good_bucket,
                }
            },
            "malformed_request",
        ),
        (
            "source-policy-malformed-and-state-not-a-dict",
            {
                "policy": {
                    "version": 1,
                    "account": good_bucket,
                    "source": {"capacity": "x", "window_ms": 10},
                },
                "state": [],
            },
            "malformed_request",
        ),
    ]


def _validation_rows_fail(d, names=None):
    """True when any named row's outcome differs from its literal expectation."""
    for name, overrides, expected in _validation_rows():
        if names is not None and name not in names:
            continue
        kind, _ = _run(d, dict(_good(), **overrides))
        if kind != expected:
            return True
    return False


def test_validation_rows_hold_on_production():
    assert _validation_rows_fail(decide) is False


VALIDATION_MUTANTS = {
    "bucket-type-or-to-and": (
        'if type(value) is not dict or set(value) != {"capacity", "window_ms"}:',
        'if type(value) is not dict and set(value) != {"capacity", "window_ms"}:',
        ("policy-bucket-dict-subclass",),
    ),
    "bucket-minimum-zero": (
        "type(n) is not int or n < 1 or n > _MAX_INTEGER",
        "type(n) is not int or n < 0 or n > _MAX_INTEGER",
        ("policy-capacity-zero",),
    ),
    "account-type-or-to-and": (
        "or type(account) is not str\n",
        "and type(account) is not str\n",
        ("account-str-subclass",),
    ),
    "account-prefix-or-to-and": (
        "or not account.startswith",
        "and not account.startswith",
        ("account-without-prefix",),
    ),
    "source-type-or-to-and": (
        "or type(source) is not str\n",
        "and type(source) is not str\n",
        ("source-str-subclass",),
    ),
    "source-prefix-or-to-and": (
        "or not source.startswith",
        "and not source.startswith",
        ("source-without-prefix",),
    ),
    "state-value-check-or-to-and": (
        "or len(value) != 2\n            or any(",
        "or len(value) != 2\n            and any(",
        ("state-negative-number", "state-bool-number"),
    ),
    "source-policy-not-validated": (
        'for scope in ("account", "source")}',
        'for scope in ("account",)}',
        ("source-policy-malformed-and-state-not-a-dict",),
    ),
}


@pytest.mark.parametrize("name", sorted(VALIDATION_MUTANTS))
def test_each_validation_mutant_dies_on_a_semantic_row(name):
    old, new, rows = VALIDATION_MUTANTS[name]
    mutant = _mutant(old, new)
    assert _validation_rows_fail(mutant.decide, set(rows)), name


def _mutant(old, new):
    source = inspect.getsource(prod)
    assert source.count(old) == 1, old
    module = types.ModuleType("mutant_identity_admission")
    exec(compile(source.replace(old, new), "mutant", "exec"), module.__dict__)  # noqa: S102
    return module


MUTANTS = [
    ("previous_slot == slot and previous_count > capacity", "False"),
    ("updates = {}", "policy['version'] = 999\n    updates = {}"),
    ("previous_slot > slot or ", ""),
    ("if count >= capacity:", "if count > capacity:"),
    ("type(now) is not int", "not isinstance(now, int)"),
    ("type(replay) is not str or", "False or"),
    ('raise AdmissionRefusal("idempotency_conflict")', 'return "replay", deepcopy(state)'),
    ("if type(store_available) is not bool or not store_available:", "if False:"),
    ("if type(effect_ok) is not bool or not effect_ok:", "if False:"),
    ('replay not in ("new", "cached")', 'replay not in ("new", "cached", "x")'),
    ("slot = now // duration", "slot = now // (duration + 1)"),
    ("result = deepcopy(state)", "result = state"),
    ("count = previous_count if previous_slot == slot else 0", "count = previous_count"),
    ('return "replay", deepcopy(state)', 'return "replay", state'),
    (
        "    result.update(updates)\n",
        "    result.update(updates)\n    result[('x', 'account', 'opaque:phantom')] = (0, 1)\n",
    ),
    ("or type(key) is not tuple\n", "\n")
    if False
    else (
        "type(key) is not tuple\n            or len(key) != 3",
        "False\n            or len(key) != 3",
    ),
    ("previous_count > capacity)", "previous_count >= capacity)"),
    ("previous_count > capacity)", "previous_count > capacity + 1)"),
]


@pytest.mark.parametrize("old,new", MUTANTS)
def test_every_mutant_is_caught_by_a_detector(old, new):
    mutant = _mutant(old, new)
    caught = False
    for seed in (60, 460, 4600):
        try:
            campaign(mutant.decide, seed)
            stateful_campaign(mutant.decide, seed)
        except AssertionError:
            caught = True
            break
    if not caught:
        caught = _targeted_detectors_fail(mutant) or _boundary_rows_fail(mutant.decide)
    assert caught, f"mutant survived: {old!r}"


def _targeted_detectors_fail(mutant):
    """Boundary detectors independent of the random campaign."""
    d = mutant.decide
    try:
        r = _good()
        r["policy"]["account"]["capacity"] = 1
        r["policy"]["source"]["capacity"] = 1
        kind, state = d(**r)
        r["state"] = state
        if _run(d, r)[0] != "rate_limited":
            return True
        r["now"] = 10
        r["state"] = {}
        kind, snap = d(**r)
        if snap is r["state"]:
            return True
        r2 = _good()
        r2["now"] = True
        if _run(d, r2)[0] != "malformed_request":
            return True
        r3 = _good()
        r3["store_available"] = False
        if _run(d, r3)[0] != "internal":
            return True
        r4 = _good()
        r4["effect_ok"] = 0
        if _run(d, r4)[0] != "internal":
            return True
        r5 = _good()
        r5.update(replay="x")
        if _run(d, r5)[0] != "malformed_request":
            return True
        r6 = _good()
        r6.update(body_same=1)
        if _run(d, r6)[0] != "malformed_request":
            return True
        r8 = _good()
        r8.update(replay="cached", body_same=False)
        if _run(d, r8)[0] != "idempotency_conflict":
            return True
        r9 = _good()
        r9["state"] = {("identity.login", "account", "opaque:a"): (99, 1)}
        if _run(d, r9)[0] != "internal":
            return True
        r10 = _good()
        r10["state"] = {
            ("identity.login", "account", "opaque:a"): (1, 99)
        }  # over capacity in-window
        r10["now"] = 10
        if _run(d, r10)[0] != "internal":
            return True
        # window rollover
        r7 = _good()
        r7["policy"]["account"].update(capacity=1, window_ms=5)
        r7["policy"]["source"].update(capacity=1, window_ms=5)
        r7["now"] = 4
        _, s = d(**r7)
        r7.update(now=5, state=s)
        if _run(d, r7)[0] != "admit":
            return True
    except AssertionError:
        return True
    return False
