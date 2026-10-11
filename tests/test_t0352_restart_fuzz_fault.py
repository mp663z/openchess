"""T0352: seeded fuzz and fault battery for the production jobs restart.

The contract's reference implementation is the differential oracle. Valid
cases are mutated at every reachable path with hostile values (every type
the validators gate on, boundary ints and strings, armed objects whose every
hook fails the test, attribute bombs, lying dicts, cycles, 100000-deep and
wide values, oversize ints under a lowered interpreter int-digit limit),
keys are dropped, added and replaced by non-str or armed keys, and faults
are injected into the digest step. Production and reference must return the
same record or the same failure class and code, a rejected call must leave
its input structurally identical (a hook-free iterative snapshot), production
must never run caller code, and a late fault must leave the input untouched
and the next call clean. Seeded and single-threaded, so a failure replays.
"""

from __future__ import annotations

import copy
import hashlib
import random
import sys
import time

import pytest

import server.jobs_restart as PMOD
from server.jobs_queue import QueueEngine, job_id_for
from server.jobs_restart import RestartError as PROD_ERR
from server.jobs_restart import restart as PROD
from tests.test_t0347_restart_contract import RestartError as REF_ERR
from tests.test_t0347_restart_contract import restart as REF

CODES = {"malformed_restart_request": "malformed_request", "corrupt_queue": "corrupt_queue"}

MAX = 2**53 - 1
REQUEST_FAILURE = "malformed_restart_request"
STATE_KEY_ORDER_MATTERS = False


def _args(case):
    return (case["state"], case["request"])


def _job(i, status="ready", attempts=0, owner=None, expires=None, payload=None):
    return {
        "job_id": job_id_for(f"k{i}"),
        "seq": i,
        "priority": i % 10,
        "payload": {"i": i} if payload is None else payload,
        "status": status,
        "attempts": attempts,
        "lease_owner": owner,
        "lease_expires_at": expires,
    }


def make_base_cases(rng):
    state = {
        "jobs": [
            _job(0, "leased", 1, "w1", 100),
            _job(1, "leased", 2, "w2", 100),
            _job(2),
            _job(3, "done", 1),
            _job(4, "dead", 5),
            _job(5, "leased", 5, "w1", 51),
            _job(6, "leased", 1, "w1", 50),
        ],
        "next_seq": 7,
    }
    cases = []
    for worker, now in (("w1", 50), ("w1", 0), ("w2", 99), ("w3", 10), ("w1", MAX)):
        cases.append(
            {
                "state": copy.deepcopy(state),
                "request": {"op": "restart", "worker": worker, "now": now},
            }
        )
    cases.append(
        {
            "state": {"jobs": [], "next_seq": 0},
            "request": {"op": "restart", "worker": "w1", "now": 1},
        }
    )
    cases.append(
        {
            "state": {"jobs": [_job(0, payload={"é": [1, 2.5, None]})], "next_seq": 1},
            "request": {"op": "restart", "worker": "A" * 64, "now": 1},
        }
    )
    return cases


# ---------------------------------------------------------------------------
# Shared fuzz and fault machinery. A case is {"request": ..., "state": ...}
# ("state" only for restart). Hostile cases are always rebuilt from a factory,
# never deep-copied, so no caller code runs in the harness itself.
# ---------------------------------------------------------------------------

HOOKS = (
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__repr__",
    "__str__",
    "__iter__",
    "__len__",
    "__getitem__",
    "__contains__",
    "__bool__",
    "__int__",
    "__float__",
    "__index__",
    "__bytes__",
    "__format__",
    "__reversed__",
    "__abs__",
    "__neg__",
    "__add__",
    "__radd__",
    "__sub__",
    "__mul__",
    "__hash__",
)


def _raiser(name):
    def hook(self, *a, **k):
        raise AssertionError(f"caller code ran: {name}")

    return hook


def _make_armed(base, hooks):
    ns = {h: _raiser(h) for h in hooks}
    if "__hash__" not in hooks and base.__hash__ is not None:
        ns["__hash__"] = base.__hash__
    return type("Armed" + base.__name__.capitalize(), (base,), ns)


ArmedObject = _make_armed(object, [h for h in HOOKS if h != "__hash__"])
ArmedStr = _make_armed(str, [h for h in HOOKS if h != "__hash__"])
ArmedInt = _make_armed(int, [h for h in HOOKS if h != "__hash__"])
ArmedFloat = _make_armed(float, [h for h in HOOKS if h != "__hash__"])
ArmedList = _make_armed(list, HOOKS)
ArmedDict = _make_armed(dict, HOOKS)
ArmedTuple = _make_armed(tuple, HOOKS)
ArmedBytes = _make_armed(bytes, [h for h in HOOKS if h != "__hash__"])


class AttrBomb:
    """Any attribute access is a fault (guards getattr-based duck typing)."""

    def __getattribute__(self, name):
        raise AssertionError(f"caller code ran: attribute {name}")


class LyingDict(dict):
    """An exact-looking dict whose real contents differ from what hooks say."""

    def keys(self):
        raise AssertionError("caller code ran: keys")

    def items(self):
        raise AssertionError("caller code ran: items")

    def values(self):
        raise AssertionError("caller code ran: values")

    def get(self, *a):
        raise AssertionError("caller code ran: get")

    def __getitem__(self, k):
        raise AssertionError("caller code ran: getitem")


class Plain:
    pass


def _nested_list(depth):
    node = []
    for _ in range(depth - 1):
        node = [node]
    return node


def _nested_dict(depth):
    node = {}
    for _ in range(depth - 1):
        node = {"k": node}
    return node


def _cyc_list():
    c = []
    c.append(c)
    return c


def _cyc_dict():
    c = {}
    c["me"] = c
    return c


HOSTILE_FACTORIES = (
    lambda: None,
    lambda: True,
    lambda: False,
    lambda: 0,
    lambda: 1,
    lambda: -1,
    lambda: 2**53 - 1,
    lambda: 2**53,
    lambda: 2**63,
    lambda: -(2**63),
    lambda: 2**70,
    lambda: 10**5000,
    lambda: -(10**5000),
    lambda: 1.0,
    lambda: 0.5,
    lambda: -0.0,
    lambda: float("nan"),
    lambda: float("inf"),
    lambda: float("-inf"),
    lambda: "",
    lambda: " ",
    lambda: "x" * 300,
    lambda: "\x00",
    lambda: "\ud800",
    lambda: "\U0001f600",
    lambda: "job1:" + "A" * 64,
    lambda: b"bytes",
    lambda: bytearray(b"x"),
    lambda: [],
    lambda: {},
    lambda: (),
    lambda: (1, 2),
    lambda: {1, 2},
    lambda: frozenset(),
    lambda: object(),
    lambda: Plain(),
    lambda: complex(1, 1),
    lambda: range(3),
    lambda: _nested_list(70),
    lambda: _nested_dict(70),
    lambda: _cyc_list(),
    lambda: _cyc_dict(),
    lambda: ArmedObject(),
    lambda: ArmedStr("x"),
    lambda: ArmedInt(3),
    lambda: ArmedFloat(1.5),
    lambda: ArmedList([1]),
    lambda: ArmedDict(a=1),
    lambda: ArmedTuple((1,)),
    lambda: ArmedBytes(b"x"),
    lambda: AttrBomb(),
    lambda: LyingDict(a=1),
)

NASTY_KEYS = (
    lambda: 1,
    lambda: None,
    lambda: True,
    lambda: 1.5,
    lambda: (1,),
    lambda: b"k",
    lambda: ArmedStr("armed-key"),
    lambda: ArmedInt(7),
    lambda: "\ud800",
    lambda: "",
    lambda: "x" * 1000,
)


def _isnap(case):
    """Iterative, cycle-safe, hook-free structural snapshot."""
    out, seen, stack = [], {}, [case]
    while stack:
        n = stack.pop()
        t = type(n)
        if t is list or t is dict:
            if id(n) in seen:
                out.append(("ref", seen[id(n)]))
                continue
            seen[id(n)] = len(seen)
            if t is dict:
                items = list(dict.items(n))
                out.append(("d", len(items)))
                for k, v in reversed(items):
                    stack.append(v)
                    stack.append(k)
            else:
                items = list(list.__iter__(n))
                out.append(("l", len(items)))
                stack.extend(reversed(items))
        elif t is int:
            out.append(("int", hex(n)))
        elif t is str or t is float or t is bool or n is None or t is bytes:
            out.append((t.__name__, repr(n)))
        else:
            out.append(("other", t.__name__))
    return out


def _paths(node, prefix=()):
    """Every (path) to a value reachable through exact dicts and lists of a valid case."""
    out = [prefix]
    if type(node) is dict:
        for k, v in node.items():
            out.extend(_paths(v, prefix + (k,)))
    elif type(node) is list:
        for i, v in enumerate(node[:6]):
            out.extend(_paths(v, prefix + (i,)))
    return out


def _get(root, path):
    for step in path:
        root = root[step]
    return root


def _build(base, path, make=None, drop=False, add=None):
    """A fresh copy of BASE with the value at PATH replaced, dropped, or a key added."""
    case = copy.deepcopy(base)
    if not path:
        return make() if make else case
    parent = _get(case, path[:-1])
    if add is not None:
        parent[add[0]()] = add[1]()
    elif drop:
        del parent[path[-1]]
    else:
        parent[path[-1]] = make()
    return case


def _outcome(fn, case, err_cls):
    try:
        res = fn(*_args(case))
        return ("ok", res, _isnap(case.get("state")) if "state" in case else None)
    except err_cls as error:
        assert type(error) is err_cls
        assert error.__cause__ is None and error.__context__ is None
        assert error.retryable is False
        return ("fail", error.failure_class, error.code)


def _agree(base, **kw):
    """Run PROD and REF on two fresh builds; require identical outcomes and no torn input."""
    prod_case, ref_case = _build(base, **kw), _build(base, **kw)
    before = _isnap(prod_case)
    prod = _outcome(PROD, prod_case, PROD_ERR)
    ref = _outcome(REF, ref_case, REF_ERR)
    assert prod[0] == ref[0], (kw.get("path"), prod, ref)
    if prod[0] == "fail":
        assert prod == ref, (kw.get("path"), prod, ref)
        assert _isnap(prod_case) == before, "rejected call changed its input"
        assert prod[2] == CODES[prod[1]]
    else:
        assert prod[1] == ref[1], (kw.get("path"), prod, ref)
        assert prod[2] == ref[2]
        assert _isnap(prod_case["request"]) == _isnap(_build(base, **kw)["request"]), (
            "accepted call changed its request"
        )
    return prod


def _request_unchanged(base, **kw):
    case = _build(base, **kw)
    req_before = _isnap(case["request"])
    _outcome(PROD, case, PROD_ERR)
    assert _isnap(case["request"]) == req_before, "request was changed"


# ---------- differential fuzz: every path x every hostile value ----------


def _bases():
    rng = random.Random(1234)
    return make_base_cases(rng)


BASES = _bases()


def test_bases_are_valid_for_both_engines_and_agree():
    assert BASES
    for base in BASES:
        out = _agree(base, path=())
        assert out[0] == "ok", base


def test_every_path_of_every_valid_case_rejects_or_matches_the_oracle_on_hostile_values():
    seen = set()
    for base in BASES:
        for path in _paths(base):
            if not path:
                continue
            for factory in HOSTILE_FACTORIES:
                out = _agree(base, path=path, make=factory)
                seen.add(out[0])
    assert seen == {"ok", "fail"}


def test_dropping_any_key_or_adding_any_key_matches_the_oracle():
    fails = 0
    for base in BASES:
        for path in _paths(base):
            if len(path) > 1 and type(path[-1]) is str:
                fails += _agree(base, path=path, drop=True)[0] == "fail"
            if path and type(_get(base, path)) is dict:
                for make_key in NASTY_KEYS + (lambda: "extra",):
                    fails += (
                        _agree(base, path=path + ("x",), add=(make_key, lambda: 1))[0] == "fail"
                    )
    assert fails > 0


@pytest.mark.parametrize("seed", range(30))
def test_seeded_multi_mutation_fuzz_matches_the_oracle(seed):
    rng = random.Random(9000 + seed)
    for _ in range(40):
        base = rng.choice(BASES)
        paths = [p for p in _paths(base) if p]
        picks = sorted((rng.choice(paths) for _ in range(rng.randint(1, 3))), key=len, reverse=True)
        makes = [rng.choice(HOSTILE_FACTORIES) for _ in picks]

        def build_case(base=base, picks=picks, makes=makes):
            case = copy.deepcopy(base)
            for path, make in zip(picks, makes, strict=True):
                try:
                    parent = _get(case, path[:-1])
                    parent[path[-1]] = make()
                except (KeyError, IndexError, TypeError):
                    pass
            return case

        prod_case, ref_case = build_case(), build_case()
        before = _isnap(prod_case)
        prod = _outcome(PROD, prod_case, PROD_ERR)
        ref = _outcome(REF, ref_case, REF_ERR)
        assert prod[0] == ref[0] and prod[1] == ref[1], (picks, prod, ref)
        if prod[0] == "fail":
            assert _isnap(prod_case) == before


# ---------- fault objects: no caller code, always typed, never raw ----------


def test_armed_objects_at_every_path_never_run_caller_code_and_fail_typed():
    armed = (
        ArmedObject,
        lambda: ArmedStr("x"),
        lambda: ArmedInt(1),
        lambda: ArmedFloat(1.0),
        lambda: ArmedList([1]),
        lambda: ArmedDict(a=1),
        lambda: ArmedTuple((1,)),
        lambda: ArmedBytes(b"x"),
        AttrBomb,
        lambda: LyingDict(a=1),
    )
    for base in BASES:
        for path in _paths(base):
            if not path:
                continue
            for make in armed:
                case = _build(base, path=path, make=make)
                before = _isnap(case)
                # any AssertionError from an armed hook would escape _outcome
                out = _outcome(PROD, case, PROD_ERR)
                assert out[0] == "fail", (path, out)
                assert _isnap(case) == before


def test_armed_and_nasty_keys_in_every_dict_are_refused_without_running_hooks():
    for base in BASES:
        for path in _paths(base):
            if not path or type(_get(base, path)) is not dict:
                continue
            for make_key in NASTY_KEYS:
                case = _build(base, path=path + ("x",), add=(make_key, lambda: 1))
                before = _isnap(case)
                out = _outcome(PROD, case, PROD_ERR)
                assert _isnap(case) == before or out[0] == "ok"
                if type(dict.fromkeys([make_key()]).popitem()[0]) is not str:
                    assert out[0] == "fail", path
                else:  # exact str keys of any content: the oracle decides
                    _agree(base, path=path + ("x",), add=(make_key, lambda: 1))
    # an armed key replacing a real key is also refused, never run
    for base in BASES:
        for path in _paths(base):
            if (
                len(path) < 2
                or type(path[-1]) is not str
                or type(_get(base, path[:-1])) is not dict
            ):
                continue
            case = copy.deepcopy(base)
            parent = _get(case, path[:-1])
            value = parent.pop(path[-1])
            dict.__setitem__(parent, ArmedStr(path[-1]), value)
            before = _isnap(case)
            out = _outcome(PROD, case, PROD_ERR)
            assert out[0] == "fail", path
            assert _isnap(case) == before


class PlainStr(str):
    """A str subclass with no hooks: only its exact type is wrong."""


def test_plain_str_subclass_keys_with_valid_names_and_values_are_refused_like_the_oracle():
    refused = 0
    for base in BASES:
        for path in _paths(base):
            if (
                len(path) < 2
                or type(path[-1]) is not str
                or type(_get(base, path[:-1])) is not dict
            ):
                continue

            def build(base=base, path=path):
                case = copy.deepcopy(base)
                parent = _get(case, path[:-1])
                value = parent.pop(path[-1])
                dict.__setitem__(parent, PlainStr(path[-1]), value)
                return case

            prod_case, ref_case = build(), build()
            before = _isnap(prod_case)
            prod = _outcome(PROD, prod_case, PROD_ERR)
            ref = _outcome(REF, ref_case, REF_ERR)
            assert prod[0] == ref[0] == "fail", (path, prod, ref)
            assert prod[1] == ref[1] and prod[2] == ref[2]
            assert _isnap(prod_case) == before
            refused += 1
    assert refused > 0


def test_non_dict_and_hostile_top_level_requests_are_total():
    for factory in HOSTILE_FACTORIES:
        base = BASES[0]
        case = copy.deepcopy(base)
        case["request"] = factory()
        out = _outcome(PROD, case, PROD_ERR)
        ref_case = copy.deepcopy(base)
        ref_case["request"] = factory()
        ref = _outcome(REF, ref_case, REF_ERR)
        assert out[0] == ref[0] == "fail"
        assert out[1] == ref[1] == REQUEST_FAILURE


# ---------- pathological sizes and shapes (fast, no recursion, no raw faults) ----------


def _timed(fn, *a):
    start = time.perf_counter()
    out = fn(*a)
    assert time.perf_counter() - start < 5.0, "pathological input was not handled in bounded time"
    return out


def test_hundred_thousand_deep_and_wide_values_at_every_field_fail_typed_in_bounded_time():
    deep_list = _nested_list(100_000)
    deep_dict = _nested_dict(100_000)
    wide_dict = {str(i): i for i in range(100_000)}
    wide_list = list(range(100_000))
    big_str = "a" * 5_000_000
    big_int = 10**100_000
    for base in BASES[:2]:
        for path in [p for p in _paths(base) if p][:12]:
            for value in (deep_list, deep_dict, wide_dict, wide_list, big_str, big_int, -big_int):
                case = _build(base, path=path, make=lambda v=value: v)
                before = _isnap(case) if value is not deep_dict else None
                out = _timed(_outcome, PROD, case, PROD_ERR)
                assert out[0] in ("ok", "fail")
                if out[0] == "fail" and before is not None:
                    assert _isnap(case) == before


def test_huge_ints_never_hit_a_str_conversion_under_a_lowered_interpreter_limit():
    old = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        for base in BASES:
            for path in [p for p in _paths(base) if p]:
                for value in (10**1000, -(10**1000), 2**5000, 10**639):
                    prod_case, ref_case = (
                        _build(base, path=path, make=lambda v=value: v),
                        _build(base, path=path, make=lambda v=value: v),
                    )
                    prod = _outcome(PROD, prod_case, PROD_ERR)
                    ref = _outcome(REF, ref_case, REF_ERR)
                    assert prod[0] == ref[0] and prod[1] == ref[1], (path, value.bit_length())
    finally:
        sys.set_int_max_str_digits(old)
    assert sys.get_int_max_str_digits() == old


def test_every_field_boundary_value_matches_the_oracle():
    edges = [
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        9,
        10,
        11,
        63,
        64,
        65,
        127,
        128,
        129,
        999,
        1000,
        1001,
        2**31 - 1,
        2**31,
        2**32,
        2**53 - 3,
        2**53 - 2,
        2**53 - 1,
        2**53,
        -1,
        -2,
    ]
    for base in BASES:
        for path in _paths(base):
            if not path or type(_get(base, path)) is not int or type(_get(base, path)) is bool:
                continue
            for value in edges:
                _agree(base, path=path, make=lambda v=value: v)
            original = _get(base, path)
            for delta in (-1, 1):
                _agree(base, path=path, make=lambda d=delta, b=original: b + d)


def test_every_string_field_boundary_matches_the_oracle():
    samples = [
        "",
        "a",
        "A",
        "z9",
        "w" * 63,
        "w" * 64,
        "w" * 65,
        "w" * 128,
        "w" * 129,
        "w w",
        "w/w",
        "w.w",
        "w:w",
        "w-w",
        "w_w",
        "é",
        "ｗ",
        "\n",
        "w\n",
        "\x00",
        "job1:" + "0" * 64,
        "job1:" + "0" * 63,
        "job1:" + "0" * 65,
        "job1:" + "G" * 64,
        "job2:" + "0" * 64,
    ]
    for base in BASES:
        for path in _paths(base):
            if not path or type(_get(base, path)) is not str:
                continue
            for value in samples:
                _agree(base, path=path, make=lambda v=value: v)


# ---------- order independence, determinism, late faults ----------


def _reorder(node, rng):
    if type(node) is dict:
        items = [(k, _reorder(v, rng)) for k, v in node.items()]
        rng.shuffle(items)
        return dict(items)
    if type(node) is list:
        return [_reorder(v, rng) for v in node]
    return node


@pytest.mark.parametrize("seed", range(10))
def test_key_order_never_changes_the_outcome_and_runs_are_deterministic(seed):
    rng = random.Random(seed)
    for base in BASES:
        first = _outcome(PROD, copy.deepcopy(base), PROD_ERR)
        assert _outcome(PROD, copy.deepcopy(base), PROD_ERR) == first
        shuffled = _reorder(copy.deepcopy(base), rng)
        assert _outcome(PROD, shuffled, PROD_ERR)[:2] == first[:2] or STATE_KEY_ORDER_MATTERS


class _CountingHashlib:
    """Delegates to the real hashlib and faults on the Nth sha256 call."""

    def __init__(self, fail_on=None):
        self.calls, self.fail_on = 0, fail_on

    def sha256(self, *a, **k):
        self.calls += 1
        if self.fail_on == self.calls:
            raise RuntimeError("injected fault in the digest step")
        return hashlib.sha256(*a, **k)


def test_a_fault_at_any_digest_step_leaves_input_untouched_and_the_next_call_is_clean(monkeypatch):
    for base in BASES:
        counter = _CountingHashlib()
        monkeypatch.setattr(PMOD, "hashlib", counter)
        baseline = _outcome(PROD, copy.deepcopy(base), PROD_ERR)
        steps = counter.calls
        assert baseline[0] == "ok" and steps >= 1
        for n in range(1, steps + 1):
            monkeypatch.setattr(PMOD, "hashlib", _CountingHashlib(fail_on=n))
            case = copy.deepcopy(base)
            before = _isnap(case)
            with pytest.raises(RuntimeError, match="injected fault"):
                PROD(*_args(case))
            assert _isnap(case) == before, f"fault at digest step {n} tore the input"
            monkeypatch.setattr(PMOD, "hashlib", _CountingHashlib())
            again = _outcome(PROD, copy.deepcopy(base), PROD_ERR)
            assert again == baseline
        monkeypatch.undo()


def test_rejected_requests_never_reach_the_digest_step(monkeypatch):
    counter = _CountingHashlib()
    monkeypatch.setattr(PMOD, "hashlib", counter)
    for base in BASES:
        for path in [p for p in _paths(base) if p][:20]:
            case = _build(base, path=path, make=lambda: ArmedObject())
            _outcome(PROD, case, PROD_ERR)
    assert counter.calls == 0


# ---------- queue streams with crashed workers: restart against the real queue ----------


def _live_leases(state, worker, now):
    return [
        j
        for j in state["jobs"]
        if j["status"] == "leased" and j["lease_owner"] == worker and now < j["lease_expires_at"]
    ]


@pytest.mark.parametrize("seed", range(30))
def test_crash_and_restart_streams_match_the_oracle_and_the_queue_stays_sound(seed):
    rng = random.Random(2100 + seed)
    state, now = {"jobs": [], "next_seq": 0}, 0
    engine = QueueEngine()
    for _ in range(80):
        now += rng.choice((0, 1, 9, 200))
        pick = rng.random()
        try:
            if pick < 0.3:
                engine.apply(
                    state,
                    {
                        "op": "enqueue",
                        "dedupe_key": f"k{rng.randrange(8)}",
                        "priority": rng.randrange(3),
                        "payload": {"n": rng.randrange(2)},
                    },
                )
            elif pick < 0.6:
                engine.apply(
                    state,
                    {
                        "op": "claim",
                        "worker": rng.choice(("w1", "w2")),
                        "now": now,
                        "lease_ms": rng.choice((1, 10, 100)),
                    },
                )
            elif pick < 0.7:
                engine.apply(
                    state,
                    {
                        "op": rng.choice(("ack", "nack")),
                        "worker": rng.choice(("w1", "w2")),
                        "now": now,
                        "job_id": job_id_for(f"k{rng.randrange(8)}"),
                    },
                )
        except Exception as error:  # noqa: BLE001
            assert type(error).__name__ == "QueueError"
        if pick >= 0.7:
            worker = rng.choice(("w1", "w2", "w3"))
            request = {"op": "restart", "worker": worker, "now": now}
            prod_state, ref_state = copy.deepcopy(state), copy.deepcopy(state)
            before_attempts = {j["job_id"]: j["attempts"] for j in state["jobs"]}
            prod = _outcome(PROD, {"state": prod_state, "request": request}, PROD_ERR)
            ref = _outcome(REF, {"state": ref_state, "request": copy.deepcopy(request)}, REF_ERR)
            assert prod[0] == ref[0] == "ok" and prod[1] == ref[1] and prod_state == ref_state
            assert _live_leases(prod_state, worker, now) == []
            assert {j["job_id"]: j["attempts"] for j in prod_state["jobs"]} == before_attempts
            assert [j["seq"] for j in prod_state["jobs"]] == [j["seq"] for j in state["jobs"]]
            assert prod_state["next_seq"] == state["next_seq"]
            for released in prod[1]["released"]:
                job = next(j for j in prod_state["jobs"] if j["job_id"] == released)
                assert job["status"] == "ready" and job["lease_owner"] is None
            state = prod_state
            # the queue still accepts the restarted state: a claim reaches the released job
            probe = copy.deepcopy(state)
            try:
                engine.apply(probe, {"op": "claim", "worker": "w9", "now": now, "lease_ms": 5})
            except Exception as error:  # noqa: BLE001
                assert type(error).__name__ == "QueueError"


def test_every_single_corruption_of_every_job_field_is_corrupt_queue_like_the_oracle():
    base = BASES[0]
    n = len(base["state"]["jobs"])
    for i in range(n):
        for key in list(base["state"]["jobs"][i]):
            for factory in HOSTILE_FACTORIES:
                out = _agree(base, path=("state", "jobs", i, key), make=factory)
                if out[0] == "fail":
                    assert out[1] == "corrupt_queue"


def _one(state_jobs, worker="w1", now=7, next_seq=None):
    return {
        "state": {
            "jobs": state_jobs,
            "next_seq": len(state_jobs) if next_seq is None else next_seq,
        },
        "request": {"op": "restart", "worker": worker, "now": now},
    }


def test_every_status_attempt_and_lease_combination_matches_the_oracle():
    leases = [
        (None, None),
        ("w1", 1),
        ("w1", 7),
        ("w1", 8),
        ("w1", 0),
        ("w1", MAX),
        ("w1", MAX + 1),
        ("w2", 8),
        ("", 5),
        (None, 5),
        ("w1", None),
    ]
    for status in ("ready", "leased", "done", "dead", "other"):
        for attempts in range(-1, 8):
            for owner, expires in leases:
                base = _one([_job(0, status, attempts, owner, expires)])
                out = _agree(base, path=())
                if out[0] == "ok":
                    assert out[1]["restarted_at"] == 7


def test_now_around_every_expiry_edge_releases_exactly_the_unexpired_leases():
    for expiry in (1, 2, 50, MAX - 1, MAX):
        for now in {0, 1, 2, expiry - 1, expiry, expiry + 1, MAX - 1, MAX}:
            if not 0 <= now <= MAX:
                continue
            for worker in ("w1", "w2"):
                base = _one([_job(0, "leased", 1, "w1", expiry)], worker, now)
                out = _agree(base, path=())
                if out[0] == "ok":
                    released = out[1]["released"]
                    assert (released != []) == (worker == "w1" and now < expiry)


def test_seq_and_next_seq_edges_match_the_oracle():
    for seq in (-1, 0, 1, 2, 9, MAX):
        for next_seq in (-1, 0, 1, 2, 3, 10, MAX, MAX + 1):
            job = _job(0)
            job["seq"] = seq
            _agree(_one([job], next_seq=next_seq), path=())
    jobs = [_job(0), _job(1), _job(2)]
    jobs[0]["seq"], jobs[1]["seq"] = jobs[1]["seq"], jobs[0]["seq"]
    _agree(_one(jobs), path=())
    jobs = [_job(0), _job(1), _job(2)]
    jobs[2]["seq"] = 1
    _agree(_one(jobs), path=())
    jobs = [_job(0), _job(1), _job(2)]
    jobs[2]["job_id"] = jobs[0]["job_id"]
    _agree(_one(jobs), path=())


def test_valid_field_corruptions_match_the_oracle():
    for field, values in (
        ("priority", (-1, 0, 9, 10, 11, MAX, True)),
        ("attempts", (-1, 0, 5, 6, True)),
        ("status", ("READY", "", "leased ", None)),
        ("job_id", ("", "j", "J" * 64, None)),
        ("payload", (None, [], {}, "s", 1, 1.5, True)),
    ):
        for value in values:
            job = _job(0)
            job[field] = value
            _agree(_one([job]), path=())
    for n in (0, 1, 10000, 10001):
        jobs = [_job(i) for i in range(n)]
        _agree(_one(jobs, next_seq=n), path=())


def test_exact_bit_and_digit_edges_match_the_oracle():
    edges = [
        10**4000 - 1,
        -(10**4000 - 1),
        10**4000,
        -(10**4000),
        2**13287,
        2**13288,
        2**13289,
        -(2**13287),
        -(2**13288),
        2**13287 - 1,
        10**3999,
        10**3999 + 1,
    ]
    for value in edges:
        for wrap in (lambda v: v, lambda v: [v], lambda v: {"k": v}):
            base = _one([_job(0, payload=wrap(value))])
            out = _agree(base, path=())
            digits = len(str(abs(value))) if abs(value) < 10**4010 else 9999
            assert (out[0] == "ok") == (digits <= 4000)


def test_exact_nesting_depth_edges_match_the_oracle():
    for depth in range(58, 71):
        for wrap in (lambda v: [v], lambda v: {"k": v}):
            value = 1
            for _ in range(depth):
                value = wrap(value)
            _agree(_one([_job(0, payload=value)]), path=())
            _agree(_one([_job(0, payload=value)], worker="w2"), path=())
