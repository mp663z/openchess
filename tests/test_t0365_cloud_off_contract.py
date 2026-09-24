"""T0365: privacy cloud-off contract behavior battery.

Reference engine derived from data/contracts/cloud_off.yaml. One
install-wide cloud mode gates every cloud-model egress: cloud is allowed
only when the mode is exactly "on" AND the collection's sensitive flag
(the T0356 contract, composed, never re-owned) reads exactly false. An
unknown install reads "off"; local is always allowed. Turning cloud off
needs no approval; turning it on needs a user opt-in bound to the
install and expected revision. Every failure is typed and leaves switch
state, flag state and inputs unchanged.
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

from tests.test_t0356_sensitive_flag_contract import (  # noqa: E402
    FlagStore,
    approval_for,
)
from tools.cloud_off_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_CLASSES,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

REPO = CONTRACT.parents[2]  # data/contracts/<name>.yaml; valid for battery copies too
DOC = yaml.safe_load(CONTRACT.read_text())
_CC = DOC["contract"]
_FIELDS = _CC["record"]["fields"]
_INS_RE = re.compile(_CC["identifiers"]["install_id"]["grammar"])
_OPT_RE = re.compile(_CC["identifiers"]["opt_in"]["grammar"])
_COL_RE = re.compile(
    yaml.safe_load((REPO / _CC["links"]["sensitive_flag_contract"]).read_text())["contract"][
        "identifiers"
    ]["collection_id"]["grammar"]
)
_MAX_REV = 2**63 - 1
assert str(_MAX_REV) in _CC["identifiers"]["revision"]["grammar"]
_MODES = ("off", "on")
_DESTINATIONS = ("local", "cloud")
_VERDICT_KEYS = {"install_id", "collection_id", "destination"}
_TRANSITION_KEYS = {"install_id", "cloud_mode", "expected_revision", "opt_in"}


class CloudError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(cls):
    raise CloudError(cls)


def opt_in_for(install_id, expected_revision, target="on"):
    """The pinned opt-in binding: install, revision, target on."""
    raw = f"{install_id}|{expected_revision}|{target}".encode()
    return "opt1:" + hashlib.sha256(raw).hexdigest()


def _exact_str(v):
    return type(v) is str


def _exact_rev(v):
    return type(v) is int and 0 <= v <= _MAX_REV


def _request(req, keys):
    if type(req) is not dict:
        _fail("malformed_cloud_request")
    for k in list(req.keys()):
        if type(k) is not str:
            _fail("malformed_cloud_request")
    if set(req) != keys:
        _fail("malformed_cloud_request")
    iid = req["install_id"]
    if not _exact_str(iid) or _INS_RE.fullmatch(iid) is None:
        _fail("malformed_cloud_request")
    return iid


def _opt_in_ok(req, iid):
    token = req["opt_in"]
    if token is None:
        return False
    if not _exact_str(token) or _OPT_RE.fullmatch(token) is None:
        _fail("malformed_cloud_request")
    return token == opt_in_for(iid, req["expected_revision"])


def _bump(rev):
    """Fail-closed saturation: never saturate or wrap past the grammar."""
    if rev >= _MAX_REV:
        _fail("revision_exhausted")
    return rev + 1


class CloudSwitch:
    def __init__(self, flags):
        self._flags = flags
        self._state = {}

    def register(self, install_id):
        if not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None:
            _fail("malformed_cloud_request")
        self._state.setdefault(install_id, ("off", 0))
        return self.record(install_id)

    def record(self, iid):
        mode, rev = self._state[iid]
        return dict(zip(_FIELDS, (iid, mode, rev), strict=True))

    def reads_off(self, iid):
        state = self._state.get(iid)
        return state is None or state[0] != "on"

    def verdict(self, req):
        iid = _request(req, _VERDICT_KEYS)
        cid = req["collection_id"]
        if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:
            _fail("malformed_cloud_request")
        dest = req["destination"]
        if not _exact_str(dest) or dest not in _DESTINATIONS:
            _fail("malformed_cloud_request")
        if dest == "local":
            return "allow"
        if self.reads_off(iid):
            _fail("cloud_mode_off")
        if self._flags.reads_sensitive(cid):
            _fail("collection_sensitive")
        return "allow"

    def transition(self, req):
        iid = _request(req, _TRANSITION_KEYS)
        target = req["cloud_mode"]
        if not _exact_str(target) or target not in _MODES:
            _fail("malformed_cloud_request")
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_cloud_request")
        approved = _opt_in_ok(req, iid)
        if iid not in self._state:
            _fail("unknown_install")
        mode, rev = self._state[iid]
        if req["expected_revision"] != rev:
            _fail("stale_revision")
        if target == mode:
            return self.record(iid)
        if target == "on" and not approved:
            _fail("opt_in_missing")
        self._state[iid] = (target, _bump(rev))
        return self.record(iid)


INS = "ins1:" + "1" * 64
INS2 = "ins1:" + "2" * 64
A = "col1:" + "a" * 64
B = "col1:" + "b" * 64


def _world(open_a=True):
    """Install INS registered (off); collection A registered and, when
    OPEN_A, lowered to false; collection B registered and sensitive."""
    flags = FlagStore()
    flags.register(A)
    flags.register(B)
    if open_a:
        flags.transition(
            {
                "collection_id": A,
                "sensitive": False,
                "expected_revision": 0,
                "approval": approval_for(A, 0),
            }
        )
    s = CloudSwitch(flags)
    s.register(INS)
    return s, flags


_BOUND = object()


def _set(s, mode, rev, iid=INS, opt_in=_BOUND):
    if opt_in is _BOUND:
        opt_in = opt_in_for(iid, rev) if mode == "on" else None
    return s.transition(
        {"install_id": iid, "cloud_mode": mode, "expected_revision": rev, "opt_in": opt_in}
    )


def _ask(s, dest="cloud", cid=A, iid=INS):
    return s.verdict({"install_id": iid, "collection_id": cid, "destination": dest})


def _raises(cls, fn, *args):
    with pytest.raises(CloudError) as ei:
        fn(*args)
    assert ei.value.failure_class == cls
    assert ei.value.code == FAILURE_MAPPING[cls]
    assert ei.value.__cause__ is None
    return ei.value


def _flag_state(flags):
    return {cid: flags.record(cid) for cid in (A, B)}


# ---- contract / lint ------------------------------------------------------


def test_contract_lints_clean():
    lint()


def test_error_model_is_closed_and_consistent():
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASSES)
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert _CC["errors"]["shape"]["retryable_true_only_for"] == ["internal"]
    # both egress refusals surface as the one user-facing cloud_off code
    assert FAILURE_MAPPING["cloud_mode_off"] == FAILURE_MAPPING["collection_sensitive"]


def test_linked_documents_exist_and_agree():
    flag_doc = yaml.safe_load((REPO / _CC["links"]["sensitive_flag_contract"]).read_text())
    assert flag_doc["contract"]["errors"]["closed_enum"].count("cloud_off") == 1
    adr = (REPO / _CC["links"]["architecture_decision"]).read_text()
    front = yaml.safe_load(adr.split("---")[1])
    assert front["external_providers"]["hosted-byom"]["opt_in"] is True
    assert front["external_providers"]["hosted-byom"]["default"] in (False, "off")  # YAML 1.1
    assert "full-loop-offline-on-desktop" in front["invariants"]


LINT_MUTATIONS = {
    "default_on": lambda c: c["semantics"].__setitem__(
        "default", "unknown-or-unset-install-reads-cloud-mode-on"
    ),
    "turn_on_without_opt_in": lambda c: c["semantics"].__setitem__(
        "turn_on", "off-to-on-applies-immediately"
    ),
    "turn_off_needs_approval": lambda c: c["semantics"].__setitem__(
        "turn_off", "on-to-off-needs-an-approval"
    ),
    "egress_ignores_flag": lambda c: c["semantics"].__setitem__(
        "egress", "cloud-destination-allowed-when-cloud-mode-is-on"
    ),
    "precedence_flag_first": lambda c: c["semantics"].__setitem__(
        "precedence", "malformed-then-collection-flag-then-cloud-mode"
    ),
    "composition_reowned": lambda c: c["semantics"].__setitem__(
        "composition", "collection-flag-stored-by-the-switch"
    ),
    "cached_verdict": lambda c: c["semantics"].__setitem__(
        "evaluation", "egress-verdict-cached-per-session"
    ),
    "rollback_history": lambda c: c["semantics"].__setitem__("rollback", "pops-one-history-entry"),
    "reregister_resets": lambda c: c["semantics"].__setitem__(
        "reregister", "registering-resets-to-off"
    ),
    "saturation_wraps": lambda c: c["semantics"].__setitem__(
        "saturation", "revision-wraps-to-zero"
    ),
    "extra_section": lambda c: c.__setitem__("notes", {"x": 1}),
    "error_added": lambda c: c["errors"]["closed_enum"].append("denied"),
    "mapping_swapped": lambda c: c["failures"]["mapping"].__setitem__(
        "cloud_mode_off", "malformed_request"
    ),
    "collection_mapping_swapped": lambda c: c["failures"]["mapping"].__setitem__(
        "collection_sensitive", "unknown_install"
    ),
    "open_failures": lambda c: c["failures"].__setitem__("closed", False),
    "retryable_widened": lambda c: c["errors"]["shape"].__setitem__(
        "retryable_true_only_for", ["internal", "conflict"]
    ),
    "opt_in_source_widened": lambda c: c["identifiers"]["opt_in"].__setitem__(
        "source", "any-caller"
    ),
    "opt_in_binding_loosened": lambda c: c["identifiers"]["opt_in"].__setitem__(
        "binding", "sha256-over-install-id"
    ),
    "mode_grammar_loosened": lambda c: c["identifiers"]["cloud_mode"].__setitem__(
        "grammar", "truthy-value"
    ),
    "record_field_dropped": lambda c: c["record"]["fields"].remove("revision"),
    "record_exact_int": lambda c: c["record"].__setitem__("exact", 1),
    "role_not_scope_changed": lambda c: c["role"].__setitem__("not_scope", "none"),
    "failure_class_dropped": lambda c: c["failures"]["classes"].remove("collection_sensitive"),
    "failure_class_renamed": lambda c: c["failures"]["classes"].__setitem__(0, "bad_request"),
    "trigger_reworded": lambda c: c["failures"]["triggers"].__setitem__(
        "cloud_mode_off", "cloud-destination-requested"
    ),
    "failures_extra_key": lambda c: c["failures"].__setitem__("notes", "x"),
    "properties_kill_switch_changed": lambda c: c["properties"].__setitem__(
        "kill_switch", "best-effort"
    ),
    "base_path_changed": lambda c: c["versioning"].__setitem__(
        "base_path", "/privacy/cloud-off/v2"
    ),
    "link_changed": lambda c: c["links"].__setitem__(
        "sensitive_flag_contract", "data/contracts/sensitive-flag.yaml"
    ),
    "revision_unbounded": lambda c: c["identifiers"]["revision"].__setitem__(
        "grammar", "exact-built-in-int-zero-or-greater-never-bool"
    ),
    "schema_version_bool": lambda c: None,  # envelope mutation, see below
}


@pytest.mark.parametrize("name", sorted(LINT_MUTATIONS))
def test_lint_rejects_mutation(name, tmp_path):
    doc = copy.deepcopy(DOC)
    LINT_MUTATIONS[name](doc["contract"])
    if name == "schema_version_bool":
        doc["schema_version"] = True
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(p)


# ---- happy / default ------------------------------------------------------


def test_register_defaults_off():
    s = CloudSwitch(FlagStore())
    assert s.register(INS) == {"install_id": INS, "cloud_mode": "off", "revision": 0}


@pytest.mark.parametrize("iid", [INS, INS2])
def test_default_blocks_cloud_even_for_an_open_collection(iid):
    s, _ = _world()
    _raises("cloud_mode_off", _ask, s, "cloud", A, iid)


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("cid", [A, B, "col1:" + "c" * 64])
@pytest.mark.parametrize("iid", [INS, INS2])
def test_local_is_always_allowed(mode, cid, iid):
    s, _ = _world()
    if mode == "on":
        _set(s, "on", 0)
    assert _ask(s, "local", cid, iid) == "allow"


def test_turn_on_with_bound_opt_in_opens_cloud_for_an_open_collection():
    s, _ = _world()
    assert _set(s, "on", 0) == {"install_id": INS, "cloud_mode": "on", "revision": 1}
    assert _ask(s) == "allow"


@pytest.mark.parametrize("cid", [B, "col1:" + "c" * 64])
def test_cloud_on_still_blocks_a_sensitive_or_unknown_collection(cid):
    s, _ = _world()
    _set(s, "on", 0)
    _raises("collection_sensitive", _ask, s, "cloud", cid)


@pytest.mark.parametrize("cid", [A, B])
def test_switch_is_checked_before_the_flag(cid):
    s, _ = _world()
    _raises("cloud_mode_off", _ask, s, "cloud", cid)


def test_unknown_install_reads_off_even_when_another_install_is_on():
    s, _ = _world()
    _set(s, "on", 0)
    _raises("cloud_mode_off", _ask, s, "cloud", A, INS2)


def test_turn_off_needs_no_opt_in_and_applies_before_the_next_verdict():
    s, _ = _world()
    _set(s, "on", 0)
    assert _ask(s) == "allow"
    assert _set(s, "off", 1, opt_in=None)["cloud_mode"] == "off"
    _raises("cloud_mode_off", _ask, s)


@pytest.mark.parametrize(
    "token",
    ["junk", "OPT1:" + "a" * 64, "STRSUB", 7, "EQRAISES", "opt1:" + "a" * 63],
    ids=["junk", "upper", "strsub", "int", "eq_raises", "hex63"],
)
def test_malformed_opt_in_on_turn_off_is_malformed(token):
    s, flags = _world()
    _set(s, "on", 0)
    if token == "STRSUB":
        token = StrSub(opt_in_for(INS, 1))
    elif token == "EQRAISES":
        token = EqRaises(opt_in_for(INS, 1))
    before, fb = s.record(INS), _flag_state(flags)
    CALLS.clear()
    _raises("malformed_cloud_request", _set, s, "off", 1, INS, token)
    assert CALLS == [] and s.record(INS) == before and _flag_state(flags) == fb
    assert _ask(s) == "allow"  # still on


def test_well_formed_opt_in_on_turn_off_is_ignored():
    s, _ = _world()
    _set(s, "on", 0)
    assert _set(s, "off", 1, opt_in="opt1:" + "f" * 64)["revision"] == 2
    _raises("cloud_mode_off", _ask, s)


def test_verdict_is_read_at_send_time_never_cached():
    s, flags = _world()
    _set(s, "on", 0)
    assert _ask(s) == "allow"
    flags.transition(
        {"collection_id": A, "sensitive": True, "expected_revision": 1, "approval": None}
    )
    _raises("collection_sensitive", _ask, s)
    flags.transition(
        {
            "collection_id": A,
            "sensitive": False,
            "expected_revision": 2,
            "approval": approval_for(A, 2),
        }
    )
    assert _ask(s) == "allow"


@pytest.mark.parametrize("mode", _MODES)
def test_same_value_is_noop(mode):
    s, _ = _world()
    if mode == "on":
        _set(s, "on", 0)
    before = s.record(INS)
    # same-value turn-on without an opt-in is a no-op, not opt_in_missing
    assert _set(s, mode, before["revision"], opt_in=None) == before


def test_reregister_never_resets_mode_or_revision():
    s, _ = _world()
    _set(s, "on", 0)
    assert s.register(INS) == {"install_id": INS, "cloud_mode": "on", "revision": 1}


# ---- opt-in / conflict / unknown -------------------------------------------


@pytest.mark.parametrize(
    "tag",
    ["none", "other_install", "other_revision", "target_off", "random"],
)
def test_turn_on_without_a_bound_opt_in_is_refused(tag):
    s, flags = _world()
    token = {
        "none": None,
        "other_install": opt_in_for(INS2, 0),
        "other_revision": opt_in_for(INS, 1),
        "target_off": opt_in_for(INS, 0, target="off"),
        "random": "opt1:" + "e" * 64,
    }[tag]
    before, fb = s.record(INS), _flag_state(flags)
    _raises("opt_in_missing", _set, s, "on", 0, INS, token)
    assert s.record(INS) == before and _flag_state(flags) == fb
    _raises("cloud_mode_off", _ask, s)


def test_an_opt_in_is_bound_to_its_install_both_ways():
    s, _ = _world()
    s.register(INS2)
    _raises("opt_in_missing", _set, s, "on", 0, INS2, opt_in_for(INS, 0))
    _raises("opt_in_missing", _set, s, "on", 0, INS, opt_in_for(INS2, 0))
    assert _set(s, "on", 0, INS2)["cloud_mode"] == "on"
    _raises("cloud_mode_off", _ask, s)  # INS is still off


def test_an_old_opt_in_is_never_reusable():
    s, _ = _world()
    old = opt_in_for(INS, 0)
    _set(s, "on", 0, opt_in=old)
    _set(s, "off", 1)
    _raises("opt_in_missing", _set, s, "on", 2, INS, old)
    assert _set(s, "on", 2)["revision"] == 3  # a fresh opt-in works


@pytest.mark.parametrize("rev", [1, 2, _MAX_REV])
def test_stale_revision_is_conflict(rev):
    s, _ = _world()
    before = s.record(INS)
    _raises("stale_revision", _set, s, "on", rev)
    assert s.record(INS) == before


def test_stale_revision_edges_after_transitions():
    s, _ = _world()
    _set(s, "on", 0)
    _set(s, "off", 1)
    for rev in (1, 3):
        _raises("stale_revision", _set, s, "on", rev)
        _raises("stale_revision", _set, s, "off", rev)
    assert _set(s, "on", 2)["revision"] == 3


def test_unknown_install_on_transition():
    s, _ = _world()
    _raises("unknown_install", _set, s, "on", 0, INS2)
    _raises("unknown_install", _set, s, "off", 0, INS2)
    assert set(s._state) == {INS}


@pytest.mark.parametrize("which", ["unknown", "stale"])
def test_malformed_opt_in_precedes_unknown_and_stale(which):
    s, _ = _world()
    iid, rev = (INS2, 0) if which == "unknown" else (INS, 5)
    _raises("malformed_cloud_request", _set, s, "on", rev, iid, "OPT1:" + "a" * 64)


def test_unknown_precedes_stale_and_stale_precedes_opt_in():
    s, _ = _world()
    _raises("unknown_install", _set, s, "on", 7, INS2, None)
    _raises("stale_revision", _set, s, "on", 7, INS, None)


# ---- saturation ---------------------------------------------------------------


def _at(s, rev, mode="off"):
    s._state[INS] = (mode, rev)


@pytest.mark.parametrize("target", _MODES)
def test_change_at_max_revision_fails_typed_state_unchanged(target):
    s, _ = _world()
    _at(s, _MAX_REV, "on" if target == "off" else "off")
    before = s.record(INS)
    _raises("revision_exhausted", _set, s, target, _MAX_REV)
    assert s.record(INS) == before


@pytest.mark.parametrize("target", _MODES)
def test_change_just_below_max_revision_commits_to_max(target):
    s, _ = _world()
    _at(s, _MAX_REV - 1, "on" if target == "off" else "off")
    assert _set(s, target, _MAX_REV - 1)["revision"] == _MAX_REV


def test_unapproved_turn_on_at_max_revision_is_opt_in_missing():
    s, _ = _world()
    _at(s, _MAX_REV)
    _raises("opt_in_missing", _set, s, "on", _MAX_REV, INS, None)


@pytest.mark.parametrize("mode", _MODES)
def test_same_value_at_max_revision_is_a_noop(mode):
    s, _ = _world()
    _at(s, _MAX_REV, mode)
    assert _set(s, mode, _MAX_REV, opt_in=None)["revision"] == _MAX_REV


@pytest.mark.parametrize("rev", [_MAX_REV, 0])
def test_revision_edges_are_well_formed(rev):
    s, _ = _world()
    _at(s, 1)
    _raises("stale_revision", _set, s, "on", rev)


# ---- rollback: rejected transitions are rolled back whole ------------------


def test_undoing_on_is_turn_off_and_undoing_off_needs_a_fresh_opt_in():
    s, _ = _world()
    first = opt_in_for(INS, 0)
    _set(s, "on", 0, opt_in=first)
    _set(s, "off", 1)  # undo "on": no approval
    _raises("opt_in_missing", _set, s, "on", 2, INS, first)  # undo "off": not by replay
    _raises("cloud_mode_off", _ask, s)


@pytest.mark.parametrize(
    "case",
    ["opt_in_missing", "stale", "unknown", "exhausted", "malformed"],
)
def test_rejected_transition_leaves_everything_unchanged(case):
    s, flags = _world()
    _set(s, "on", 0)
    _set(s, "off", 1)
    if case == "exhausted":
        _at(s, _MAX_REV, "on")
    before, fb, states = s.record(INS), _flag_state(flags), dict(s._state)
    req = {
        "opt_in_missing": {"install_id": INS, "cloud_mode": "on", "expected_revision": 2},
        "stale": {"install_id": INS, "cloud_mode": "on", "expected_revision": 1},
        "unknown": {"install_id": INS2, "cloud_mode": "on", "expected_revision": 0},
        "exhausted": {"install_id": INS, "cloud_mode": "off", "expected_revision": _MAX_REV},
        "malformed": {"install_id": INS, "cloud_mode": "ON", "expected_revision": 2},
    }[case]
    req["opt_in"] = None
    snap = copy.deepcopy(req)
    with pytest.raises(CloudError):
        s.transition(req)
    assert s.record(INS) == before and dict(s._state) == states
    assert _flag_state(flags) == fb and req == snap


@pytest.mark.parametrize("cid", [A, B])
def test_refused_verdict_changes_nothing(cid):
    s, flags = _world()
    before, fb = s.record(INS), _flag_state(flags)
    with pytest.raises(CloudError):
        _ask(s, "cloud", cid)
    assert s.record(INS) == before and _flag_state(flags) == fb


# ---- hostile rows ---------------------------------------------------------

CALLS = []


class StrSub(str):
    pass


class DictSub(dict):
    pass


class ListSub(list):
    pass


class IntSub(int):
    pass


class EqRaises(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("eq")

    __hash__ = str.__hash__


class HashCollide(str):
    def __hash__(self):
        CALLS.append("hash")
        return hash("install_id")

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)


def _base(op):
    if op == "verdict":
        return {"install_id": INS, "collection_id": A, "destination": "cloud"}
    if op == "verdict_local":
        return {"install_id": INS, "collection_id": A, "destination": "local"}
    return {
        "install_id": INS,
        "cloud_mode": "on",
        "expected_revision": 0,
        "opt_in": opt_in_for(INS, 0),
    }


def _hostile_rows():
    rows = []
    for kind in ("verdict", "verdict_local", "transition"):
        op = "verdict" if kind.startswith("verdict") else "transition"
        base = _base(kind)
        start = len(rows)
        rows.append((op, "dict_sub", DictSub(base)))
        rows.append((op, "list", list(base.items())))
        rows.append((op, "str", "install_id"))
        rows.append((op, "none", None))
        for key in base:
            r = dict(base)
            if type(r[key]) is str:
                r[key] = StrSub(r[key])
            elif type(r[key]) is int:
                r[key] = IntSub(r[key])
            rows.append((op, f"{key}_sub", r))
            r = dict(base)
            r[key] = ListSub([r[key]])
            rows.append((op, f"{key}_listsub", r))
            r = dict(base)
            r[key] = DictSub({"v": r[key]})
            rows.append((op, f"{key}_dictsub", r))
            r = dict(base)
            del r[key]
            rows.append((op, f"missing_{key}", r))
            r = dict(base)
            r[StrSub(key)] = r.pop(key)  # real key replaced by a str subclass
            rows.append((op, f"real_key_strsub_{key}", r))
            r = dict(base)
            r[key + "_x"] = r.pop(key)  # same arity, one key renamed
            rows.append((op, f"renamed_key_{key}", r))
        for form, cls in (("plain", str), ("eq_raises", EqRaises), ("hash_collide", HashCollide)):
            r = dict(base)
            r[cls("extra")] = 1
            rows.append((op, f"extra_key_{form}", r))
        r = dict(base)
        r[b"install_id"] = r.pop("install_id")
        rows.append((op, "bytes_key", r))
        for iid in (
            "ins1:" + "A" * 64,
            "ins1:" + "1" * 63,
            "ins1:" + "1" * 65,
            "ins1:" + "1" * 64 + "\n",
            "INS1:" + "1" * 64,
        ):
            rows.append((op, f"iid_{len(iid)}_{iid[:4]}_{iid[-1]!r}", dict(base, install_id=iid)))
        if kind == "verdict_local":
            rows[start:] = [(o, f"local_{n}", r) for o, n, r in rows[start:]]
    v = _base("verdict")
    for cid in ("col1:" + "A" * 64, "col1:" + "a" * 63, "col1:" + "a" * 64 + "\n", None):
        rows.append(("verdict", f"cid_{cid!r:.12}_{len(cid or '')}", dict(v, collection_id=cid)))
    for dest in ("Cloud", "remote", "", "cloud ", None, True, "Local", "local "):
        rows.append(("verdict", f"dest_{dest!r}", dict(v, destination=dest)))
    loc = _base("verdict_local")
    for tag, cid in (
        ("junk", "junk"),
        ("upper", "col1:" + "A" * 64),
        ("short", "col1:" + "a" * 63),
        ("newline", "col1:" + "a" * 64 + "\n"),
        ("strsub", StrSub(A)),
        ("eq_raises", EqRaises(A)),
        ("none", None),
    ):
        rows.append(("verdict", f"local_cid_{tag}", dict(loc, collection_id=cid)))
    for tag, dest in (("strsub", StrSub("local")), ("eq_raises", EqRaises("local"))):
        rows.append(("verdict", f"local_dest_{tag}", dict(loc, destination=dest)))
    t = _base("transition")
    for mode in ("On", "ON", "", "on ", None, True, 1):
        rows.append(("transition", f"mode_{mode!r}", dict(t, cloud_mode=mode)))
    for tag, bad in (
        ("true", True),
        ("false", False),
        ("neg", -1),
        ("float", 1.0),
        ("str", "0"),
        ("none", None),
        ("huge", 10**5000),
        ("2**63", 2**63),
    ):
        rows.append(("transition", f"rev_{tag}", dict(t, expected_revision=bad)))
    good = opt_in_for(INS, 0)
    for tag, token in (
        ("upper", "OPT1:" + "a" * 64),
        ("newline", good + "\n"),
        ("hex63", good[:-1]),
        ("hex65", good + "a"),
        ("apv_prefix", "apv1:" + good[5:]),
        ("strsub", StrSub(good)),
        ("bytes", good.encode()),
        ("false", False),
    ):
        rows.append(("transition", f"opt_in_{tag}", dict(t, opt_in=token)))
    return rows


def _labelled(rows):
    return rows


ROWS = _hostile_rows()


def _snap(req):
    if isinstance(req, dict):
        return [(k, copy.deepcopy(v)) for k, v in dict.items(req)]
    return copy.deepcopy(req)


def _same(v1, v2):
    """Equality that never calls a caller method (str subclasses compare
    through str.__eq__)."""
    if type(v1) is not type(v2):
        return False
    if isinstance(v1, str):
        return str.__eq__(v1, v2) is True
    return v1 == v2


def _unchanged(req, snap):
    if isinstance(req, dict):
        now = list(dict.items(req))
        return len(now) == len(snap) and all(
            k1 is k2 and _same(v1, v2) for (k1, v1), (k2, v2) in zip(now, snap, strict=True)
        )
    return type(req) is type(snap) and req == snap


@pytest.mark.parametrize("op,name,req", ROWS, ids=[f"{o}-{n}" for o, n, _ in ROWS])
def test_hostile_request_fails_closed_typed(op, name, req):
    s, flags = _world()
    before, fb = s.record(INS), _flag_state(flags)
    snap = _snap(req)
    CALLS.clear()
    _raises("malformed_cloud_request", getattr(s, op), req)
    assert CALLS == []
    assert s.record(INS) == before and _flag_state(flags) == fb
    assert _unchanged(req, snap)


@pytest.mark.parametrize(
    "bad",
    [StrSub(INS), INS.upper(), None, [INS], INS + "\n", b"ins1"],
    ids=["strsub", "upper", "none", "list", "newline", "bytes"],
)
def test_register_hostile_ids(bad):
    s = CloudSwitch(FlagStore())
    _raises("malformed_cloud_request", s.register, bad)
    assert s._state == {}


# ---- executable kill check: one-edit mutants of the reference engine ------
# Each edit is applied to THIS file's source; the mutated copy's battery
# (minus this section) must go red. The identity edit must stay green.

MUTANTS = {
    "bump_saturates": ("    if rev >= _MAX_REV:", "    if rev > _MAX_REV:"),
    "local_before_malformed": (
        "        if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:\n",
        '        if dest == "local":\n            return "allow"\n        if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:\n',  # noqa: E501
    ),
    "opt_in_parsed_only_on_turn_on": (
        "        approved = _opt_in_ok(req, iid)\n",
        '        approved = _opt_in_ok(req, iid) if target == "on" else False\n',
    ),
    "cid_search": ("_COL_RE.fullmatch(cid) is None", "_COL_RE.search(cid) is None"),
    "cid_unchecked": (
        '        if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:\n            _fail("malformed_cloud_request")\n',  # noqa: E501
        "",
    ),
    "default_on": (
        'return state is None or state[0] != "on"',
        'return state is not None and state[0] != "on"',
    ),
    "dest_any": (
        "if not _exact_str(dest) or dest not in _DESTINATIONS:",
        "if not _exact_str(dest):",
    ),
    "exhausted_before_noop": (
        '        if target == mode:\n            return self.record(iid)\n        if target == "on"',  # noqa: E501
        '        _bump(rev)\n        if target == mode:\n            return self.record(iid)\n        if target == "on"',  # noqa: E501
    ),
    "exhausted_before_opt_in": (
        '        if target == "on" and not approved:\n            _fail("opt_in_missing")\n        self._state[iid] = (target, _bump(rev))',  # noqa: E501
        '        new_rev = _bump(rev)\n        if target == "on" and not approved:\n            _fail("opt_in_missing")\n        self._state[iid] = (target, new_rev)',  # noqa: E501
    ),
    "flag_first": (
        '        if self.reads_off(iid):\n            _fail("cloud_mode_off")\n        if self._flags.reads_sensitive(cid):\n            _fail("collection_sensitive")\n',  # noqa: E501
        '        if self._flags.reads_sensitive(cid):\n            _fail("collection_sensitive")\n        if self.reads_off(iid):\n            _fail("cloud_mode_off")\n',  # noqa: E501
    ),
    "flag_ignored": ("        if self._flags.reads_sensitive(cid):", "        if False:"),
    "iid_search": (
        "if not _exact_str(iid) or _INS_RE.fullmatch(iid) is None:",
        "if not _exact_str(iid) or _INS_RE.search(iid) is None:",
    ),
    "key_type_skip": (
        '    for k in list(req.keys()):\n        if type(k) is not str:\n            _fail("malformed_cloud_request")\n',  # noqa: E501
        "",
    ),
    "keys_same_arity": (
        "if set(req) != keys:",
        "if len(req) != len(keys):",
    ),
    "keys_subset": ("if set(req) != keys:", "if not keys <= set(req):"),
    "local_gated": ('        if dest == "local":\n            return "allow"\n', ""),
    "mode_any": (
        "if not _exact_str(target) or target not in _MODES:",
        "if not _exact_str(target):",
    ),
    "no_same_value_noop": ("        if target == mode:\n            return self.record(iid)\n", ""),
    "opt_in_after_unknown": (
        '        approved = _opt_in_ok(req, iid)\n        if iid not in self._state:\n            _fail("unknown_install")\n',  # noqa: E501
        '        if iid not in self._state:\n            _fail("unknown_install")\n        approved = _opt_in_ok(req, iid)\n',  # noqa: E501
    ),
    "opt_install_ignored": (
        'return token == opt_in_for(iid, req["expected_revision"])',
        'return token == opt_in_for(INS, req["expected_revision"])',
    ),
    "opt_match": ("_OPT_RE.fullmatch(token) is None", "_OPT_RE.match(token) is None"),
    "opt_none_ok": (
        "    if token is None:\n        return False",
        "    if token is None:\n        return True",
    ),
    "opt_revision_ignored": (
        'return token == opt_in_for(iid, req["expected_revision"])',
        "return token == opt_in_for(iid, 0)",
    ),
    "opt_target_ignored": (
        '    raw = f"{install_id}|{expected_revision}|{target}".encode()',
        '    raw = f"{install_id}|{expected_revision}|on".encode()',
    ),
    "opt_unbound": ('return token == opt_in_for(iid, req["expected_revision"])', "return True"),
    "register_default_on": (
        'self._state.setdefault(install_id, ("off", 0))',
        'self._state.setdefault(install_id, ("on", 0))',
    ),
    "register_unchecked": (
        '        if not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None:\n            _fail("malformed_cloud_request")\n',  # noqa: E501
        "",
    ),
    "req_isinstance": (
        '    if type(req) is not dict:\n        _fail("malformed_cloud_request")',
        '    if not isinstance(req, dict):\n        _fail("malformed_cloud_request")',
    ),
    "reregister_resets": (
        'self._state.setdefault(install_id, ("off", 0))',
        'self._state[install_id] = ("off", 0)',
    ),
    "rev_check_after_unknown": (
        '        if not _exact_rev(req["expected_revision"]):\n            _fail("malformed_cloud_request")\n        approved',  # noqa: E501
        "        approved",
    ),
    "rev_isinstance": (
        "return type(v) is int and 0 <= v <= _MAX_REV",
        "return isinstance(v, int) and 0 <= v <= _MAX_REV",
    ),
    "rev_lower_edge": ("0 <= v <= _MAX_REV", "-1 <= v <= _MAX_REV"),
    "rev_unbounded": ("0 <= v <= _MAX_REV", "0 <= v"),
    "rev_upper_edge": ("0 <= v <= _MAX_REV", "0 <= v < _MAX_REV"),
    "stale_unchecked": (
        '        if req["expected_revision"] != rev:\n            _fail("stale_revision")\n',
        "",
    ),
    "str_isinstance": ("    return type(v) is str\n", "    return isinstance(v, str)\n"),
    "truthy_mode": ('state[0] != "on"', "not state[0]"),
    "turn_off_needs_opt_in": ('if target == "on" and not approved:', "if not approved:"),
    "unknown_unchecked": (
        '        if iid not in self._state:\n            _fail("unknown_install")\n',
        "",
    ),
}
EQUIVALENT = {
    # stored mode is only ever the exact str "off" or "on"
    "reads_off_eq_off": ('state[0] != "on"', 'state[0] == "off"'),
}
_SELF = Path(__file__)


# the battery without this section: every test above, parametrized rows
# expanded; a green copy must run exactly this many and pass them all
EXPECTED_BATTERY = 248


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
