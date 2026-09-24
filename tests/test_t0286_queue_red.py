"""T0286 permanent red battery for jobs-queue engines.

The battery drives EVERY row of the T0285 conformance fixture
(tests/fixtures/queue/cases.json), a closed set of totality probes and
pinned semantic probes through an engine class constructed with no
arguments:

- happy/boundary: every request applied in order to the SAME state object
  (commit in place) returns the pinned receipt exactly (exact field order
  and exact field types) and ends in the pinned state; the state_id is
  re-derived here from the committed state and every job id from its
  dedupe key; the caller's request is untouched by value and identity and
  nothing in the committed state aliases it; the replaced containers of
  the prior state are not mutated; the returned receipt is fresh per call;
  determinism by value over fresh copies on the SAME instance;
- malformed: the original input rejects with the pinned failure class and
  its mapped code, fresh (no __cause__, no __context__), state and request
  untouched, and the declared single-locus minimal repair is accepted with
  the result the reference derives for it;
- rollback: a rejection leaves state and request untouched, then the valid
  follow-up on the SAME engine and state returns the pinned receipt and
  state;
- totality: hostile requests, payloads, states and jobs (non-dict and
  list/dict-subclass containers, a lying dict, str-subclass keys in 3
  forms - plain, eq-raises, hash-collides - str/int-subclass and bool
  values, grammar edges with a trailing newline and non-ASCII digits,
  cyclic, aliased, too-deep and too-long-int payloads, every state
  invariant) each reject with the pinned class, an empty hostile-call log
  and unchanged input; any other BaseException escaping is a failure;
- semantics: ordering, lease expiry edges, dead letter, nack, capacity and
  clock limits, dedupe, detachment of the committed state.

Standalone-red convention: the battery is permanently GREEN against the
contract-derived reference engine from tests.test_t0284_queue_contract and
every mutant below is RED on its pinned target label. The production task
switches the binding by replacing ONLY the two binding lines below with
the production QueueEngine / QueueError names; no assertion changes.

The queue engine takes no caller callback, so there is no untrusted-
component boundary and no forged-error battery (as in T0285).

Fixture closure: ordered per-section manifests and a ROW_DIGESTS whole-row
sha256 table whose key set equals the manifests.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0284_queue_contract as _reference  # noqa: E402
from tools.queue_contract_lint import FAILURE_MAPPING, MAX_DEPTH, MAX_INT_DIGITS  # noqa: E402

# -- binding (the production task replaces ONLY these two lines) ---------------
QueueEngine = _reference.QueueEngine
QueueError = _reference.QueueError

FIXTURE = Path(__file__).parent / "fixtures" / "queue" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
RECEIPT_FIELDS = ("op", "job_id", "status", "attempts", "lease_expires_at", "state_id")
JOB_FIELDS = (
    "job_id",
    "seq",
    "priority",
    "payload",
    "status",
    "attempts",
    "lease_owner",
    "lease_expires_at",
)
MQR, UJ, LC, DC, CE, CQ = (
    "malformed_queue_request",
    "unknown_job",
    "lease_conflict",
    "dedupe_conflict",
    "capacity_exceeded",
    "corrupt_queue",
)
CLASSES = frozenset(FAILURE_MAPPING)
MAX_NOW = 2**53 - 1
MAX_JOBS = 10000
MAX_ATTEMPTS = 5
ACCEPT = "accept"

HAPPY_MANIFEST = tuple(r["name"] for r in CASES["happy"])
BOUNDARY_MANIFEST = tuple(r["name"] for r in CASES["boundary"])
MALFORMED_MANIFEST = tuple(r["name"] for r in CASES["malformed"])
ROLLBACK_MANIFEST = tuple(r["name"] for r in CASES["rollback"])
MANIFESTS = {
    "happy": HAPPY_MANIFEST,
    "boundary": BOUNDARY_MANIFEST,
    "malformed": MALFORMED_MANIFEST,
    "rollback": ROLLBACK_MANIFEST,
}
EXPECTED_NAMES = {
    "happy": (
        "enqueue_new_job",
        "enqueue_claim_ack",
        "claim_nack_returns_ready",
        "claim_orders_priority_then_seq",
        "identical_reenqueue_is_noop",
    ),
    "boundary": (
        "empty_claim_null_receipt",
        "lease_expired_at_now_reclaimed",
        "ack_one_ms_before_expiry",
        "dead_after_max_attempts",
        "last_free_seq",
        "reenqueue_at_seq_exhausted",
        "reenqueue_at_max_jobs",
        "claim_expiry_at_clock_limit",
        "priority_extremes",
        "payload_depth_at_limit",
        "payload_int_at_digit_limit",
    ),
    "malformed": (
        "request_not_a_dict",
        "unknown_op",
        "enqueue_extra_field",
        "priority_bool",
        "priority_out_of_range",
        "dedupe_key_trailing_newline",
        "worker_bad_grammar",
        "lease_ms_zero",
        "claim_past_clock_limit",
        "payload_too_deep",
        "payload_int_past_digit_bound",
        "ack_unknown_job",
        "ack_by_non_owner",
        "ack_at_expiry",
        "nack_not_leased",
        "dedupe_conflict_priority",
        "dedupe_conflict_payload",
        "seq_space_exhausted",
        "state_not_a_dict",
        "duplicate_job_id",
        "leased_without_owner",
        "dead_below_max_attempts",
        "seq_not_increasing",
    ),
    "rollback": (
        "dedupe_conflict_then_identical_reenqueue",
        "lease_conflict_then_owner_ack",
        "malformed_payload_then_valid_enqueue",
        "expired_ack_then_reclaim",
    ),
}
ROW_DIGESTS = dict(
    [
        (
            "happy:enqueue_new_job",
            "5ebf63a5c0f5868b3cf843b92e69ba2bb8d6ff86c33a42e9e9d64a528d78227b",
        ),
        (
            "happy:enqueue_claim_ack",
            "bb4ddeacc1ea61360bcfe55bf40e3f060ec739279fc2a5702f5f429603c48f6a",
        ),
        (
            "happy:claim_nack_returns_ready",
            "6f640cc195fe204bfb9d43cd4792ddbdc778c447e92c33a61079b827426bee85",
        ),
        (
            "happy:claim_orders_priority_then_seq",
            "dd22a4ed06f39c61bd65d136eceefc5558fab0597a45239f7fe8b2a7613f62b2",
        ),
        (
            "happy:identical_reenqueue_is_noop",
            "29a14ee5df5731731f1ca193fe54ad98c8f71ee161171206dd70f5ce5cad6bf9",
        ),
        (
            "boundary:empty_claim_null_receipt",
            "0528e9a7ec830a6809e926ef5803333353ccfae562b0c51f2f03fe0a865d3707",
        ),
        (
            "boundary:lease_expired_at_now_reclaimed",
            "0c0a710bffe66ba215012cc6d3c807360519887fad6ee9782429ee2a92b0f0e4",
        ),
        (
            "boundary:ack_one_ms_before_expiry",
            "044e58853d11826c0e99fe7cbfe148ddf688de0fc6621a4e156f708ab5c861dc",
        ),
        (
            "boundary:dead_after_max_attempts",
            "bf3c4f732ebd13c110ae2eacd640ad2b581e1289a6d25c1064cf2213b896aa54",
        ),
        (
            "boundary:last_free_seq",
            "9d3023dac87a1b4e577627162cc913884e7f8fb95f66b44ca8253254f55c501f",
        ),
        (
            "boundary:reenqueue_at_seq_exhausted",
            "28a110aa2ee4beb3e1cc6200336e4299e36539df304dd92e236c86addc705829",
        ),
        (
            "boundary:reenqueue_at_max_jobs",
            "1929cadab4ac7a687ab33c6b2dd46d2082495122631cc92086b4ef81090e7ba8",
        ),
        (
            "boundary:claim_expiry_at_clock_limit",
            "a072b65486c1c325f7f766a5b56e27aa3c498f9953ea06a8aed0f69bcc337723",
        ),
        (
            "boundary:priority_extremes",
            "2abee70a52221e7c38f97cc07ae28ee5e628bd25db620b13dcc79f1b95709da6",
        ),
        (
            "boundary:payload_depth_at_limit",
            "f1d15706fa3e5ba8eeff489004c8832dd65c8e9d2a9a502838b9d59297225796",
        ),
        (
            "boundary:payload_int_at_digit_limit",
            "f16802f2a09f6a670a47d4fd15f1891f5c568fee84616348eb1e2b8f3223b66e",
        ),
        (
            "malformed:request_not_a_dict",
            "be98f7f7ad453a48ddea72e9c7d7dc831aa72b88b22a007a97c397ff485283d3",
        ),
        (
            "malformed:unknown_op",
            "80050f5efde0796477bbb415f98050f53857f7d4cac0bbdd3c34d8e348c53e25",
        ),
        (
            "malformed:enqueue_extra_field",
            "8f31ce91a94c421c22c636f1f7716a15d281e9fa4176832ac4a6ed0c5c32b1bc",
        ),
        (
            "malformed:priority_bool",
            "1a5ad8b77c76046293fab59582a7596f2f1f22ef2394b198d7bf2e2540af5972",
        ),
        (
            "malformed:priority_out_of_range",
            "2228d37896dca49c65d6e2f13bf9c89859fbc16ddc0d99964e9a03a4aaafb89d",
        ),
        (
            "malformed:dedupe_key_trailing_newline",
            "8ff39fa9af26fc2b8f57ee84e6a102eca98b5c50ae22352c431c9764cf6e7e7a",
        ),
        (
            "malformed:worker_bad_grammar",
            "80e5876f6df82f377beafb2f3d6c8320a76b753ea9294382157a0da8669e181d",
        ),
        (
            "malformed:lease_ms_zero",
            "f73742201d5a358e99aaf7f56a72c7e322f704f5144f2232988ddc162bcd17d8",
        ),
        (
            "malformed:claim_past_clock_limit",
            "0d9af084c33428696b9e8601ecd80d9c0b0682a46980bba3735a10c59f9a7769",
        ),
        (
            "malformed:payload_too_deep",
            "452d98b7fa3615de7a56f9adda476c78ce7871ba411a3c76221ffabe26d26fd8",
        ),
        (
            "malformed:payload_int_past_digit_bound",
            "78fa7f88b101b5d25fd52a9aefb054ecd7efde76fa4651924452e477d5ed2138",
        ),
        (
            "malformed:ack_unknown_job",
            "9ce8e0d22412be94e6282bb141bd2c25b71e79a2dc1ab8b85bef55d367d9a711",
        ),
        (
            "malformed:ack_by_non_owner",
            "694c8c643be00db12a3ecf91342264953918f11cf8c43bec3e49f7995f303dd7",
        ),
        (
            "malformed:ack_at_expiry",
            "26ca743fc0ef37172fdbdb6993eb99f83d7cf3d7f83ad5bc5696a08398695c91",
        ),
        (
            "malformed:nack_not_leased",
            "a661a8f7ac92da090bb0cf8eda2bf94b96d25d703ce27aa46bfd8418de5f72aa",
        ),
        (
            "malformed:dedupe_conflict_priority",
            "e6aad7dd837364b782ce3460390370a5a94d24074362774baf967e7fd470fb42",
        ),
        (
            "malformed:dedupe_conflict_payload",
            "f52353ebed7f0c24719d235d04748b9a3acc007b02b6ddd77d4286d4fa144998",
        ),
        (
            "malformed:seq_space_exhausted",
            "913b280f74955b26f9a8f2da901cbc55e49e951ff10f1b614c0a0827655f1560",
        ),
        (
            "malformed:state_not_a_dict",
            "0dde8f013ee787ff53edae770562a11d5be4faf87a6e98f55c19ede4f08c078c",
        ),
        (
            "malformed:duplicate_job_id",
            "e2fdd7a741e40c2d08b3684eae0e8bac08db8d3c0037ea32a392820a9dc70c31",
        ),
        (
            "malformed:leased_without_owner",
            "e401268f8ffb78f1bfaf2e3a2d16f61d7d437c7d4c518cc25b35a60e3dace2d9",
        ),
        (
            "malformed:dead_below_max_attempts",
            "8ea4b2ddfb730d5c15633c504229d90a025452627e3dae4b7ce803884108f32d",
        ),
        (
            "malformed:seq_not_increasing",
            "9cec17b4272ec4e4bc044a27ee308cb4cc7ef93ae366a6830d7aa9bdf29d82c9",
        ),
        (
            "rollback:dedupe_conflict_then_identical_reenqueue",
            "006f3fbecadc4f9b48f4ee4f579e78575e27ad054cf649d09f52934883ea17a5",
        ),
        (
            "rollback:lease_conflict_then_owner_ack",
            "901ed74a6fc87a65daca44ac75121475ac16cb02000a71f56606ec8521333587",
        ),
        (
            "rollback:malformed_payload_then_valid_enqueue",
            "6b6144b5d2a99f5e65c0c321bbdaead89ddb12e98dd0d3974216b801d32522e2",
        ),
        (
            "rollback:expired_ack_then_reclaim",
            "8e13fe33ad2a40bb81734dde4a1ecdf07277c82dfbd50e541c8875aeb858d473",
        ),
    ]
)


def _row_digest(row):
    text = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _validate_closure(cases):
    """Ordered names per section equal the manifests, and every row's
    whole-row digest matches the table."""
    for section, names in EXPECTED_NAMES.items():
        got = tuple(r["name"] for r in cases[section])
        assert got == names, section
        for row in cases[section]:
            assert _row_digest(row) == ROW_DIGESTS[f"{section}:{row['name']}"], row["name"]
    assert set(ROW_DIGESTS) == {f"{s}:{n}" for s, ns in EXPECTED_NAMES.items() for n in ns}


# -- independent derivations -------------------------------------------------------


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _job_id(dedupe_key):
    return "job1:" + hashlib.sha256(f"job1|{dedupe_key}".encode()).hexdigest()


def _state_id(state):
    return "qs1:" + hashlib.sha256(_canon(state).encode()).hexdigest()


# -- hostile types -----------------------------------------------------------------

HOSTILE = []  # user dunder calls observed while armed


class _Armed:
    on = False


class SK(str):
    """A plain str subclass."""


class _EqRaises(str):
    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("eqraises-eq")
            raise RuntimeError("hostile __eq__")
        return str.__eq__(self, other)

    def __hash__(self):
        if _Armed.on:
            HOSTILE.append("eqraises-hash")
        return str.__hash__(self)


class HK:
    """Hashes like a real field name; comparing it while armed raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("colliding-eq")
            raise RuntimeError("hostile __eq__")
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _Lying(dict):
    """A dict subclass whose every view is logged while armed."""

    def _log(self, name):
        if _Armed.on:
            HOSTILE.append(f"lying-{name}")

    def keys(self):
        self._log("keys")
        return dict.keys(self)

    def items(self):
        self._log("items")
        return dict.items(self)

    def values(self):
        self._log("values")
        return dict.values(self)

    def get(self, key, default=None):
        self._log("get")
        return dict.get(self, key, default)

    def __iter__(self):
        self._log("iter")
        return dict.__iter__(self)

    def __getitem__(self, key):
        self._log("getitem")
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        self._log("contains")
        return dict.__contains__(self, key)

    def __len__(self):
        self._log("len")
        return dict.__len__(self)


class _LogList(list):
    def __iter__(self):
        if _Armed.on:
            HOSTILE.append("list-iter")
        return list.__iter__(self)

    def __len__(self):
        if _Armed.on:
            HOSTILE.append("list-len")
        return list.__len__(self)


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hkey(form, text):
    if form == "plain":
        return SK(text)
    if form == "eq-raises":
        return _EqRaises(text)
    return HK(text)


def _rekey(mapping, field, key):
    return {(key if k == field else k): v for k, v in dict.items(mapping)}


def _snap(obj, memo=None):
    """Exact structure: type, identity, key order and value at every
    level (base-type methods only, so a subclass cannot lie)."""
    if memo is None:
        memo = set()
    if isinstance(obj, (dict, list)):
        if id(obj) in memo:
            return ("cycle", id(obj))
        memo = memo | {id(obj)}
    if isinstance(obj, dict):
        return (
            "d",
            type(obj).__name__,
            id(obj),
            tuple(
                (type(k).__name__, k if type(k) is str else id(k), _snap(v, memo))
                for k, v in dict.items(obj)
            ),
        )
    if isinstance(obj, list):
        return ("l", type(obj).__name__, id(obj), tuple(_snap(v, memo) for v in list.__iter__(obj)))
    if isinstance(obj, tuple):
        return ("t", id(obj), tuple(_snap(v, memo) for v in obj))
    if isinstance(obj, (str, int)) and type(obj) not in (str, int, bool):
        return ("sub", type(obj).__name__, id(obj))
    return ("v", type(obj).__name__, obj)


def _containers(obj, out=None):
    """ids of every dict/list reachable from OBJ."""
    if out is None:
        out = set()
    if isinstance(obj, (dict, list)) and id(obj) not in out:
        out.add(id(obj))
        for v in dict.values(obj) if isinstance(obj, dict) else list.__iter__(obj):
            _containers(v, out)
    return out


# -- row runners -------------------------------------------------------------------


class Fail(AssertionError):
    pass


def _need(cond, why):
    if not cond:
        raise Fail(why)


def _exact_receipt(receipt, expect, state):
    _need(type(receipt) is dict, "receipt not an exact dict")
    _need(tuple(dict.keys(receipt)) == RECEIPT_FIELDS, "receipt field order")
    _need(_canon(receipt) == _canon(expect), f"receipt differs {receipt!r}"[:200])
    for field in ("op", "state_id"):
        _need(type(receipt[field]) is str, f"receipt {field} not exact str")
    for field in ("job_id", "status"):
        _need(receipt[field] is None or type(receipt[field]) is str, f"receipt {field} type")
    for field in ("attempts", "lease_expires_at"):
        _need(receipt[field] is None or type(receipt[field]) is int, f"receipt {field} type")
    _need(receipt["state_id"] == _state_id(state), "state_id does not bind the committed state")


def _checked_apply(engine, state, request, expect, previous=None):
    """One successful apply with every per-call pin."""
    req_snap = _snap(request)
    req_boxes = _containers(request)
    old_jobs = state["jobs"] if type(state) is dict else None
    old_snap = _snap(old_jobs)
    state_id = id(state)
    receipt = engine.apply(state, request)
    _need(id(state) == state_id and type(state) is dict, "commit not in place")
    _exact_receipt(receipt, expect, state)
    _need(_snap(request) == req_snap, "request mutated")
    _need(not (_containers(state) & req_boxes), "committed state aliases the request")
    _need(not (_containers(receipt) & _containers(state)), "receipt aliases the state")
    if old_jobs is not state["jobs"]:
        _need(_snap(old_jobs) == old_snap, "replaced prior jobs mutated")
    if previous is not None:
        _need(receipt is not previous, "receipt reused across calls")
    if type(request) is dict and request.get("op") == "enqueue":
        _need(receipt["job_id"] == _job_id(request["dedupe_key"]), "job_id not derived")
    return receipt


def _rejects(engine, state, request, failure):
    snaps = (_snap(state), _snap(request))
    try:
        engine.apply(state, request)
    except QueueError as exc:
        _need(type(exc) is QueueError, "error subclass")
        _need(exc.failure_class == failure, f"class {exc.failure_class} != {failure}")
        _need(exc.code == FAILURE_MAPPING[failure], "code not mapped")
        _need(exc.__cause__ is None and exc.__context__ is None, "typed error chained")
    else:
        raise Fail(f"accepted, expected {failure}")
    _need((_snap(state), _snap(request)) == snaps, "rejected apply changed state or request")


def _run_ok(cls, row):
    engine = cls()
    state = copy.deepcopy(row["state"])
    previous = None
    for request, expect in zip(row["requests"], row["expect_receipts"], strict=True):
        previous = _checked_apply(engine, state, copy.deepcopy(request), expect, previous)
    _need(_canon(state) == _canon(row["expect_state"]), "committed state differs")
    # determinism by value on the SAME instance over fresh copies
    again = copy.deepcopy(row["state"])
    receipts = [engine.apply(again, copy.deepcopy(r)) for r in row["requests"]]
    _need(_canon(receipts) == _canon(row["expect_receipts"]), "not deterministic")
    _need(_canon(again) == _canon(state), "state not deterministic")


def _reference_result(state, request):
    state = copy.deepcopy(state)
    receipt = _reference.QueueEngine().apply(state, copy.deepcopy(request))
    return receipt, state


def _run_malformed(cls, row):
    engine = cls()
    _rejects(
        engine, copy.deepcopy(row["state"]), copy.deepcopy(row["request"]), row["expect_failure"]
    )
    repair = row["minimal_repair"]
    _need(set(repair) in ({"state"}, {"request"}), "repair not single-locus")
    state = copy.deepcopy(repair.get("state", row["state"]))
    request = copy.deepcopy(repair.get("request", row["request"]))
    want_receipt, want_state = _reference_result(state, request)
    _checked_apply(engine, state, request, want_receipt)
    _need(_canon(state) == _canon(want_state), "repaired state differs from the reference")


def _run_rollback(cls, row):
    engine = cls()
    state = copy.deepcopy(row["state"])
    _rejects(engine, state, copy.deepcopy(row["rejected_request"]), row["expect_failure"])
    _checked_apply(engine, state, copy.deepcopy(row["request"]), row["expect_receipt"])
    _need(_canon(state) == _canon(row["expect_state"]), "follow-up state differs")


RUNNERS = {
    "happy": _run_ok,
    "boundary": _run_ok,
    "malformed": _run_malformed,
    "rollback": _run_rollback,
}


# -- totality and semantic probes ---------------------------------------------------


def _build_base():
    """ready a (p5), leased b (w1, expires 500), done c, dead d, ready e
    (p1), all through the reference engine."""
    eng = _reference.QueueEngine()
    state = {"jobs": [], "next_seq": 0}
    for key, prio in (("a", 5), ("b", 2), ("c", 4), ("d", 3), ("e", 7)):
        eng.apply(
            state,
            {"op": "enqueue", "dedupe_key": key, "priority": prio, "payload": {"k": [key, 1]}},
        )
    b, c, d = _job_id("b"), _job_id("c"), _job_id("d")
    # c: claim at 0, ack -> done
    eng.apply(state, {"op": "claim", "worker": "w1", "now": 0, "lease_ms": 50})
    eng.apply(state, {"op": "ack", "job_id": b, "worker": "w1", "now": 1})
    for job in state["jobs"]:
        if job["job_id"] == d:
            job.update(status="dead", attempts=MAX_ATTEMPTS)
        if job["job_id"] == c:
            job.update(status="leased", attempts=1, lease_owner="w1", lease_expires_at=500)
    _reference._validate_state(state)
    return state


_BASE = _build_base()
LEASED, DONE, DEAD = _job_id("c"), _job_id("b"), _job_id("d")
R_ENQ = {"op": "enqueue", "dedupe_key": "n1", "priority": 3, "payload": {"a": [1, {"b": None}]}}
R_CLAIM = {"op": "claim", "worker": "w2", "now": 10, "lease_ms": 100}
R_ACK = {"op": "ack", "job_id": LEASED, "worker": "w1", "now": 10}
R_NACK = {"op": "nack", "job_id": LEASED, "worker": "w1", "now": 10}
REQUESTS = {"enqueue": R_ENQ, "claim": R_CLAIM, "ack": R_ACK, "nack": R_NACK}


def _base():
    return copy.deepcopy(_BASE)


def _req(base, edit=None):
    def build():
        request = copy.deepcopy(base)
        return _base(), (request if edit is None else edit(request))

    return build


def _st(edit, request=R_CLAIM):
    def build():
        state = _base()
        out = edit(state)
        return (state if out is None else out), copy.deepcopy(request)

    return build


def _job_at(position):
    return {"first": 0, "middle": 2, "last": -1}[position]


def _set_job(position, fn):
    def edit(state):
        jobs = state["jobs"]
        i = _job_at(position)
        new = fn(jobs[i])
        if new is not None:
            jobs[i] = new

    return edit


def _nested(depth):
    """DEPTH nested lists."""
    node = []
    for _ in range(depth - 1):
        node = [node]
    return node


def _cyclic():
    node = []
    node.append(node)
    return node


def _aliased():
    shared = [1]
    return {"x": shared, "y": shared}


def _full_state(count):
    """COUNT valid ready jobs with distinct payload containers."""
    jobs = [
        {
            "job_id": _job_id(f"m{i}"),
            "seq": i,
            "priority": 5,
            "payload": {"i": [i]},
            "status": "ready",
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
        }
        for i in range(count)
    ]
    return {"jobs": jobs, "next_seq": count}


def _probe_builders():
    out = {}
    # request containers
    for label, value in (
        ("none", None),
        ("list", [R_ENQ]),
        ("text", "enqueue"),
        ("tuple", tuple(R_ENQ.items())),
    ):
        out[f"request-{label}"] = (_req(R_ENQ, lambda r, v=value: v), MQR)
    for op, base in REQUESTS.items():
        out[f"{op}-dict-subclass"] = (_req(base, _DictSub), MQR)
        out[f"{op}-lying-dict"] = (_req(base, _Lying), MQR)
        out[f"{op}-extra-field"] = (_req(base, lambda r: {**r, "force": True}), MQR)
        out[f"{op}-extra-colliding-key"] = (_req(base, lambda r: {**r, HK("force"): 1}), MQR)
        for field in base:
            for form in KEY_FORMS:
                out[f"{op}-key-{field}-{form}"] = (
                    _req(base, lambda r, f=field, m=form: _rekey(r, f, _hkey(m, f))),
                    MQR,
                )
            out[f"{op}-drop-{field}"] = (
                _req(base, lambda r, f=field: {k: v for k, v in r.items() if k != f}),
                MQR,
            )
            out[f"{op}-renamed-{field}"] = (
                _req(base, lambda r, f=field: _rekey(r, f, f + "_x")),
                MQR,
            )
            value = base[field]
            if type(value) is str:
                out[f"{op}-{field}-str-subclass"] = (
                    _req(base, lambda r, f=field: {**r, f: SK(r[f])}),
                    MQR,
                )
                out[f"{op}-{field}-trailing-newline"] = (
                    _req(base, lambda r, f=field: {**r, f: r[f] + "\n"}),
                    MQR,
                )
            if type(value) is int:
                for label, fn in (
                    ("int-subclass", _IntSub),
                    ("bool", lambda v: bool(v)),
                    ("float", float),
                    ("text", str),
                ):
                    out[f"{op}-{field}-{label}"] = (
                        _req(base, lambda r, f=field, g=fn: {**r, f: g(r[f])}),
                        MQR,
                    )
    for label, value in (
        ("case", "Enqueue"),
        ("empty", ""),
        ("none", None),
        ("sub", SK("enqueue")),
    ):
        out[f"op-{label}"] = (_req(R_ENQ, lambda r, v=value: {**r, "op": v}), MQR)
    # grammar and bounds
    for label, value, cls in (
        ("empty", "", MQR),
        ("129", "k" * 129, MQR),
        ("space", "a b", MQR),
        ("non-ascii", "é", MQR),
        ("128", "k" * 128, ACCEPT),
        ("one", "Z", ACCEPT),
    ):
        out[f"dedupe-key-{label}"] = (_req(R_ENQ, lambda r, v=value: {**r, "dedupe_key": v}), cls)
    for label, value, cls in (("empty", "", MQR), ("65", "w" * 65, MQR), ("64", "w" * 64, ACCEPT)):
        out[f"worker-{label}"] = (_req(R_CLAIM, lambda r, v=value: {**r, "worker": v}), cls)
    out["job-id-upper"] = (_req(R_ACK, lambda r: {**r, "job_id": r["job_id"].upper()}), MQR)
    out["job-id-short"] = (_req(R_ACK, lambda r: {**r, "job_id": r["job_id"][:-1]}), MQR)
    for value, cls in ((-1, MQR), (10, MQR), (0, ACCEPT), (9, ACCEPT)):
        out[f"priority-{value}"] = (_req(R_ENQ, lambda r, v=value: {**r, "priority": v}), cls)
    for value, cls in ((0, MQR), (3_600_001, MQR), (1, ACCEPT), (3_600_000, ACCEPT)):
        out[f"lease-ms-{value}"] = (_req(R_CLAIM, lambda r, v=value: {**r, "lease_ms": v}), cls)
    for label, value, cls in (
        ("negative", -1, MQR),
        ("past-max", MAX_NOW + 1, MQR),
        ("huge", 10**5000, MQR),
    ):
        out[f"now-{label}"] = (_req(R_ACK, lambda r, v=value: {**r, "now": v}), cls)
    out["claim-at-clock-limit"] = (_req(R_CLAIM, lambda r: {**r, "now": MAX_NOW - 100}), ACCEPT)
    out["claim-past-clock-limit"] = (_req(R_CLAIM, lambda r: {**r, "now": MAX_NOW - 99}), MQR)
    # payloads
    for label, value, cls in (
        ("cyclic", _cyclic, MQR),
        ("aliased", _aliased, MQR),
        ("too-deep", lambda: _nested(MAX_DEPTH + 1), MQR),
        ("depth-limit", lambda: _nested(MAX_DEPTH), ACCEPT),
        ("int-too-long", lambda: 10**MAX_INT_DIGITS, MQR),
        ("int-digit-limit", lambda: 10**MAX_INT_DIGITS - 1, ACCEPT),
        ("tuple", lambda: (1, 2), MQR),
        ("set", lambda: {1}, MQR),
        ("bytes", lambda: b"x", MQR),
        ("nan", lambda: float("nan"), MQR),
        ("int-key", lambda: {1: 2}, MQR),
        ("str-subclass-key", lambda: {SK("a"): 1}, MQR),
        ("colliding-key", lambda: {HK("a"): 1}, MQR),
        ("str-subclass-value", lambda: {"a": SK("x")}, MQR),
        ("nested-str-subclass", lambda: {"a": [1, {"b": SK("x")}]}, MQR),
        ("int-subclass-value", lambda: [_IntSub(1)], MQR),
        ("dict-subclass", lambda: _DictSub(a=1), MQR),
        ("list-subclass", lambda: _ListSub([1]), MQR),
        ("lying-dict", lambda: _Lying(a=1), MQR),
    ):
        out[f"payload-{label}"] = (_req(R_ENQ, lambda r, v=value: {**r, "payload": v()}), cls)
    # state containers and keys
    for label, fn in (
        ("list", lambda s: [s]),
        ("dict-subclass", _DictSub),
        ("lying-dict", _Lying),
        ("extra-key", lambda s: {**s, "x": 1}),
        ("drop-jobs", lambda s: {"next_seq": s["next_seq"]}),
        ("renamed-jobs", lambda s: _rekey(s, "jobs", "jobz")),
        ("jobs-tuple", lambda s: {**s, "jobs": tuple(s["jobs"])}),
        ("jobs-list-subclass", lambda s: {**s, "jobs": _ListSub(s["jobs"])}),
        ("jobs-logged-list", lambda s: {**s, "jobs": _LogList(s["jobs"])}),
        ("next-seq-negative", lambda s: {**s, "next_seq": -1}),
        ("next-seq-past-max", lambda s: {**s, "next_seq": MAX_NOW + 1}),
        ("next-seq-bool", lambda s: {"jobs": [], "next_seq": True}),
        ("next-seq-int-subclass", lambda s: {**s, "next_seq": _IntSub(s["next_seq"])}),
        ("next-seq-text", lambda s: {**s, "next_seq": "5"}),
        ("next-seq-at-last-seq", lambda s: {**s, "next_seq": 4}),
    ):
        out[f"state-{label}"] = (_st(fn), CQ)
    out["state-none"] = (lambda: (None, copy.deepcopy(R_CLAIM)), CQ)
    for field in ("jobs", "next_seq"):
        for form in KEY_FORMS:
            out[f"state-key-{field}-{form}"] = (
                _st(lambda s, f=field, m=form: _rekey(s, f, _hkey(m, f))),
                CQ,
            )
    # jobs at the first, middle and last position
    for pos in ("first", "middle", "last"):
        jobs = {
            "not-a-dict": lambda j: [j],
            "dict-subclass": _DictSub,
            "lying-dict": _Lying,
            "extra-field": lambda j: {**j, "x": 1},
            "extra-colliding-key": lambda j: {**j, HK("x"): 1},
        }
        for field in JOB_FIELDS:
            jobs[f"drop-{field}"] = lambda j, f=field: {k: v for k, v in j.items() if k != f}
            jobs[f"renamed-{field}"] = lambda j, f=field: _rekey(j, f, f + "_x")
            for form in KEY_FORMS:
                jobs[f"key-{field}-{form}"] = lambda j, f=field, m=form: _rekey(j, f, _hkey(m, f))
        for field in ("job_id", "status"):
            jobs[f"{field}-str-subclass"] = lambda j, f=field: {**j, f: SK(j[f])}
        for field in ("seq", "priority", "attempts"):
            jobs[f"{field}-int-subclass"] = lambda j, f=field: {**j, f: _IntSub(j[f])}
            jobs[f"{field}-float"] = lambda j, f=field: {**j, f: float(j[f])}
        jobs["priority-bool"] = lambda j: {**j, "priority": True}
        jobs["payload-str-subclass"] = lambda j: {**j, "payload": {"k": [SK("x"), 1]}}
        jobs["job-id-upper"] = lambda j: {**j, "job_id": j["job_id"].upper()}
        jobs["job-id-newline"] = lambda j: {**j, "job_id": j["job_id"] + "\n"}
        for label, fn in jobs.items():
            out[f"job-{pos}-{label}"] = (_st(_set_job(pos, fn)), CQ)
    # the leased job (index 2) owner and expiry types
    out["job-leased-owner-str-subclass"] = (
        _st(_set_job("middle", lambda j: {**j, "lease_owner": SK("w1")})),
        CQ,
    )
    out["job-leased-expiry-int-subclass"] = (
        _st(_set_job("middle", lambda j: {**j, "lease_expires_at": _IntSub(500)})),
        CQ,
    )

    # state invariants
    def dup(state):
        state["jobs"][1]["job_id"] = state["jobs"][0]["job_id"]

    def seq_equal(state):
        state["jobs"][1]["seq"] = state["jobs"][0]["seq"]

    def seq_order(state):
        state["jobs"][0]["seq"], state["jobs"][1]["seq"] = 1, 0

    def payload_alias(state):
        state["jobs"][1]["payload"] = state["jobs"][0]["payload"]

    def too_many(state):
        state.update(_full_state(MAX_JOBS + 1))

    for label, fn in (
        ("duplicate-job-id", dup),
        ("seq-repeated", seq_equal),
        ("seq-decreasing", seq_order),
        ("payload-alias-across-jobs", payload_alias),
        ("payload-cyclic", _set_job("first", lambda j: {**j, "payload": _cyclic()})),
        ("payload-too-deep", _set_job("first", lambda j: {**j, "payload": _nested(MAX_DEPTH + 1)})),
        ("priority-10", _set_job("first", lambda j: {**j, "priority": 10})),
        ("status-unknown", _set_job("first", lambda j: {**j, "status": "running"})),
        ("attempts-6", _set_job("first", lambda j: {**j, "attempts": 6})),
        ("attempts-negative", _set_job("first", lambda j: {**j, "attempts": -1})),
        ("leased-without-owner", _set_job("middle", lambda j: {**j, "lease_owner": None})),
        ("leased-expiry-zero", _set_job("middle", lambda j: {**j, "lease_expires_at": 0})),
        ("leased-attempts-zero", _set_job("middle", lambda j: {**j, "attempts": 0})),
        ("leased-owner-grammar", _set_job("middle", lambda j: {**j, "lease_owner": "w 1"})),
        ("ready-with-owner", _set_job("first", lambda j: {**j, "lease_owner": "w1"})),
        ("ready-with-expiry", _set_job("first", lambda j: {**j, "lease_expires_at": 5})),
        ("done-attempts-zero", _set_job("first", lambda j: {**j, "status": "done"})),
        ("dead-below-max", _set_job("first", lambda j: {**j, "status": "dead", "attempts": 4})),
        ("too-many-jobs", too_many),
    ):
        out[f"invariant-{label}"] = (_st(fn), CQ)
    # transition failures
    out["ack-unknown-job"] = (_req(R_ACK, lambda r: {**r, "job_id": _job_id("zz")}), UJ)
    out["nack-unknown-job"] = (_req(R_NACK, lambda r: {**r, "job_id": _job_id("zz")}), UJ)
    out["ack-non-owner"] = (_req(R_ACK, lambda r: {**r, "worker": "w2"}), LC)
    out["ack-at-expiry"] = (_req(R_ACK, lambda r: {**r, "now": 500}), LC)
    out["ack-before-expiry"] = (_req(R_ACK, lambda r: {**r, "now": 499}), ACCEPT)
    out["nack-at-expiry"] = (_req(R_NACK, lambda r: {**r, "now": 500}), LC)
    out["ack-ready-job"] = (_req(R_ACK, lambda r: {**r, "job_id": _job_id("a")}), LC)
    out["ack-done-job"] = (_req(R_ACK, lambda r: {**r, "job_id": DONE}), LC)
    out["nack-dead-job"] = (_req(R_NACK, lambda r: {**r, "job_id": DEAD}), LC)
    out["reenqueue-other-priority"] = (
        _req(R_ENQ, lambda r: {**r, "dedupe_key": "a", "priority": 4}),
        DC,
    )
    out["reenqueue-other-payload"] = (
        _req(R_ENQ, lambda r: {**r, "dedupe_key": "a", "priority": 5, "payload": {"k": ["a", 2]}}),
        DC,
    )
    out["reenqueue-identical"] = (
        _req(R_ENQ, lambda r: {**r, "dedupe_key": "a", "priority": 5, "payload": {"k": ["a", 1]}}),
        ACCEPT,
    )
    out["reenqueue-payload-true-for-1"] = (
        _req(
            R_ENQ, lambda r: {**r, "dedupe_key": "a", "priority": 5, "payload": {"k": ["a", True]}}
        ),
        DC,
    )
    out["reenqueue-payload-float-for-1"] = (
        _req(
            R_ENQ, lambda r: {**r, "dedupe_key": "a", "priority": 5, "payload": {"k": ["a", 1.0]}}
        ),
        DC,
    )
    out["enqueue-seq-exhausted"] = (
        _st(lambda s: {**s, "next_seq": MAX_NOW}, R_ENQ),
        CE,
    )
    out["enqueue-new-at-max-jobs"] = (_st(lambda s: _full_state(MAX_JOBS), R_ENQ), CE)
    out["reenqueue-identical-at-max-jobs"] = (
        _st(
            lambda s: _full_state(MAX_JOBS),
            {"op": "enqueue", "dedupe_key": "m7", "priority": 5, "payload": {"i": [7]}},
        ),
        ACCEPT,
    )
    out["enqueue-last-free-seq"] = (
        _st(lambda s: {**s, "next_seq": MAX_NOW - 1}, R_ENQ),
        ACCEPT,
    )
    # semantic accepts: ordering, expiry, dead letter, nack
    out["claim-orders-by-priority"] = (_req(R_CLAIM), ACCEPT)
    out["claim-reclaims-expired-at-now"] = (_req(R_CLAIM, lambda r: {**r, "now": 500}), ACCEPT)
    out["claim-skips-unexpired"] = (_req(R_CLAIM, lambda r: {**r, "now": 499}), ACCEPT)
    out["nack-keeps-attempts"] = (_req(R_NACK), ACCEPT)
    out["claim-marks-exhausted-dead"] = (
        _st(_set_job("last", lambda j: {**j, "priority": 0, "attempts": MAX_ATTEMPTS}), R_CLAIM),
        ACCEPT,
    )
    out["claim-empty-after-dead-marking"] = (
        _st(
            lambda s: {
                "jobs": [
                    {**j, "attempts": MAX_ATTEMPTS}
                    if j["status"] == "ready"
                    else {**j, "lease_expires_at": 20}
                    if j["status"] == "leased"
                    else j
                    for j in s["jobs"]
                ],
                "next_seq": s["next_seq"],
            },
            {**R_CLAIM, "now": 10},
        ),
        ACCEPT,
    )
    # dead-marking an EXPIRED leased job clears its lease, both when a
    # claimable job follows and when it is the only job
    out["claim-dead-marks-expired-leased"] = (
        _st(_set_job("middle", lambda j: {**j, "attempts": MAX_ATTEMPTS}), EXPIRED_CLAIM),
        ACCEPT,
    )
    out["claim-dead-marks-only-expired-leased"] = (
        _st(lambda s: _only_expired_leased(), EXPIRED_CLAIM),
        ACCEPT,
    )
    # the scan continues past a dead-marked job to the next ready one
    out["claim-continues-past-dead-marked"] = (
        _st(_set_job("first", lambda j: {**j, "priority": 0, "attempts": MAX_ATTEMPTS})),
        ACCEPT,
    )
    # request errors take precedence over state corruption
    for label, (edit, request) in _both_bad_rows().items():
        out[f"precedence-{label}"] = (_st(edit, request), MQR)
    return out


EXPIRED_CLAIM = {**R_CLAIM, "now": 500}


def _only_expired_leased():
    job = next(j for j in _base()["jobs"] if j["job_id"] == LEASED)
    return {"jobs": [{**job, "attempts": MAX_ATTEMPTS}], "next_seq": _BASE["next_seq"]}


def _both_bad_rows():
    """(state edit, request) pairs where the request is malformed AND the
    state is corrupt; the request error must win."""

    def next_seq_negative(state):
        state["next_seq"] = -1

    def duplicate_id(state):
        state["jobs"][1]["job_id"] = state["jobs"][0]["job_id"]

    return {
        "bad-op-and-negative-next-seq": (next_seq_negative, {**R_CLAIM, "op": "peek"}),
        "str-subclass-worker-and-duplicate-id": (duplicate_id, {**R_CLAIM, "worker": SK("w2")}),
        "eq-raises-worker-and-duplicate-id": (
            duplicate_id,
            {**R_CLAIM, "worker": _EqRaises("w2")},
        ),
        "request-none-and-negative-next-seq": (next_seq_negative, None),
    }


PROBES = _probe_builders()
PROBE_MANIFEST = tuple(PROBES)


def _totality_ok(cls, name):
    build, expect = PROBES[name]
    engine = cls()
    state, request = build()
    if expect == ACCEPT:
        ref_state, ref_request = build()
        want_receipt, want_state = _reference_result(ref_state, ref_request)
        _checked_apply(engine, state, request, want_receipt)
        _need(_canon(state) == _canon(want_state), "state differs from the reference")
        return
    HOSTILE.clear()
    _Armed.on = True
    try:
        _rejects(engine, state, request, expect)
    finally:
        _Armed.on = False
    _need(HOSTILE == [], f"hostile calls {sorted(set(HOSTILE))}")


def _probe(cls, only=None):
    """Every manifest row and every totality probe through CLS; returns
    the failing labels. Any BaseException escaping a row is a failure."""
    failures = []
    jobs = []
    for section, manifest in MANIFESTS.items():
        rows = {r["name"]: r for r in CASES[section]}
        for name in manifest:
            jobs.append((f"{section}:{name}", lambda s=section, r=rows[name]: RUNNERS[s](cls, r)))
    for name in PROBE_MANIFEST:
        jobs.append((f"totality:{name}", lambda n=name: _totality_ok(cls, n)))
    for label, job in jobs:
        if only is not None and label not in only:
            continue
        try:
            job()
        except BaseException as error:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(error).__name__} {error}"[:200])
    return failures


# -- tests ---------------------------------------------------------------------------


def test_fixture_closure():
    _validate_closure(CASES)


def test_closure_rejects_substitution():
    for section in EXPECTED_NAMES:
        tampered = copy.deepcopy(CASES)
        row = tampered[section][0]
        row["name"] = row["name"]  # same name, different content
        key = "why" if "why" in row else "defect"
        row[key] = row[key] + " "
        with pytest.raises(AssertionError):
            _validate_closure(tampered)
        swapped = copy.deepcopy(CASES)
        swapped[section].reverse()
        with pytest.raises(AssertionError):
            _validate_closure(swapped)


@pytest.mark.parametrize("section", sorted(MANIFESTS))
def test_fixture_rows(section):
    labels = {f"{section}:{name}" for name in MANIFESTS[section]}
    assert _probe(QueueEngine, only=labels) == []


def test_totality_and_semantic_probes():
    assert _probe(QueueEngine, only={f"totality:{n}" for n in PROBE_MANIFEST}) == []


def test_probes_reach_every_failure_class_and_accept():
    reached = {expect for _build, expect in PROBES.values()}
    assert reached == CLASSES | {ACCEPT}


def test_hostile_rows_cover_every_boundary():
    for op, base in REQUESTS.items():
        for kind in ("dict-subclass", "lying-dict", "extra-colliding-key"):
            assert f"{op}-{kind}" in PROBES
        for field in base:
            for form in KEY_FORMS:
                assert f"{op}-key-{field}-{form}" in PROBES
            assert f"{op}-renamed-{field}" in PROBES
    for pos in ("first", "middle", "last"):
        for kind in ("dict-subclass", "lying-dict", "not-a-dict", "extra-colliding-key"):
            assert f"job-{pos}-{kind}" in PROBES
        for field in JOB_FIELDS:
            for form in KEY_FORMS:
                assert f"job-{pos}-key-{field}-{form}" in PROBES
    for field in ("jobs", "next_seq"):
        for form in KEY_FORMS:
            assert f"state-key-{field}-{form}" in PROBES
    for kind in ("dict-subclass", "lying-dict", "jobs-list-subclass"):
        assert f"state-{kind}" in PROBES
    for kind in ("str-subclass-value", "nested-str-subclass", "lying-dict", "dict-subclass"):
        assert f"payload-{kind}" in PROBES


# -- mutants -------------------------------------------------------------------------

REFERENCE_TEXT = inspect.getsource(_reference)


def _source_mutant(name, edits):
    """The reference module with EDITS (old, new) applied, each matching
    exactly once; its QueueError is rebound to the bound class so typed
    failures stay comparable. Returns the mutant engine class."""
    text = REFERENCE_TEXT
    for old, new in edits:
        assert text.count(old) == 1, (name, old)
        text = text.replace(old, new)
    module = types.ModuleType(f"tests._t0286_mutant_{name}")
    module.__file__ = _reference.__file__
    exec(compile(text, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.QueueError = QueueError
    return module.QueueEngine


class WrongCode(QueueEngine):
    def apply(self, state, request):
        try:
            return super().apply(state, request)
        except QueueError as exc:
            exc.code = "internal"
            raise


class SubclassError(QueueEngine):
    def apply(self, state, request):
        try:
            return super().apply(state, request)
        except QueueError as exc:
            error = type("QueueErrorSub", (QueueError,), {})(exc.failure_class, exc.code)
        raise error


class ChainedError(QueueEngine):
    def apply(self, state, request):
        try:
            return super().apply(state, request)
        except QueueError as exc:
            raise QueueError(exc.failure_class, exc.code) from exc


class CachedReceipt(QueueEngine):
    _last = None

    def apply(self, state, request):
        receipt = super().apply(state, request)
        if self._last is not None and self._last == receipt:
            return self._last
        self._last = receipt
        return receipt


class MutatesRequest(QueueEngine):
    def apply(self, state, request):
        receipt = super().apply(state, request)
        request.pop("op", None)
        return receipt


class NewStateObject(QueueEngine):
    def apply(self, state, request):
        copy_ = dict(state) if type(state) is dict else state
        receipt = super().apply(copy_, request)
        return receipt


_OPS_KEY_GUARD = '    if not all(type(key) is str for key in keys) or "op" not in keys:'
_SM = {
    "priority-bound-open": [
        (
            '        if not _int_in(request["priority"], 0, MAX_PRIORITY):',
            '        if not _int_in(request["priority"], 0, MAX_PRIORITY + 1):',
        )
    ],
    "int-isinstance": [
        (
            "    return type(value) is int and lo <= value <= hi",
            "    return isinstance(value, int) and lo <= value <= hi",
        )
    ],
    "grammar-match": [
        (
            "    return type(value) is str and pattern.fullmatch(value) is not None",
            "    return type(value) is str and pattern.match(value) is not None",
        )
    ],
    "grammar-isinstance": [
        (
            "    return type(value) is str and pattern.fullmatch(value) is not None",
            "    return isinstance(value, str) and pattern.fullmatch(value) is not None",
        )
    ],
    "request-key-guard-off": [(_OPS_KEY_GUARD, '    if "op" not in keys:')],
    "exact-keys-guard-off": [
        (
            "    if not all(type(key) is str for key in keys):\n        return False\n",
            "",
        )
    ],
    "request-dict-isinstance": [
        (
            "    if type(request) is not dict:\n",
            "    if not isinstance(request, dict):\n",
        )
    ],
    "state-dict-isinstance": [
        (
            "    if type(state) is not dict or not _exact_keys(state, _STATE_FIELDS):",
            "    if not isinstance(state, dict) or not _exact_keys(state, _STATE_FIELDS):",
        )
    ],
    "jobs-list-isinstance": [
        (
            "    if type(jobs) is not list or",
            "    if not isinstance(jobs, list) or",
        )
    ],
    "job-dict-isinstance": [
        (
            "        if type(job) is not dict or not _exact_keys(job, _JOB_FIELDS):",
            "        if not isinstance(job, dict) or not _exact_keys(job, _JOB_FIELDS):",
        )
    ],
    "lease-ms-zero-allowed": [
        (
            '        if not _int_in(request["lease_ms"], 1, MAX_LEASE_MS):',
            '        if not _int_in(request["lease_ms"], 0, MAX_LEASE_MS):',
        )
    ],
    "clock-limit-strict": [
        (
            '        if request["now"] + request["lease_ms"] > clock_limit:',
            '        if request["now"] + request["lease_ms"] >= clock_limit:',
        )
    ],
    "clock-limit-off": [
        (
            '        if request["now"] + request["lease_ms"] > clock_limit:',
            "        if False:",
        )
    ],
    "max-jobs-off": [("    if len(jobs) > MAX_JOBS:\n", "    if False:\n")],
    "duplicate-id-allowed": [
        (
            "        if not _grammar(jid, _JOB_RE) or jid in seen_ids:",
            "        if not _grammar(jid, _JOB_RE):",
        )
    ],
    "seq-non-decreasing": [
        (
            "        if not _int_in(seq, prior_seq + 1, next_seq - 1):",
            "        if not _int_in(seq, prior_seq, next_seq - 1):",
        )
    ],
    "seq-at-next-seq": [
        (
            "        if not _int_in(seq, prior_seq + 1, next_seq - 1):",
            "        if not _int_in(seq, prior_seq + 1, next_seq):",
        )
    ],
    "state-priority-open": [
        (
            '        if not _int_in(job["priority"], 0, MAX_PRIORITY):',
            '        if not _int_in(job["priority"], 0, MAX_PRIORITY + 1):',
        )
    ],
    "status-type-unchecked": [
        (
            "        if type(status) is not str or status not in _STATUSES:",
            "        if status not in _STATUSES:",
        )
    ],
    "attempts-open": [
        (
            "        if not _int_in(attempts, 0, MAX_ATTEMPTS):",
            "        if not _int_in(attempts, 0, MAX_ATTEMPTS + 1):",
        )
    ],
    "leased-owner-unchecked": [
        (
            "            if not _grammar(owner, _WORKER_RE) or not _int_in(expires, 1, MAX_NOW):",
            "            if not _int_in(expires, 1, MAX_NOW):",
        )
    ],
    "leased-expiry-zero": [
        (
            "            if not _grammar(owner, _WORKER_RE) or not _int_in(expires, 1, MAX_NOW):",
            "            if not _grammar(owner, _WORKER_RE) or not _int_in(expires, 0, MAX_NOW):",
        )
    ],
    "leased-attempts-unchecked": [
        (
            "            if attempts < 1:\n                _fail",
            "            if False:\n                _fail",
        )
    ],
    "idle-lease-fields-unchecked": [
        (
            "        elif owner is not None or expires is not None:",
            "        elif False:",
        )
    ],
    "dead-attempts-unchecked": [
        (
            '        if status == "dead" and attempts != MAX_ATTEMPTS:',
            "        if False:",
        )
    ],
    "done-attempts-unchecked": [
        ('        if status == "done" and attempts < 1:', "        if False:")
    ],
    "cross-job-alias-allowed": [
        (
            '        if not _admissible([job["payload"]], containers):',
            '        if not _admissible([job["payload"]], set()):',
        )
    ],
    "request-payload-unchecked": [
        (
            '        if not _admissible([request["payload"]], set()):\n'
            '            _fail("malformed_queue_request")\n',
            "",
        )
    ],
    "dedupe-by-value": [
        (
            '_canon(existing["payload"]) != _canon(\n'
            '                    req["payload"]\n                )',
            'existing["payload"] != req["payload"]',
        )
    ],
    "dedupe-ignores-priority": [('existing["priority"] != req["priority"] or ', "")],
    "capacity-off": [
        (
            '            if len(work["jobs"]) >= MAX_JOBS:\n'
            '                _fail("capacity_exceeded")\n',
            "",
        )
    ],
    "seq-exhaustion-late": [
        (
            '            if work["next_seq"] >= self.SEQ_LIMIT:',
            '            if work["next_seq"] > self.SEQ_LIMIT:',
        )
    ],
    "capacity-before-dedupe": [
        (
            "            existing = self._find(work, jid)\n",
            '            if work["next_seq"] >= self.SEQ_LIMIT:\n'
            '                _fail("capacity_exceeded")\n'
            "            existing = self._find(work, jid)\n",
        )
    ],
    "order-by-seq-only": [
        ('key=lambda j: (j["priority"], j["seq"]),', 'key=lambda j: (j["seq"],),')
    ],
    "order-priority-reversed": [
        ('key=lambda j: (j["priority"], j["seq"]),', 'key=lambda j: (-j["priority"], j["seq"]),')
    ],
    "expiry-strict": [
        (
            '        job["status"] == "leased" and job["lease_expires_at"] <= now',
            '        job["status"] == "leased" and job["lease_expires_at"] < now',
        )
    ],
    "dead-letter-off": [
        ('                if job["attempts"] >= MAX_ATTEMPTS:\n', "                if False:\n")
    ],
    "attempts-not-counted": [
        (
            '                    attempts=job["attempts"] + 1,',
            '                    attempts=job["attempts"],',
        )
    ],
    "lease-expiry-short": [
        (
            '                    lease_expires_at=now + req["lease_ms"],',
            '                    lease_expires_at=now + req["lease_ms"] - 1,',
        )
    ],
    "ack-at-expiry-allowed": [
        (
            '            or not req["now"] < job["lease_expires_at"]',
            '            or not req["now"] <= job["lease_expires_at"]',
        )
    ],
    "owner-unchecked": [('            or job["lease_owner"] != req["worker"]\n', "")],
    "nack-marks-done": [
        ('            status="done" if op == "ack" else "ready",', '            status="done",')
    ],
    "unknown-as-lease-conflict": [
        ('            _fail("unknown_job")', '            _fail("lease_conflict")')
    ],
    "no-commit": [("        state.clear()\n        state.update(work)\n", "")],
    "live-work": [("        work = json.loads(_canon(state))", "        work = state")],
    "shallow-work": [
        (
            "        work = json.loads(_canon(state))",
            "        work = {**state, 'jobs': list(state['jobs'])}",
        )
    ],
    "payload-not-frozen": [
        (
            '        frozen["payload"] = json.loads(_canon(request["payload"]))',
            '        frozen["payload"] = request["payload"]',
        )
    ],
    "stale-state-id": [
        (
            '            "state_id": state_id_for(work),',
            '            "state_id": state_id_for(state),',
        )
    ],
    "dead-marking-keeps-lease": [
        (
            'job.update(status="dead", lease_owner=None, lease_expires_at=None)',
            'job.update(status="dead")',
        )
    ],
    "dead-marking-stops-scan": [
        (
            "lease_expires_at=None)\n                    continue\n",
            "lease_expires_at=None)\n                    return None\n",
        )
    ],
    "state-before-request": [
        (
            "        req = _validate_request(request, self.CLOCK_LIMIT)\n"
            "        _validate_state(state)\n",
            "        _validate_state(state)\n"
            "        req = _validate_request(request, self.CLOCK_LIMIT)\n",
        )
    ],
    "receipt-field-order": [
        (
            '            "op": req["op"],\n'
            '            "job_id": None if job is None else job["job_id"],',
            '            "job_id": None if job is None else job["job_id"],\n'
            '            "op": req["op"],',
        )
    ],
}
# Edits that must NOT change behavior; each is asserted green.
# status-unchecked: on a validated state every job that is not leased has
# lease_owner None, which never equals a grammar-valid worker, so the owner
# check that follows rejects it as lease_conflict all the same, and nothing
# between the two checks reads anything else or fails with another class.
EQUIVALENT_EDITS = {
    "status-unchecked": [
        ('            job["status"] != "leased"\n            or ', "            ")
    ],
}
MUTANTS = {
    "wrong-code": WrongCode,
    "subclass-error": SubclassError,
    "chained-error": ChainedError,
    "cached-receipt": CachedReceipt,
    "mutates-request": MutatesRequest,
    "new-state-object": NewStateObject,
    **{name: _source_mutant(name, edits) for name, edits in _SM.items()},
}


def test_identity_source_mutant_is_green():
    assert _probe(_source_mutant("identity", [])) == []


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name):
    assert _probe(_source_mutant(name, EQUIVALENT_EDITS[name])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(MUTANTS[name]) != [], name


def _receipt(job, state):
    return {
        "op": "claim",
        "job_id": None if job is None else job["job_id"],
        "status": None if job is None else job["status"],
        "attempts": None if job is None else job["attempts"],
        "lease_expires_at": None if job is None else job["lease_expires_at"],
        "state_id": _state_id(state),
    }


def test_dead_marking_of_an_expired_leased_job_is_pinned():
    """Explicit expectations (not just reference parity): the expired
    leased job at MAX_ATTEMPTS is dead with no lease; the next job in
    (priority, seq) order is leased; alone, the claim returns no job."""
    for name, claimed in (
        ("claim-dead-marks-expired-leased", _job_id("a")),
        ("claim-dead-marks-only-expired-leased", None),
    ):
        build, _ = PROBES[name]
        state, request = build()
        receipt = _reference.QueueEngine().apply(state, request)
        jobs = {j["job_id"]: j for j in state["jobs"]}
        dead = jobs[LEASED]
        assert (dead["status"], dead["attempts"]) == ("dead", MAX_ATTEMPTS)
        assert dead["lease_owner"] is None and dead["lease_expires_at"] is None
        if claimed is None:
            assert list(jobs) == [LEASED]
            assert receipt == _receipt(None, state)
        else:
            job = jobs[claimed]
            assert (job["status"], job["attempts"], job["lease_owner"]) == ("leased", 1, "w2")
            assert job["lease_expires_at"] == 600
            assert receipt == _receipt(job, state)


def test_claim_scan_continues_past_a_dead_marked_job():
    build, _ = PROBES["claim-continues-past-dead-marked"]
    state, request = build()
    receipt = _reference.QueueEngine().apply(state, request)
    jobs = {j["job_id"]: j for j in state["jobs"]}
    first, second = jobs[_job_id("a")], jobs[_job_id("e")]
    assert (first["status"], first["lease_owner"], first["lease_expires_at"]) == (
        "dead",
        None,
        None,
    )
    assert (second["status"], second["attempts"], second["lease_owner"]) == ("leased", 1, "w2")
    assert second["lease_expires_at"] == 110
    assert receipt == _receipt(second, state)


def test_request_errors_take_precedence_over_state_corruption():
    """Each row alone: the corrupt state is corrupt_queue under a valid
    request, and the malformed request wins when both are bad."""
    for label, (edit, _request) in _both_bad_rows().items():
        _totality_ok(_reference.QueueEngine, f"precedence-{label}")
        state = _base()
        edit(state)
        _rejects(_reference.QueueEngine(), state, copy.deepcopy(R_CLAIM), CQ)
