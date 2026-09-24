"""T0338: jobs limit contract - the contract document
(data/contracts/limit.yaml) is normative; this battery holds the
contract-derived REFERENCE admit function and proves happy, boundary,
malformed and rollback behavior against it.

A limit decision is a pure function of one request
{op, key, cost, now, policy, previous}: a keyed token bucket of
`capacity` tokens gains one token every `refill_ms` milliseconds. previous
is null for a fresh full bucket and otherwise exactly the previous bucket
record. The request is admitted when the refilled tokens cover the cost;
otherwise it is denied with the exact retry_after_ms.

Fail-closed readings (the spec is silent; each is stated in the
contract's rule text):
- at capacity no partial refill is banked: updated_at jumps to now;
- a cost above capacity can never be admitted and is malformed, never a
  deny;
- key, capacity and refill_ms must equal the previous record's; a policy
  change starts a fresh bucket (previous null);
- now never precedes the previous updated_at; an equal now is accepted;
- a deny whose now + retry_after_ms would pass 2**53 - 1 is
  clock_overflow, never clamped;
- a previous record whose shape, bounds, decision invariants or limit_id
  do not recompute is corrupt and never trusted;
- validation order is request -> policy -> cost within capacity ->
  previous integrity -> chain -> clock.
Not covered (needs the idempotency/cancel contracts T0293/T0302,
parked): refunds for cancelled jobs and deduplication of replayed
admissions. Fairness across keys is out of scope.

DESIGN CAUTION: the reference is derived from the same contract
document, so this battery proves contract CONSISTENCY, not production
behavior; a later implement task must run the same cases against a
separately built runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.limit_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_POLICY_FIELDS = _REQ["policy_fields"]
_RECORD_FIELDS = _CC["record"]["fields"]
_DECISIONS = tuple(_CC["record"]["decisions"])
_KEY_RE = re.compile(_CC["identifiers"]["key"]["grammar"], re.ASCII)
_LIMIT_RE = re.compile(_CC["identifiers"]["limit_id"]["grammar"], re.ASCII)

# -- reference begin: everything down to the reference end marker is rebuilt
# from source for each reference mutant (constants, error class, checks).

_REF_MAX_CAPACITY = 1_000_000
_REF_MAX_REFILL = 86_400_000
_REF_MAX_NOW = 2**53 - 1


class LimitError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _int_in(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _grammar(value, regex):
    return type(value) is str and regex.fullmatch(value) is not None


def _exact_keys(mapping, fields):
    """Exact dict whose keys are exact strs equal to FIELDS (checked
    before any set comparison, so no user key code runs)."""
    return (
        type(mapping) is dict
        and all(type(k) is str for k in mapping)
        and len(mapping) == len(fields)
        and set(mapping) == set(fields)
    )


def _request_ok(request):
    if not _exact_keys(request, _FIELDS):
        return False
    return (
        type(request["op"]) is str
        and request["op"] == "admit"
        and _grammar(request["key"], _KEY_RE)
        and _int_in(request["cost"], 1, _REF_MAX_CAPACITY)
        and _int_in(request["now"], 0, _REF_MAX_NOW)
        and type(request["policy"]) is dict
        and (request["previous"] is None or type(request["previous"]) is dict)
    )


def _policy_ok(policy):
    return (
        _exact_keys(policy, _POLICY_FIELDS)
        and _int_in(policy["capacity"], 1, _REF_MAX_CAPACITY)
        and _int_in(policy["refill_ms"], 1, _REF_MAX_REFILL)
    )


def _limit_id(record_without_id):
    body = _canon(record_without_id).encode()
    return "lm1:" + hashlib.sha256(b"lm1\x00" + body).hexdigest()


def _previous_ok(prev):
    if not _exact_keys(prev, _RECORD_FIELDS):
        return False
    shape = (
        type(prev["op"]) is str
        and prev["op"] == "admit"
        and _grammar(prev["key"], _KEY_RE)
        and _int_in(prev["capacity"], 1, _REF_MAX_CAPACITY)
        and _int_in(prev["refill_ms"], 1, _REF_MAX_REFILL)
        and _int_in(prev["cost"], 1, prev["capacity"])
        and type(prev["decision"]) is str
        and prev["decision"] in _DECISIONS
        and _int_in(prev["tokens"], 0, prev["capacity"])
        and _int_in(prev["updated_at"], 0, _REF_MAX_NOW)
        and (prev["previous_id"] is None or _grammar(prev["previous_id"], _LIMIT_RE))
        and _grammar(prev["limit_id"], _LIMIT_RE)
    )
    if not shape:
        return False
    if prev["decision"] == "admit":
        if prev["retry_after_ms"] is not None or prev["tokens"] > prev["capacity"] - prev["cost"]:
            return False
    elif not (
        _int_in(prev["retry_after_ms"], 1, _REF_MAX_NOW)
        and prev["tokens"] < prev["cost"]
        and (prev["cost"] - prev["tokens"] - 1) * prev["refill_ms"] < prev["retry_after_ms"]
        and prev["retry_after_ms"] <= (prev["cost"] - prev["tokens"]) * prev["refill_ms"]
        and prev["updated_at"] + prev["retry_after_ms"] <= _REF_MAX_NOW
    ):
        return False
    return prev["limit_id"] == _limit_id({k: prev[k] for k in _RECORD_FIELDS[:-1]})


def _chain_ok(request):
    prev, policy = request["previous"], request["policy"]
    return (
        request["key"] == prev["key"]
        and policy["capacity"] == prev["capacity"]
        and policy["refill_ms"] == prev["refill_ms"]
        and request["now"] >= prev["updated_at"]
    )


def admit(request):
    """The reference decision. Fresh typed errors (flag pattern)."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_limit_request"
    elif not _policy_ok(request["policy"]):
        failed = "invalid_limit_policy"
    elif request["cost"] > request["policy"]["capacity"]:
        failed = "malformed_limit_request"
    elif request["previous"] is not None and not _previous_ok(request["previous"]):
        failed = "corrupt_previous_bucket"
    elif request["previous"] is not None and not _chain_ok(request):
        failed = "limit_conflict"
    if failed is None:
        req = json.loads(_canon(request))  # detached exact copy
        capacity, refill = req["policy"]["capacity"], req["policy"]["refill_ms"]
        now, prev = req["now"], req["previous"]
        if prev is None:
            refilled, base = capacity, now
        else:
            gained = (now - prev["updated_at"]) // refill
            if prev["tokens"] + gained >= capacity:
                refilled, base = capacity, now
            else:
                refilled, base = prev["tokens"] + gained, prev["updated_at"] + gained * refill
        if refilled >= req["cost"]:
            decision, tokens, retry = "admit", refilled - req["cost"], None
        else:
            decision, tokens = "deny", refilled
            retry = (req["cost"] - refilled) * refill - (now - base)
            if now + retry > _REF_MAX_NOW:
                failed = "clock_overflow"
    if failed is not None:
        raise LimitError(failed)
    record = {
        "op": "admit",
        "key": req["key"],
        "capacity": capacity,
        "refill_ms": refill,
        "cost": req["cost"],
        "decision": decision,
        "tokens": tokens,
        "updated_at": base,
        "retry_after_ms": retry,
        "previous_id": None if prev is None else prev["limit_id"],
    }
    record["limit_id"] = _limit_id(record)
    return record


# -- reference end

# -- helpers ----------------------------------------------------------------------------

POLICY = {"capacity": 3, "refill_ms": 1000}
MAX = 9007199254740991  # 2**53 - 1, written out: the contract's literal edge


def req(**kw):
    base = {
        "op": "admit",
        "key": "k-1",
        "cost": 1,
        "now": 0,
        "policy": dict(POLICY),
        "previous": None,
    }
    base.update(kw)
    return base


def _independent_id(record_without_id):
    """The contract preimage, built by hand: ASCII lm1, a NUL byte, then
    canonical JSON (sort_keys, compact separators, ensure_ascii) of the
    record without limit_id."""
    body = json.dumps(record_without_id, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "lm1:" + hashlib.sha256(b"lm1" + bytes([0]) + body.encode("utf-8")).hexdigest()


def _forge(**fields):
    """A previous record with the given fields and a RECOMPUTED id, so
    only the named defect is wrong."""
    rec = {
        "op": "admit",
        "key": "k-1",
        "capacity": 3,
        "refill_ms": 1000,
        "cost": 1,
        "decision": "admit",
        "tokens": 1,
        "updated_at": 50,
        "retry_after_ms": None,
        "previous_id": None,
    }
    rec.update(fields)
    rec["limit_id"] = _independent_id({k: rec[k] for k in _RECORD_FIELDS[:-1]})
    return rec


def _plain(obj):
    if type(obj) is dict:
        return all(type(k) is str and _plain(v) for k, v in obj.items())
    return type(obj) in (str, int, bool, type(None))


def _raises(failure_class, request):
    snap = copy.deepcopy(request) if _plain(request) else None
    with pytest.raises(LimitError) as caught:
        admit(request)
    exc = caught.value
    assert type(exc) is LimitError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False  # retryable_true_only_for is [internal]
    if snap is not None:
        assert request == snap


# -- the contract document ------------------------------------------------------------------


def test_lint_clean():
    lint()


def _mutants():
    def key_grammar(cc):
        cc["identifiers"]["key"]["grammar"] = "^.*$"

    def cap_reading(cc):
        cc["semantics"]["cap"] = "partial-refill-is-banked-at-capacity"

    def cost_reading(cc):
        cc["semantics"]["cost"] = "a-cost-above-capacity-is-denied"

    def identity_reading(cc):
        cc["semantics"]["identity"] = "a-policy-change-rescales-tokens"

    def overflow_reading(cc):
        cc["semantics"]["overflow"] = "retry-after-is-clamped"

    def derivation_prefix_dropped(cc):
        cc["identifiers"]["limit_id"]["derivation"] = (
            "sha256-over-canonical-json-of-the-record-without-limit-id"
        )

    def extra_failure(cc):
        cc["failures"]["classes"].append("limit_exhausted")

    def mapping_drift(cc):
        cc["failures"]["mapping"]["limit_conflict"] = "internal"

    def retryable(cc):
        cc["errors"]["shape"]["retryable_true_only_for"] = ["internal", "limit_conflict"]

    def record_field(cc):
        cc["record"]["fields"].append("fairness")

    def capacity_bound(cc):
        cc["request"]["bounds"]["capacity"] = "int-1-through-10000000"

    def policy_field(cc):
        cc["request"]["policy_fields"].append("burst")

    def link(cc):
        cc["links"]["queue_contract"] = "data/contracts/missing.yaml"

    def extra_section(cc):
        cc["fairness"] = {}

    return [
        key_grammar,
        cap_reading,
        cost_reading,
        identity_reading,
        overflow_reading,
        derivation_prefix_dropped,
        extra_failure,
        mapping_drift,
        retryable,
        record_field,
        capacity_bound,
        policy_field,
        link,
        extra_section,
    ]


@pytest.mark.parametrize("mutate", _mutants(), ids=lambda f: f.__name__)
def test_mutations_fail_lint(mutate, tmp_path):
    doc = yaml.safe_load(CONTRACT.read_text())
    mutate(doc["contract"])
    path = tmp_path / "limit.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


def test_lint_binds_the_queue_sibling(tmp_path, monkeypatch):
    """The key grammar is the queue contract's worker grammar: a drifted
    queue contract fails the limit lint too."""
    from tools import limit_contract_lint as ll

    queue = yaml.safe_load((ROOT / "data/contracts/queue.yaml").read_text())
    queue["contract"]["identifiers"]["worker"]["grammar"] = "^x$"
    (tmp_path / "data/contracts").mkdir(parents=True)
    (tmp_path / "data/contracts/queue.yaml").write_text(yaml.safe_dump(queue))
    monkeypatch.setattr(ll, "ROOT", tmp_path)
    with pytest.raises(ContractError):
        ll.lint(CONTRACT)


def test_error_enum_matches_mapping():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)


def test_bounds_are_the_contract_literals():
    bounds = _REQ["bounds"]
    assert bounds["capacity"] == "int-1-through-1000000"
    assert bounds["refill_ms"] == "int-1-through-86400000"
    assert bounds["cost"] == "int-1-through-capacity"
    assert bounds["now"] == "int-0-through-2-pow-53-minus-1-milliseconds"
    assert MAX == 2**53 - 1


# -- happy path: golden chain ------------------------------------------------------------------

_KEY = "q.7:w-2"
_GOLDEN_STEPS = [
    # (cost, now, decision, tokens, updated_at, retry_after_ms, limit_id)
    (
        1,
        0,
        "admit",
        2,
        0,
        None,
        "lm1:84faf4379e1aa8589f0fe6601fea12cef948b62f12d212cc0af0f3025c17157f",
    ),
    (
        2,
        500,
        "admit",
        0,
        0,
        None,
        "lm1:5cc092434a366ad4bd614b5ba1756f64547b975d39214a7c0544c93b4771c4a3",
    ),
    (
        2,
        1700,
        "deny",
        1,
        1000,
        300,
        "lm1:86d861cd59cf9c8ccd0b33b203814a9f81ce34a41e930299f858dff40f09c87a",
    ),
    (
        2,
        2000,
        "admit",
        0,
        2000,
        None,
        "lm1:d0bd5843a7c1256022300dfa31a44a1ff0b338a1bafa30d75052c224374a0434",
    ),
    (
        1,
        9999,
        "admit",
        2,
        9999,
        None,
        "lm1:a9f0e6fa7b5ef19444577ce4af2556dfa7740a50bba2711014716c3b47752e44",
    ),
]


def _golden():
    out, prev = [], None
    for cost, now, decision, tokens, updated_at, retry, lid in _GOLDEN_STEPS:
        request = {
            "op": "admit",
            "key": _KEY,
            "cost": cost,
            "now": now,
            "policy": {"capacity": 3, "refill_ms": 1000},
            "previous": copy.deepcopy(prev),
        }
        expected = {
            "op": "admit",
            "key": _KEY,
            "capacity": 3,
            "refill_ms": 1000,
            "cost": cost,
            "decision": decision,
            "tokens": tokens,
            "updated_at": updated_at,
            "retry_after_ms": retry,
            "previous_id": None if prev is None else prev["limit_id"],
            "limit_id": lid,
        }
        out.append((request, expected))
        prev = expected
    return out


def test_full_record_equality_golden_chain():
    for request, expected in _golden():
        got = admit(request)
        assert type(got) is dict and got == expected
        assert list(got) == _RECORD_FIELDS
        assert all(type(got[k]) is type(expected[k]) for k in expected)


def test_limit_id_matches_the_contract_preimage():
    for _, expected in _golden():
        without = {k: v for k, v in expected.items() if k != "limit_id"}
        assert _independent_id(without) == expected["limit_id"]
    prev = None
    for cost, now in ((5, 0), (5, 1), (9, 4), (2, 7), (10, 100)):
        got = admit(
            req(key="z", cost=cost, now=now, policy={"capacity": 10, "refill_ms": 3}, previous=prev)
        )
        without = {k: v for k, v in got.items() if k != "limit_id"}
        assert got["limit_id"] == _independent_id(without)
        assert (got["op"], got["key"], got["cost"], got["capacity"], got["refill_ms"]) == (
            "admit",
            "z",
            cost,
            10,
            3,
        )
        assert got["previous_id"] == (None if prev is None else prev["limit_id"])
        prev = got


def test_fresh_bucket_is_full():
    got = admit(req(cost=3, now=77))
    assert (got["decision"], got["tokens"], got["updated_at"]) == ("admit", 0, 77)


def test_tokens_stay_bounded_and_admissions_are_conserved():
    prev, admitted = None, 0
    policy = {"capacity": 4, "refill_ms": 10}
    for i in range(60):
        now = i * 3
        got = admit(req(cost=1 + i % 3, now=now, policy=dict(policy), previous=prev))
        assert 0 <= got["tokens"] <= 4
        if got["decision"] == "admit":
            admitted += got["cost"]
        prev = got
    assert admitted <= 4 + (59 * 3) // 10


def test_determinism_and_detachment():
    request = req(cost=1, now=60, previous=_forge())
    snap = copy.deepcopy(request)
    a, b = admit(request), admit(request)
    assert a == b and a is not b
    assert request == snap
    a["tokens"] = -1
    assert admit(request)["tokens"] == 0


# -- boundary ---------------------------------------------------------------------------------


def test_partial_refill_is_kept_below_capacity_and_dropped_at_it():
    prev = _forge(tokens=0, updated_at=100, cost=1)
    below = admit(req(cost=2, now=1100 + 999, previous=prev))
    assert (below["decision"], below["tokens"], below["updated_at"]) == ("deny", 1, 1100)
    assert below["retry_after_ms"] == 1
    capped = admit(req(cost=1, now=100 + 3000 + 999, previous=prev))
    assert (capped["tokens"], capped["updated_at"]) == (2, 4099)


def test_retry_after_is_exact():
    prev = _forge(tokens=0, updated_at=0, cost=1)
    for now, cost, retry in ((0, 1, 1000), (1, 1, 999), (999, 1, 1), (0, 3, 3000), (1500, 3, 1500)):
        got = admit(req(cost=cost, now=now, previous=prev))
        assert (got["decision"], got["retry_after_ms"]) == ("deny", retry), (now, cost)


def test_bounds_are_inclusive():
    admit(req(cost=1_000_000, policy={"capacity": 1_000_000, "refill_ms": 86_400_000}))
    admit(req(cost=1, policy={"capacity": 1, "refill_ms": 1}, now=9007199254740991))
    for bad in (req(cost=0), req(cost=1_000_001), req(now=-1), req(now=9007199254740992)):
        _raises("malformed_limit_request", bad)
    _raises("malformed_limit_request", req(cost=4))
    for policy in (
        {"capacity": 0, "refill_ms": 1},
        {"capacity": 1_000_001, "refill_ms": 1},
        {"capacity": 1, "refill_ms": 0},
        {"capacity": 1, "refill_ms": 86_400_001},
    ):
        _raises("invalid_limit_policy", req(policy=policy))


def test_clock_edge_fails_closed():
    prev = _forge(tokens=0, updated_at=MAX - 1000, cost=1)
    got = admit(req(cost=1, now=MAX - 1000, previous=prev))
    assert got["retry_after_ms"] == 1000 and got["decision"] == "deny"
    _raises("clock_overflow", req(cost=2, now=MAX - 1000, previous=prev))


def test_equal_clock_is_accepted():
    prev = _forge(updated_at=50)
    assert admit(req(now=50, previous=prev))["updated_at"] == 50


def test_conflicts():
    prev = _forge()
    for bad in (
        req(now=60, previous=prev, key="k-2"),
        req(now=60, previous=prev, policy={"capacity": 4, "refill_ms": 1000}),
        req(now=60, previous=prev, policy={"capacity": 3, "refill_ms": 999}),
        req(now=49, previous=prev),
    ):
        _raises("limit_conflict", bad)


def test_validation_order():
    stale = {**_forge(), "tokens": 2}
    _raises("corrupt_previous_bucket", req(now=0, key="k-2", previous=stale))
    _raises("malformed_limit_request", req(cost=4, previous=stale))
    _raises("invalid_limit_policy", req(cost=4, policy={"capacity": 0, "refill_ms": 1}))
    _raises("malformed_limit_request", req(op="x", policy={"capacity": 0}))
    _raises("limit_conflict", req(cost=2, now=0, previous=_forge(updated_at=MAX - 1000, tokens=0)))


# -- malformed requests, policies and corrupt previous records --------------------------------

HOSTILE = []


class _Armed:
    on = False


def _log(name):
    if _Armed.on:
        HOSTILE.append(name)


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _LyingDict(dict):
    def __getitem__(self, key):
        _log("getitem")
        return dict.__getitem__(self, key)

    def __iter__(self):
        _log("iter")
        return dict.__iter__(self)

    def __len__(self):
        _log("len")
        return dict.__len__(self)

    def keys(self):
        _log("keys")
        return dict.keys(self)

    def items(self):
        _log("items")
        return dict.items(self)


class _EqRaises(str):
    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")

    def __hash__(self):
        _log("hash")
        return str.__hash__(self)


class _Collides:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        _log("hash")
        return hash(self.name)

    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")


class _ReprRaises(str):
    def __repr__(self):
        _log("repr")
        raise RuntimeError("hostile __repr__")

    def __str__(self):
        _log("str")
        raise RuntimeError("hostile __str__")

    def __format__(self, spec):
        _log("format")
        raise RuntimeError("hostile __format__")


KEY_FORMS = {"str-subclass": _StrSub, "eq-raises": _EqRaises, "hash-collides": _Collides}


def _rekey(mapping, field, make):
    return {(make(k) if k == field else k): v for k, v in mapping.items()}


MALFORMED = {
    "none": None,
    "str": "admit",
    "list": list(req().items()),
    "list-subclass": _ListSub(req().items()),
    "dict-subclass": _DictSub(req()),
    "lying-dict": _LyingDict(req()),
    "missing-now": {k: v for k, v in req().items() if k != "now"},
    "extra": {**req(), "burst": 1},
    "renamed-same-arity": {("Now" if k == "now" else k): v for k, v in req().items()},
    "op-unknown": req(op="deny"),
    "op-str-subclass": req(op=_StrSub("admit")),
    "key-empty": req(key=""),
    "key-long": req(key="k" * 65),
    "key-newline": req(key="k-1\n"),
    "key-space": req(key="k 1"),
    "key-str-subclass": req(key=_StrSub("k-1")),
    "key-repr-raises": req(key=_ReprRaises("k-1")),
    "cost-bool": req(cost=True),
    "cost-float": req(cost=1.0),
    "cost-int-subclass": req(cost=_IntSub(1)),
    "now-bool": req(now=False),
    "now-int-subclass": req(now=_IntSub(0)),
    "policy-none": req(policy=None),
    "policy-list": req(policy=list(POLICY.items())),
    "policy-dict-subclass": req(policy=_DictSub(POLICY)),
    "policy-lying-dict": req(policy=_LyingDict(POLICY)),
    "previous-list": req(previous=[]),
    "previous-str": req(previous="lm1:" + "0" * 64),
    "previous-dict-subclass": req(previous=_DictSub(_forge())),
    "previous-lying-dict": req(previous=_LyingDict(_forge())),
    "previous-list-subclass": req(previous=_ListSub(_forge().items())),
    "bad-op-and-bad-policy": req(op="x", policy={"capacity": 0}),
}
for _field in _FIELDS:
    for _label, _make in KEY_FORMS.items():
        MALFORMED[f"key-{_label}-{_field}"] = _rekey(req(), _field, _make)

BAD_POLICY = {
    "missing": {"capacity": 3},
    "extra": {**POLICY, "burst": 1},
    "renamed-same-arity": {"Capacity": 3, "refill_ms": 1000},
    "bool": {**POLICY, "capacity": True},
    "float": {**POLICY, "refill_ms": 1000.0},
    "int-subclass": {**POLICY, "capacity": _IntSub(3)},
    "str": {**POLICY, "refill_ms": "1000"},
}
for _field in _POLICY_FIELDS:
    for _label, _make in KEY_FORMS.items():
        BAD_POLICY[f"key-{_label}-{_field}"] = _rekey(POLICY, _field, _make)

_GOOD_PREV = _forge()
CORRUPT = {
    "missing-field": {k: v for k, v in _GOOD_PREV.items() if k != "previous_id"},
    "extra-field": {**_GOOD_PREV, "burst": 0},
    "renamed-same-arity": {("Tokens" if k == "tokens" else k): v for k, v in _GOOD_PREV.items()},
    "op-wrong": _forge(op="deny"),
    "key-bad": _forge(key="k 1"),
    "key-newline": _forge(key="k-1\n"),
    "capacity-zero": _forge(capacity=0),
    "refill-zero": _forge(refill_ms=0),
    "cost-over-capacity": _forge(cost=4, tokens=0),
    "decision-unknown": _forge(decision="hold"),
    "decision-newline": _forge(decision="admit\n"),
    "tokens-over-capacity": _forge(tokens=4, cost=1),
    "tokens-negative": _forge(tokens=-1),
    "admit-tokens-over-capacity-minus-cost": _forge(tokens=3, cost=1),
    "admit-with-retry": _forge(retry_after_ms=5),
    "deny-without-retry": _forge(decision="deny", tokens=0, retry_after_ms=None),
    "deny-retry-zero": _forge(decision="deny", tokens=0, retry_after_ms=0),
    "deny-tokens-cover-cost": _forge(decision="deny", tokens=1, retry_after_ms=1000),
    "deny-retry-too-long": _forge(decision="deny", tokens=0, retry_after_ms=1001),
    "deny-retry-too-short": _forge(decision="deny", tokens=0, cost=2, retry_after_ms=1000),
    "deny-retry-past-clock": _forge(
        decision="deny", tokens=0, retry_after_ms=1000, updated_at=9007199254740991 - 999
    ),
    "updated-past-clock": _forge(updated_at=9007199254740992),
    "retry-bool": _forge(decision="deny", tokens=0, retry_after_ms=True),
    "previous-id-bad": _forge(previous_id="lm1:" + "G" * 64),
    "previous-id-newline": _forge(previous_id="lm1:" + "0" * 64 + "\n"),
    "limit-id-stale": {**_GOOD_PREV, "tokens": 0},
    "limit-id-grammar": {**_GOOD_PREV, "limit_id": "lm1:" + "0" * 63},
    "limit-id-no-prefix": {
        **_GOOD_PREV,
        "limit_id": "lm1:"
        + hashlib.sha256(
            json.dumps(
                {k: _GOOD_PREV[k] for k in _RECORD_FIELDS[:-1]},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    },
    "key-str-subclass-nested": {**_GOOD_PREV, "key": _StrSub("k-1")},
    "key-repr-raises-nested": {**_GOOD_PREV, "key": _ReprRaises("k-1")},
    "tokens-int-subclass-nested": {**_GOOD_PREV, "tokens": _IntSub(1)},
    "limit-id-str-subclass": {**_GOOD_PREV, "limit_id": _StrSub(_GOOD_PREV["limit_id"])},
}
for _field in _RECORD_FIELDS:
    for _label, _make in KEY_FORMS.items():
        CORRUPT[f"key-{_label}-{_field}"] = _rekey(_GOOD_PREV, _field, _make)


def _snap(obj):
    if isinstance(obj, dict):
        return (
            type(obj),
            [
                (type(k), k.name if type(k) is _Collides else str.__str__(k), _snap(v))
                for k, v in dict.items(obj)
            ],
        )
    if isinstance(obj, list):
        return (type(obj), [_snap(v) for v in list.__iter__(obj)])
    if isinstance(obj, str):
        return (type(obj), str.__str__(obj))
    return (type(obj), obj)


def _hostile(failure_class, request):
    """Typed class, fresh error, no user code ran, input unchanged."""
    snap = _snap(request)
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            admit(request)
            got = "accepted"
        except LimitError as exc:
            got = (
                type(exc) is LimitError,
                exc.failure_class,
                exc.code,
                exc.retryable,
                exc.__cause__ is None and exc.__context__ is None,
            )
        except BaseException as exc:  # noqa: BLE001 - a raw escape is the failure
            got = ("raw", type(exc).__name__)
        calls = list(HOSTILE)
    finally:
        _Armed.on = False
    assert got == (True, failure_class, FAILURE_MAPPING[failure_class], False, True), got
    assert calls == [], calls
    assert _snap(request) == snap


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_requests(name):
    _hostile("malformed_limit_request", MALFORMED[name])


@pytest.mark.parametrize("name", sorted(BAD_POLICY))
def test_invalid_policies(name):
    _hostile("invalid_limit_policy", req(policy=BAD_POLICY[name]))


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_corrupt_previous_records(name):
    _hostile("corrupt_previous_bucket", req(now=60, previous=CORRUPT[name]))


def test_forged_previous_is_otherwise_valid():
    assert admit(req(now=60, previous=_forge()))["previous_id"] == _GOOD_PREV["limit_id"]
    deny = _forge(decision="deny", tokens=0, retry_after_ms=1000)
    assert admit(req(now=60, previous=deny))["previous_id"] == deny["limit_id"]


def test_rejections_leave_inputs_bit_identical():
    for bad in (
        req(cost=0),
        req(now=10, previous=_forge()),
        req(previous=CORRUPT["limit-id-stale"]),
    ):
        before = copy.deepcopy(bad)
        with pytest.raises(LimitError):
            admit(bad)
        assert bad == before


# -- the reference itself is pinned: one-edit mutants fail the battery -------------------
# Anchors are matched against the reference block's own source (inspect),
# inside the builder, so an edit to this file never breaks collection.

REFERENCE_EDITS = {
    "gain-ceil": (
        'gained = (now - prev["updated_at"]) // refill',
        'gained = -(-(now - prev["updated_at"]) // refill)',
    ),
    "no-cap": ('if prev["tokens"] + gained >= capacity:', "if False:"),
    "cap-banks-partial": (
        "                refilled, base = capacity, now\n",
        '                refilled, base = capacity, prev["updated_at"] + gained * refill\n',
    ),
    "carry-drops-partial": (
        'refilled, base = prev["tokens"] + gained, prev["updated_at"] + gained * refill',
        'refilled, base = prev["tokens"] + gained, now',
    ),
    "admit-strict": ('if refilled >= req["cost"]:', 'if refilled > req["cost"]:'),
    "retry-no-partial": (
        'retry = (req["cost"] - refilled) * refill - (now - base)',
        'retry = (req["cost"] - refilled) * refill',
    ),
    "overflow-clamp": (
        '                failed = "clock_overflow"\n',
        "                retry = _REF_MAX_NOW - now\n",
    ),
    "cost-over-capacity-denied": (
        '    elif request["cost"] > request["policy"]["capacity"]:\n'
        '        failed = "malformed_limit_request"\n',
        "",
    ),
    "key-mismatch": ('request["key"] == prev["key"]\n        and ', ""),
    "capacity-mismatch": ('policy["capacity"] == prev["capacity"]', "True"),
    "refill-mismatch": ('policy["refill_ms"] == prev["refill_ms"]', "True"),
    "clock-regression": ('request["now"] >= prev["updated_at"]', "True"),
    "clock-strict": ('request["now"] >= prev["updated_at"]', 'request["now"] > prev["updated_at"]'),
    "prev-id-unchecked": (
        'return prev["limit_id"] == _limit_id(',
        'return True or prev["limit_id"] == _limit_id(',
    ),
    "prev-admit-invariant-off": (
        ' or prev["tokens"] > prev["capacity"] - prev["cost"]:',
        ":",
    ),
    "prev-retry-window-off": (
        'and prev["retry_after_ms"] <= (prev["cost"] - prev["tokens"]) * prev["refill_ms"]',
        "and True",
    ),
    "prev-type-unchecked": (
        '        and (request["previous"] is None or type(request["previous"]) is dict)\n',
        "",
    ),
    "chain-wrong-link": (
        '"previous_id": None if prev is None else prev["limit_id"],',
        '"previous_id": None if prev is None else prev["previous_id"],',
    ),
    "id-prefix-dropped": ('b"lm1\\x00" + ', ""),
    "id-preimage-extra-member": (
        "_canon(record_without_id)",
        '_canon({**record_without_id, "v": 1})',
    ),
    "op-const": ('"op": "admit",\n        "key"', '"op": "deny",\n        "key"'),
    "key-const": ('"key": req["key"],', '"key": "k-1",'),
    "updated-at-now": ('"updated_at": base,', '"updated_at": now,'),
    "capacity-bound-plus": ("_REF_MAX_CAPACITY = 1_000_000", "_REF_MAX_CAPACITY = 1_000_001"),
    "refill-bound-plus": ("_REF_MAX_REFILL = 86_400_000", "_REF_MAX_REFILL = 86_400_001"),
    "clock-bound-plus": ("_REF_MAX_NOW = 2**53 - 1", "_REF_MAX_NOW = 2**53"),
    "key-guard-off": ("        and all(type(k) is str for k in mapping)\n", ""),
    "grammar-type-off": ("return type(value) is str and regex.fullmatch", "return regex.fullmatch"),
    "int-type-off": ("return type(value) is int and lo <= value", "return lo <= value"),
    "retryable-true": ("self.retryable = False", "self.retryable = True"),
    "order-swap": (
        '    elif request["previous"] is not None and not _previous_ok(request["previous"]):\n'
        '        failed = "corrupt_previous_bucket"\n'
        '    elif request["previous"] is not None and not _chain_ok(request):\n'
        '        failed = "limit_conflict"\n',
        '    elif request["previous"] is not None and not _chain_ok(request):\n'
        '        failed = "limit_conflict"\n'
        '    elif request["previous"] is not None and not _previous_ok(request["previous"]):\n'
        '        failed = "corrupt_previous_bucket"\n',
    ),
    "chained-error": (
        "        raise LimitError(failed)",
        "        try:\n            raise KeyError(failed)\n"
        "        except KeyError:\n            raise LimitError(failed)",
    ),
}


# Edits that must NOT change behavior. not-detached: after validation the
# request holds only exact scalars plus exact policy/previous dicts of
# exact scalars, and the record copies scalars only.
EQUIVALENT_EDITS = {
    "not-detached": ("req = json.loads(_canon(request))", "req = request"),
}


def _mutant_reference(name):
    """Rebuild the whole reference block with one edit applied; returns
    the rebuilt top-level names."""
    import ast
    import inspect

    old, new = {**REFERENCE_EDITS, **EQUIVALENT_EDITS}[name]
    module_source = inspect.getsource(sys.modules[__name__])
    begin = module_source.index("# -- reference begin")
    end = module_source.index("# -- reference end")
    source = module_source[begin:end]
    assert source.count(old) == 1, name
    source = source.replace(old, new)
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
    namespace = dict(globals())
    exec(compile(source, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return {n: namespace[n] for n in names}


def _install(monkeypatch, name):
    for key, value in _mutant_reference(name).items():
        monkeypatch.setitem(globals(), key, value)


def _battery():
    """Every behavior test that takes no fixture, plus the parametrized
    malformed, policy and corrupt rows; returns the failing labels."""
    failures = []
    jobs = [
        (n, f)
        for n, f in sorted(globals().items())
        if n.startswith("test_")
        and callable(f)
        and f.__code__.co_argcount == 0
        and n
        not in (
            "test_lint_clean",
            "test_error_enum_matches_mapping",
            "test_bounds_are_the_contract_literals",
            "test_reference_edits_apply_once",
            "test_identity_battery_is_green",
        )
    ]
    jobs += [(f"malformed:{n}", lambda n=n: test_malformed_requests(n)) for n in MALFORMED]
    jobs += [(f"policy:{n}", lambda n=n: test_invalid_policies(n)) for n in BAD_POLICY]
    jobs += [(f"corrupt:{n}", lambda n=n: test_corrupt_previous_records(n)) for n in CORRUPT]
    for label, job in jobs:
        try:
            job()
        except BaseException as exc:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(exc).__name__}")
    return failures


def test_reference_edits_apply_once():
    for name in {**REFERENCE_EDITS, **EQUIVALENT_EDITS}:
        _mutant_reference(name)


def test_identity_battery_is_green():
    assert _battery() == []


@pytest.mark.parametrize("name", sorted(REFERENCE_EDITS))
def test_reference_mutant_is_red(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() != [], name


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() == [], name
