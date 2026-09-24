"""T0356: privacy sensitive-flag contract behavior battery.

Reference engine derived from data/contracts/sensitive_flag.yaml. A
collection's flag gates cloud-model egress: cloud is allowed only when
the flag is exactly false; an unknown or unset flag reads sensitive
(cloud off). Raising needs no approval; lowering needs a user approval
bound to the collection and expected revision. Rollback restores the
prior value under the same rules. Every failure is typed and leaves
state and inputs unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.sensitive_flag_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_CLASSES,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

DOC = yaml.safe_load(CONTRACT.read_text())
_CC = DOC["contract"]
_FIELDS = _CC["record"]["fields"]
_COL_RE = re.compile(_CC["identifiers"]["collection_id"]["grammar"])
_APV_RE = re.compile(_CC["identifiers"]["approval"]["grammar"])
_DESTINATIONS = ("local", "cloud")
_VERDICT_KEYS = {"collection_id", "destination"}
_TRANSITION_KEYS = {"collection_id", "sensitive", "expected_revision", "approval"}
_ROLLBACK_KEYS = {"collection_id", "expected_revision", "approval"}


class FlagError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(cls):
    raise FlagError(cls)


def approval_for(collection_id, expected_revision):
    """The pinned approval binding: collection, revision, target false."""
    raw = f"{collection_id}|{expected_revision}|false".encode()
    return "apv1:" + hashlib.sha256(raw).hexdigest()


def _exact_str(v):
    return type(v) is str


def _exact_rev(v):
    return type(v) is int and v >= 0


def _request(req, keys):
    if type(req) is not dict:
        _fail("malformed_flag_request")
    for k in list(req.keys()):
        if type(k) is not str:
            _fail("malformed_flag_request")
    if set(req) != keys:
        _fail("malformed_flag_request")
    cid = req["collection_id"]
    if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:
        _fail("malformed_flag_request")
    return cid


def _approval_ok(req, cid):
    apv = req["approval"]
    if apv is None:
        return False
    if not _exact_str(apv) or _APV_RE.fullmatch(apv) is None:
        _fail("malformed_flag_request")
    return apv == approval_for(cid, req["expected_revision"])


class FlagStore:
    def __init__(self):
        self._state = {}

    def register(self, collection_id):
        if not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None:
            _fail("malformed_flag_request")
        self._state.setdefault(collection_id, (True, 0, []))
        return self.record(collection_id)

    def record(self, cid):
        sensitive, rev, hist = self._state[cid]
        prior = hist[-1] if hist else None
        return dict(zip(_FIELDS, (cid, sensitive, rev, prior), strict=True))

    def reads_sensitive(self, cid):
        state = self._state.get(cid)
        return state is None or state[0] is not False

    def verdict(self, req):
        cid = _request(req, _VERDICT_KEYS)
        dest = req["destination"]
        if not _exact_str(dest) or dest not in _DESTINATIONS:
            _fail("malformed_flag_request")
        if dest == "cloud" and self.reads_sensitive(cid):
            _fail("egress_blocked")
        return "allow"

    def _apply(self, cid, req, target):
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_flag_request")
        approved = _approval_ok(req, cid)
        if cid not in self._state:
            _fail("unknown_collection")
        sensitive, rev, hist = self._state[cid]
        if req["expected_revision"] != rev:
            _fail("stale_revision")
        if target == sensitive:
            return self.record(cid)
        if target is False and not approved:
            _fail("approval_missing")
        self._state[cid] = (target, rev + 1, hist + [sensitive])
        return self.record(cid)

    def transition(self, req):
        cid = _request(req, _TRANSITION_KEYS)
        if type(req["sensitive"]) is not bool:
            _fail("malformed_flag_request")
        return self._apply(cid, req, req["sensitive"])

    def rollback(self, req):
        cid = _request(req, _ROLLBACK_KEYS)
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_flag_request")
        _approval_ok(req, cid)
        if cid not in self._state:
            _fail("unknown_collection")
        hist = self._state[cid][2]
        target = hist[-1] if hist else True
        return self._apply(cid, req, target)


A = "col1:" + "a" * 64
B = "col1:" + "b" * 64


def _store():
    s = FlagStore()
    s.register(A)
    return s


def _lower(s, cid=A, rev=0):
    return s.transition(
        {
            "collection_id": cid,
            "sensitive": False,
            "expected_revision": rev,
            "approval": approval_for(cid, rev),
        }
    )


def _raises(cls, fn, *args):
    with pytest.raises(FlagError) as ei:
        fn(*args)
    assert ei.value.failure_class == cls
    assert ei.value.code == FAILURE_MAPPING[cls]
    assert ei.value.__cause__ is None
    return ei.value


# ---- contract / lint ------------------------------------------------------


def test_contract_lints_clean():
    lint()


def test_error_model_is_closed_and_consistent():
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASSES)
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert _CC["errors"]["shape"]["retryable_true_only_for"] == ["internal"]


LINT_MUTATIONS = {
    "default_flipped": lambda c: c["semantics"].__setitem__(
        "default", "unknown-or-unset-flag-reads-sensitive-false-cloud-on"
    ),
    "lower_without_approval": lambda c: c["semantics"].__setitem__(
        "lower", "true-to-false-applies-immediately"
    ),
    "egress_truthy": lambda c: c["semantics"].__setitem__(
        "egress", "cloud-destination-allowed-when-flag-is-falsy"
    ),
    "cached_verdict": lambda c: c["semantics"].__setitem__(
        "evaluation", "egress-verdict-cached-per-session"
    ),
    "extra_section": lambda c: c.__setitem__("notes", {"x": 1}),
    "error_added": lambda c: c["errors"]["closed_enum"].append("denied"),
    "mapping_swapped": lambda c: c["failures"]["mapping"].__setitem__(
        "egress_blocked", "malformed_request"
    ),
    "open_failures": lambda c: c["failures"].__setitem__("closed", False),
    "retryable_widened": lambda c: c["errors"]["shape"].__setitem__(
        "retryable_true_only_for", ["internal", "conflict"]
    ),
    "approval_source_widened": lambda c: c["identifiers"]["approval"].__setitem__(
        "source", "any-caller"
    ),
    "record_field_dropped": lambda c: c["record"]["fields"].remove("prior"),
}


@pytest.mark.parametrize("name", sorted(LINT_MUTATIONS))
def test_lint_rejects_mutation(name, tmp_path):
    doc = copy.deepcopy(DOC)
    LINT_MUTATIONS[name](doc["contract"])
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(p)


# ---- happy / default ------------------------------------------------------


def test_register_defaults_sensitive():
    s = FlagStore()
    assert s.register(A) == {
        "collection_id": A,
        "sensitive": True,
        "revision": 0,
        "prior": None,
    }


@pytest.mark.parametrize("cid", [A, B])
def test_default_blocks_cloud_allows_local(cid):
    s = _store()
    assert s.verdict({"collection_id": cid, "destination": "local"}) == "allow"
    _raises("egress_blocked", s.verdict, {"collection_id": cid, "destination": "cloud"})


def test_lower_with_bound_approval_opens_cloud():
    s = _store()
    assert _lower(s) == {"collection_id": A, "sensitive": False, "revision": 1, "prior": True}
    assert s.verdict({"collection_id": A, "destination": "cloud"}) == "allow"
    # scope: another collection stays closed
    _raises("egress_blocked", s.verdict, {"collection_id": B, "destination": "cloud"})


def test_raise_needs_no_approval_and_applies_before_next_verdict():
    s = _store()
    _lower(s)
    rec = s.transition(
        {"collection_id": A, "sensitive": True, "expected_revision": 1, "approval": None}
    )
    assert rec == {"collection_id": A, "sensitive": True, "revision": 2, "prior": False}
    _raises("egress_blocked", s.verdict, {"collection_id": A, "destination": "cloud"})


@pytest.mark.parametrize("value", [True, False])
def test_same_value_is_noop(value):
    s = _store()
    if value is False:
        _lower(s)
    rev = s.record(A)["revision"]
    before = s.record(A)
    rec = s.transition(
        {"collection_id": A, "sensitive": value, "expected_revision": rev, "approval": None}
    )
    assert rec == before


# ---- approval / concurrency boundaries -------------------------------------


@pytest.mark.parametrize(
    "approval",
    [
        None,
        approval_for(B, 0),  # wrong collection
        approval_for(A, 1),  # wrong revision
        "apv1:" + "0" * 64,
    ],
)
def test_lower_without_bound_approval_is_refused(approval):
    s = _store()
    before = s.record(A)
    _raises(
        "approval_missing",
        s.transition,
        {"collection_id": A, "sensitive": False, "expected_revision": 0, "approval": approval},
    )
    assert s.record(A) == before


@pytest.mark.parametrize("rev", [1, 5])
def test_stale_revision_is_conflict(rev):
    s = _store()
    _raises(
        "stale_revision",
        s.transition,
        {
            "collection_id": A,
            "sensitive": False,
            "expected_revision": rev,
            "approval": approval_for(A, rev),
        },
    )
    assert s.record(A)["revision"] == 0


def test_stale_revision_edges_after_transitions():
    s = _store()
    _lower(s)
    for rev in (0, 2):
        _raises(
            "stale_revision",
            s.transition,
            {"collection_id": A, "sensitive": True, "expected_revision": rev, "approval": None},
        )
    assert s.record(A)["revision"] == 1


def test_unknown_collection_on_transition_and_rollback():
    s = _store()
    _raises("unknown_collection", _lower, s, B, 0)
    _raises(
        "unknown_collection",
        s.rollback,
        {"collection_id": B, "expected_revision": 0, "approval": None},
    )


# ---- rollback -------------------------------------------------------------


def test_rollback_of_lower_restores_sensitive_without_approval():
    s = _store()
    _lower(s)
    rec = s.rollback({"collection_id": A, "expected_revision": 1, "approval": None})
    assert rec == {"collection_id": A, "sensitive": True, "revision": 2, "prior": False}
    _raises("egress_blocked", s.verdict, {"collection_id": A, "destination": "cloud"})


def test_rollback_of_raise_is_a_lower_and_needs_approval():
    s = _store()
    _lower(s)
    s.transition({"collection_id": A, "sensitive": True, "expected_revision": 1, "approval": None})
    _raises(
        "approval_missing",
        s.rollback,
        {"collection_id": A, "expected_revision": 2, "approval": None},
    )
    rec = s.rollback({"collection_id": A, "expected_revision": 2, "approval": approval_for(A, 2)})
    assert rec["sensitive"] is False and rec["revision"] == 3


def test_rollback_at_revision_zero_restores_default_noop():
    s = _store()
    before = s.record(A)
    assert s.rollback({"collection_id": A, "expected_revision": 0, "approval": None}) == before


def test_rollback_stale_revision():
    s = _store()
    _lower(s)
    _raises(
        "stale_revision", s.rollback, {"collection_id": A, "expected_revision": 0, "approval": None}
    )


# ---- hostile rows ---------------------------------------------------------

CALLS = []


class StrSub(str):
    pass


class DictSub(dict):
    pass


class ListSub(list):
    pass


class EqRaises(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("eq")

    __hash__ = str.__hash__


class HashCollide(str):
    def __hash__(self):
        CALLS.append("hash")
        return hash("collection_id")

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)


def _base(op):
    if op == "verdict":
        return {"collection_id": A, "destination": "cloud"}
    if op == "transition":
        return {
            "collection_id": A,
            "sensitive": False,
            "expected_revision": 0,
            "approval": approval_for(A, 0),
        }
    return {"collection_id": A, "expected_revision": 0, "approval": None}


def _hostile_rows():
    rows = []
    for op in ("verdict", "transition", "rollback"):
        base = _base(op)
        rows.append((op, "dict_sub", DictSub(base)))
        rows.append((op, "list", list(base.items())))
        rows.append((op, "str", "collection_id"))
        for key in base:
            r = dict(base)
            r[key] = StrSub(r[key]) if type(r[key]) is str else ListSub([r[key]])
            rows.append((op, f"{key}_sub", r))
            r = dict(base)
            r[key] = DictSub({"v": r[key]})
            rows.append((op, f"{key}_dictsub", r))
        for form, cls in (("plain", str), ("eq_raises", EqRaises), ("hash_collide", HashCollide)):
            r = dict(base)
            r[cls("extra")] = 1
            rows.append((op, f"extra_key_{form}", r))
        for key in base:
            r = dict(base)
            del r[key]
            rows.append((op, f"missing_{key}", r))
        r = dict(base)
        r[b"collection_id"] = r.pop("collection_id")
        rows.append((op, "bytes_key", r))
    t = _base("transition")
    for bad in (1, 0, None, "false"):
        rows.append(("transition", f"sensitive_{bad!r}", dict(t, sensitive=bad)))
    for op in ("transition", "rollback"):
        for bad in (True, False, -1, 1.0, "0"):
            rows.append((op, f"rev_{bad!r}", dict(_base(op), expected_revision=bad)))
        rows.append((op, "approval_upper", dict(_base(op), approval="APV1:" + "a" * 64)))
    for cid in (
        "col1:" + "A" * 64,
        "col1:" + "a" * 63,
        "col1:" + "a" * 65,
        "col1:" + "a" * 64 + "\n",
    ):
        rows.append(
            (
                "verdict",
                f"cid_{len(cid)}_{cid[-1]!r}",
                {"collection_id": cid, "destination": "local"},
            )
        )
    for dest in ("Cloud", "remote", "", "cloud "):
        rows.append(("verdict", f"dest_{dest!r}", {"collection_id": A, "destination": dest}))
    return rows


ROWS = _hostile_rows()


def _snap(req):
    if isinstance(req, dict):
        return [(k, copy.deepcopy(v)) for k, v in req.items()]
    return copy.deepcopy(req)


def _unchanged(req, snap):
    if isinstance(req, dict):
        now = list(req.items())
        return len(now) == len(snap) and all(
            k1 is k2 and v1 == v2 for (k1, v1), (k2, v2) in zip(now, snap, strict=True)
        )
    return req == snap


@pytest.mark.parametrize("op,name,req", ROWS, ids=[f"{o}-{n}" for o, n, _ in ROWS])
def test_hostile_request_fails_closed_typed(op, name, req):
    s = _store()
    before_state = s.record(A)
    snap = _snap(req)
    CALLS.clear()
    _raises("malformed_flag_request", getattr(s, op), req)
    assert CALLS == []
    assert s.record(A) == before_state
    assert _unchanged(req, snap)


def test_register_hostile_ids():
    s = FlagStore()
    for bad in (StrSub(A), A.upper(), None, [A]):
        _raises("malformed_flag_request", s.register, bad)
    assert s._state == {}
