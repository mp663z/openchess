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
import os
import re
import subprocess
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
_MAX_REV = 2**63 - 1
assert str(_MAX_REV) in _CC["identifiers"]["revision"]["grammar"]
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
    return type(v) is int and 0 <= v <= _MAX_REV


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


def _bump(rev):
    """Fail-closed saturation: never saturate or wrap past the grammar."""
    if rev >= _MAX_REV:
        _fail("revision_exhausted")
    return rev + 1


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
        self._state[cid] = (target, _bump(rev), hist + [sensitive])
        return self.record(cid)

    def transition(self, req):
        cid = _request(req, _TRANSITION_KEYS)
        if type(req["sensitive"]) is not bool:
            _fail("malformed_flag_request")
        return self._apply(cid, req, req["sensitive"])

    def rollback(self, req):
        """Walk-back: pop ONE history entry and restore it; the rollback
        itself is never pushed; empty history fails typed."""
        cid = _request(req, _ROLLBACK_KEYS)
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_flag_request")
        approved = _approval_ok(req, cid)
        if cid not in self._state:
            _fail("unknown_collection")
        sensitive, rev, hist = self._state[cid]
        if req["expected_revision"] != rev:
            _fail("stale_revision")
        if not hist:
            _fail("empty_history")
        target = hist[-1]
        if target is False and not approved:
            _fail("approval_missing")
        self._state[cid] = (target, _bump(rev), hist[:-1])
        return self.record(cid)


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
    "record_exact_int": lambda c: c["record"].__setitem__("exact", 1),
    "role_not_scope_changed": lambda c: c["role"].__setitem__("not_scope", "none"),
    "failure_class_dropped": lambda c: c["failures"]["classes"].remove("empty_history"),
    "failure_class_renamed": lambda c: c["failures"]["classes"].__setitem__(0, "bad_request"),
    "trigger_reworded": lambda c: c["failures"]["triggers"].__setitem__(
        "egress_blocked", "cloud-destination-requested"
    ),
    "failures_extra_key": lambda c: c["failures"].__setitem__("notes", "x"),
    "properties_total_changed": lambda c: c["properties"].__setitem__("total", "best-effort"),
    "base_path_changed": lambda c: c["versioning"].__setitem__(
        "base_path", "/privacy/sensitive-flag/v2"
    ),
    "link_changed": lambda c: c["links"].__setitem__(
        "control_plane_contract", "data/contracts/control_plane.yaml"
    ),
    "rollback_toggle": lambda c: c["semantics"].__setitem__(
        "rollback", "restores-the-prior-value-and-pushes-the-current-one"
    ),
    "saturation_wraps": lambda c: c["semantics"].__setitem__(
        "saturation", "revision-wraps-to-zero"
    ),
    "revision_unbounded": lambda c: c["identifiers"]["revision"].__setitem__(
        "grammar", "exact-built-in-int-zero-or-greater-never-bool"
    ),
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
    assert rec == {"collection_id": A, "sensitive": True, "revision": 2, "prior": None}
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


def _raise(s, rev, cid=A):
    return s.transition(
        {"collection_id": cid, "sensitive": True, "expected_revision": rev, "approval": None}
    )


def _rb(s, rev, approval=None, cid=A):
    return s.rollback({"collection_id": cid, "expected_revision": rev, "approval": approval})


def test_rollback_on_empty_history_fails_typed_state_unchanged():
    s = _store()
    before = s.record(A)
    _raises("empty_history", _rb, s, 0)
    assert s.record(A) == before
    _raises("empty_history", _rb, s, 0, approval_for(A, 0))
    assert s.record(A) == before


def test_two_rollbacks_walk_back_two_steps_then_history_is_empty():
    s = _store()
    _lower(s)  # rev1 false, hist [True]
    _raise(s, 1)  # rev2 true, hist [True, False]
    rec = _rb(s, 2, approval_for(A, 2))  # pops False
    assert rec == {"collection_id": A, "sensitive": False, "revision": 3, "prior": True}
    rec = _rb(s, 3)  # pops True, NOT the False just undone
    assert rec == {"collection_id": A, "sensitive": True, "revision": 4, "prior": None}
    _raises("egress_blocked", s.verdict, {"collection_id": A, "destination": "cloud"})
    before = s.record(A)
    _raises("empty_history", _rb, s, 4, approval_for(A, 4))
    assert s.record(A) == before


def test_second_rollback_never_reapplies_the_undone_lowering():
    s = _store()
    _lower(s)
    _rb(s, 1)  # back to sensitive, history empty
    _raises("empty_history", _rb, s, 2, approval_for(A, 2))
    assert s.record(A)["sensitive"] is True


def test_set_after_rollback_continues_from_the_restored_value():
    s = _store()
    _lower(s)
    _raise(s, 1)
    _rb(s, 2, approval_for(A, 2))  # rev3 false, hist [True]
    rec = _raise(s, 3)
    assert rec == {"collection_id": A, "sensitive": True, "revision": 4, "prior": False}
    rec = _rb(s, 4, approval_for(A, 4))
    assert rec == {"collection_id": A, "sensitive": False, "revision": 5, "prior": True}
    assert _rb(s, 5)["sensitive"] is True


@pytest.mark.parametrize("op", ["transition", "rollback"])
def test_change_at_max_revision_fails_typed_state_unchanged(op):
    s = _store()
    s._state[A] = (True, _MAX_REV, [False])  # a collection at the last revision
    before = s.record(A)
    req = dict(_base(op), expected_revision=_MAX_REV, approval=approval_for(A, _MAX_REV))
    _raises("revision_exhausted", getattr(s, op), req)
    assert s.record(A) == before and s._state[A] == (True, _MAX_REV, [False])


@pytest.mark.parametrize("op", ["transition", "rollback"])
@pytest.mark.parametrize("rev", [2**63 - 1, 1])
def test_revision_upper_edge_is_well_formed_and_stale(op, rev):
    # the grammar's inclusive upper edge is ACCEPTED as well-formed: on a
    # revision-0 collection it fails the compare-and-set, never as malformed
    s = _store()
    before = s.record(A)
    req = dict(_base(op), expected_revision=rev, approval=approval_for(A, rev))
    _raises("stale_revision", getattr(s, op), req)
    assert s.record(A) == before


def test_reregister_never_resets_state_or_revives_old_approvals():
    s = _store()
    _lower(s)
    _raise(s, 1)
    before = s.record(A)
    assert (
        s.register(A)
        == before
        == {
            "collection_id": A,
            "sensitive": True,
            "revision": 2,
            "prior": False,
        }
    )
    _raises("stale_revision", _lower, s, A, 0)
    assert s.record(A) == before


@pytest.mark.parametrize("op", ["transition", "rollback"])
@pytest.mark.parametrize("which", ["upper", "str_sub", "newline"])
def test_malformed_approval_precedes_unknown_and_stale(op, which):
    s = _store()
    for cid, rev in ((B, 0), (A, 7)):  # unknown collection; stale revision
        good = approval_for(cid, rev)
        bad = {"upper": good.upper(), "str_sub": StrSub(good), "newline": good + "\n"}[which]
        req = dict(_base(op), collection_id=cid, expected_revision=rev, approval=bad)
        before = s.record(A)
        _raises("malformed_flag_request", getattr(s, op), req)
        assert s.record(A) == before and B not in s._state


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
        good = approval_for(A, 0)
        for tag, apv in (
            ("upper", "APV1:" + "a" * 64),
            ("newline", good + "\n"),
            ("hex63", good[:-1]),
            ("hex65", good + "a"),
        ):
            rows.append((op, f"approval_{tag}", dict(_base(op), approval=apv)))
        for tag, rev in (("huge", 10**5000), ("2**63", 2**63)):
            bound = dict(_base(op), expected_revision=rev, approval="apv1:" + "0" * 64)
            rows.append((op, f"rev_{tag}", bound))
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


# ---- executable kill check: one-edit mutants of the reference engine ------
# Each edit is applied to THIS file's source; the mutated copy's battery
# (minus this section) must go red. The identity edit must stay green.

MUTANTS = {
    "approval_unbound": (
        'return apv == approval_for(cid, req["expected_revision"])',
        "return True",
    ),
    "apv_match": (
        "_APV_RE.fullmatch(apv) is None",
        "_APV_RE.match(apv) is None",
    ),
    "bool_as_int": (
        'if type(req["sensitive"]) is not bool:',
        'if req["sensitive"] not in (0, 1):',
    ),
    "cid_search": (
        "_COL_RE.fullmatch(cid) is None",
        "_COL_RE.search(cid) is None",
    ),
    "default_open": (
        "return state is None or state[0] is not False",
        "return state is not None and state[0] is not False",
    ),
    "dest_any": (
        "if not _exact_str(dest) or dest not in _DESTINATIONS:",
        "if not _exact_str(dest):",
    ),
    "dict_sub_ok": (
        "if type(req) is not dict:",
        "if not isinstance(req, dict):",
    ),
    "bump_unchecked": (
        "    if rev >= _MAX_REV:\n",
        "    if False:\n",
    ),
    "max_rev_lt": (
        "return type(v) is int and 0 <= v <= _MAX_REV",
        "return type(v) is int and 0 <= v < _MAX_REV",
    ),
    "no_approval_check": (
        (
            "        if target is False and not approved:\n"
            '            _fail("approval_missing")\n'
            "        self._state[cid] = (target, _bump(rev), hist + [sensitive])"
        ),
        "        self._state[cid] = (target, _bump(rev), hist + [sensitive])",
    ),
    "no_cas": (
        (
            '        if req["expected_revision"] != rev:\n'
            '            _fail("stale_revision")\n'
            "        if target == sensitive:"
        ),
        "        if target == sensitive:",
    ),
    "noop_bumps": (
        ("        if target == sensitive:\n            return self.record(cid)\n"),
        "",
    ),
    "reregister_resets": (
        "self._state.setdefault(collection_id, (True, 0, []))",
        "self._state[collection_id] = (True, 0, [])",
    ),
    "rev_bool_ok": (
        "return type(v) is int and 0 <= v",
        "return isinstance(v, int) and 0 <= v",
    ),
    "rev_unbounded": (
        "return type(v) is int and 0 <= v <= _MAX_REV",
        "return type(v) is int and 0 <= v",
    ),
    "rollback_empty_default_true": (
        ('        if not hist:\n            _fail("empty_history")\n        target = hist[-1]'),
        "        target = hist[-1] if hist else True",
    ),
    "rollback_no_approval": (
        (
            "        if target is False and not approved:\n"
            '            _fail("approval_missing")\n'
            "        self._state[cid] = (target, _bump(rev), hist[:-1])"
        ),
        "        self._state[cid] = (target, _bump(rev), hist[:-1])",
    ),
    "rollback_toggle": (
        "self._state[cid] = (target, _bump(rev), hist[:-1])",
        "self._state[cid] = (target, _bump(rev), hist[:-1] + [sensitive])",
    ),
    "rollback_unknown_first": (
        (
            "        approved = _approval_ok(req, cid)\n"
            "        if cid not in self._state:\n"
            '            _fail("unknown_collection")\n'
            "        sensitive, rev, hist = self._state[cid]\n"
            '        if req["expected_revision"] != rev:\n'
            '            _fail("stale_revision")\n'
            "        if not hist:"
        ),
        (
            "        if cid not in self._state:\n"
            '            _fail("unknown_collection")\n'
            "        approved = _approval_ok(req, cid)\n"
            "        sensitive, rev, hist = self._state[cid]\n"
            '        if req["expected_revision"] != rev:\n'
            '            _fail("stale_revision")\n'
            "        if not hist:"
        ),
    ),
    "str_sub_ok": (
        ("def _exact_str(v):\n    return type(v) is str"),
        ("def _exact_str(v):\n    return isinstance(v, str)"),
    ),
    "transition_unknown_first": (
        (
            "        approved = _approval_ok(req, cid)\n"
            "        if cid not in self._state:\n"
            '            _fail("unknown_collection")\n'
            "        sensitive, rev, hist = self._state[cid]\n"
            '        if req["expected_revision"] != rev:\n'
            '            _fail("stale_revision")\n'
            "        if target == sensitive:"
        ),
        (
            "        if cid not in self._state:\n"
            '            _fail("unknown_collection")\n'
            "        approved = _approval_ok(req, cid)\n"
            "        sensitive, rev, hist = self._state[cid]\n"
            '        if req["expected_revision"] != rev:\n'
            '            _fail("stale_revision")\n'
            "        if target == sensitive:"
        ),
    ),
}
EQUIVALENT = {
    # stored state is always an exact bool (transitions reject non-bool)
    "truthy_egress": (
        "state[0] is not False",
        "bool(state[0])",
    ),
}
_SELF = Path(__file__)


# the battery without this section: every test above, parametrized rows
# expanded; a green copy must run exactly this many and pass them all
EXPECTED_BATTERY = 143


def _battery(tmp_dir, source):
    """Run the copy (kill-check section stripped, so no selection flags are
    needed) from a neutrally named dir; return (returncode, passed, failed)."""
    copy_ = tmp_dir / "test_battery_copy.py"
    copy_.write_text(source)
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
            str(copy_),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    passed = re.search(r"(\d+) passed", tail)
    failed = re.search(r"(\d+) failed", tail)
    return (
        r.returncode,
        int(passed.group(1)) if passed else 0,
        int(failed.group(1)) if failed else 0,
    )


def _green(tmp_dir, source):
    rc, passed, failed = _battery(tmp_dir, source)
    return rc == 0 and passed == EXPECTED_BATTERY and failed == 0


def _red(tmp_dir, source):
    # tests ran and at least one FAILED; rc 2/4/5 (errors, usage, nothing
    # collected) is never a kill
    rc, passed, failed = _battery(tmp_dir, source)
    return rc == 1 and failed >= 1 and passed + failed == EXPECTED_BATTERY


def _edited(before, after):
    source = _SELF.read_text()
    head, _, _ = source.partition("# ---- executable kill check")
    assert head.count(before) == 1, before
    return head.replace(before, after)


def test_identity_mutant_is_green(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("battery")
    assert _green(tmp, _edited("_MAX_REV = 2**63 - 1", "_MAX_REV = 2**63 - 1"))


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_source_mutant_is_killed(name, tmp_path_factory):
    assert _red(tmp_path_factory.mktemp("battery"), _edited(*MUTANTS[name]))


@pytest.mark.parametrize("name", sorted(EQUIVALENT))
def test_equivalent_edit_stays_green(name, tmp_path_factory):
    assert _green(tmp_path_factory.mktemp("battery"), _edited(*EQUIVALENT[name]))
