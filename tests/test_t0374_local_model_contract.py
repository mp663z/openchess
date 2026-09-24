"""T0374: privacy local-model contract behavior battery.

Reference engine derived from data/contracts/local_model.yaml. Small
local task models are always available on the desktop host; the large
local model is an explicit opt-in (default off) and only runs on the
desktop. A local-model invocation never sends an outbound payload: any
non-local destination is refused, whatever the cloud mode. While the
large model is on, the reference-machine p95 claims read suspended.
Every failure is typed and leaves state and inputs unchanged.
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

from tools.local_model_contract_lint import (  # noqa: E402
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
_MAX_REV = 2**63 - 1
assert str(_MAX_REV) in _CC["identifiers"]["revision"]["grammar"]
_MODES = ("off", "on")
_TIERS = ("small", "large")
_HOSTS = ("desktop", "web", "server")
_DESTINATIONS = ("local", "cloud")
_VERDICT_KEYS = {"install_id", "tier", "host", "destination"}
_TRANSITION_KEYS = {"install_id", "large_model", "expected_revision", "opt_in"}


class ModelError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(cls):
    raise ModelError(cls)


def opt_in_for(install_id, expected_revision, target="large-on"):
    """The pinned opt-in binding: install, revision, target large-on."""
    raw = f"{install_id}|{expected_revision}|{target}".encode()
    return "lmo1:" + hashlib.sha256(raw).hexdigest()


def _exact_str(v):
    return type(v) is str


def _exact_rev(v):
    return type(v) is int and 0 <= v <= _MAX_REV


def _request(req, keys):
    if type(req) is not dict:
        _fail("malformed_model_request")
    for k in list(req.keys()):
        if type(k) is not str:
            _fail("malformed_model_request")
    if set(req) != keys:
        _fail("malformed_model_request")
    iid = req["install_id"]
    if not _exact_str(iid) or _INS_RE.fullmatch(iid) is None:
        _fail("malformed_model_request")
    return iid


def _member(v, allowed):
    if not _exact_str(v) or v not in allowed:
        _fail("malformed_model_request")
    return v


def _opt_in_ok(req, iid):
    token = req["opt_in"]
    if token is None:
        return False
    if not _exact_str(token) or _OPT_RE.fullmatch(token) is None:
        _fail("malformed_model_request")
    return token == opt_in_for(iid, req["expected_revision"])


def _bump(rev):
    """Fail-closed saturation: never saturate or wrap past the grammar."""
    if rev >= _MAX_REV:
        _fail("revision_exhausted")
    return rev + 1


class LocalModels:
    def __init__(self):
        self._state = {}

    def register(self, install_id):
        if not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None:
            _fail("malformed_model_request")
        self._state.setdefault(install_id, ("off", 0))
        return self.record(install_id)

    def reads_off(self, iid):
        state = self._state.get(iid)
        return state is None or state[0] != "on"

    def claims(self, iid):
        return "valid" if self.reads_off(iid) else "suspended"

    def record(self, iid):
        mode, rev = self._state[iid]
        return dict(zip(_FIELDS, (iid, mode, rev, self.claims(iid)), strict=True))

    def verdict(self, req):
        iid = _request(req, _VERDICT_KEYS)
        tier = _member(req["tier"], _TIERS)
        host = _member(req["host"], _HOSTS)
        dest = _member(req["destination"], _DESTINATIONS)
        if dest != "local":
            _fail("egress_requested")
        if host != "desktop":
            _fail("host_not_desktop")
        if tier == "large" and self.reads_off(iid):
            _fail("large_model_off")
        return {"verdict": "allow", "reference_claims": self.claims(iid)}

    def transition(self, req):
        iid = _request(req, _TRANSITION_KEYS)
        target = _member(req["large_model"], _MODES)
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_model_request")
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
_BOUND = object()


def _world():
    m = LocalModels()
    m.register(INS)
    return m


def _set(m, mode, rev, iid=INS, opt_in=_BOUND):
    if opt_in is _BOUND:
        opt_in = opt_in_for(iid, rev) if mode == "on" else None
    return m.transition(
        {"install_id": iid, "large_model": mode, "expected_revision": rev, "opt_in": opt_in}
    )


def _ask(m, tier="small", host="desktop", dest="local", iid=INS):
    return m.verdict({"install_id": iid, "tier": tier, "host": host, "destination": dest})


def _raises(cls, fn, *args):
    with pytest.raises(ModelError) as ei:
        fn(*args)
    assert ei.value.failure_class == cls
    assert ei.value.code == FAILURE_MAPPING[cls]
    assert ei.value.__cause__ is None
    return ei.value


ALLOW_VALID = {"verdict": "allow", "reference_claims": "valid"}
ALLOW_SUSPENDED = {"verdict": "allow", "reference_claims": "suspended"}


# ---- contract / lint ------------------------------------------------------


def test_contract_lints_clean():
    lint()


def test_error_model_is_closed_and_consistent():
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASSES)
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert len(set(FAILURE_MAPPING.values())) == len(FAILURE_MAPPING)
    assert _CC["errors"]["shape"]["retryable_true_only_for"] == ["internal"]


def test_linked_documents_exist_and_agree():
    adr = (REPO / _CC["links"]["architecture_decision"]).read_text()
    front = yaml.safe_load(adr.split("---")[1])
    llm = front["external_providers"]["local-large-llm"]
    assert llm["mode"] == "local" and llm["opt_in"] is True
    assert llm["default"] in (False, "off")  # YAML 1.1 reads bare off as false
    assert llm["payload"] == "none-local-only" and llm["suspends_reference_claims"] is True
    assert front["operations"]["model-inference"] == "desktop"
    own = (REPO / _CC["links"]["platform_ownership"]).read_text()
    own_front = yaml.safe_load(own.split("---")[1])
    assert own_front["capabilities"]["models"]["compute_owner"] == "desktop"


LINT_MUTATIONS = {
    "default_on": lambda c: c["semantics"].__setitem__(
        "default", "unknown-or-unset-install-reads-large-model-on"
    ),
    "small_needs_opt_in": lambda c: c["semantics"].__setitem__(
        "small_tier", "available-after-opt-in"
    ),
    "large_any_host": lambda c: c["semantics"].__setitem__(
        "large_tier", "available-while-large-model-is-on"
    ),
    "host_widened": lambda c: c["semantics"].__setitem__("host", "model-inference-runs-anywhere"),
    "egress_allowed": lambda c: c["semantics"].__setitem__(
        "egress", "a-local-model-invocation-may-send-when-cloud-is-on"
    ),
    "precedence_mode_first": lambda c: c["semantics"].__setitem__(
        "precedence", "malformed-then-large-model-mode-then-host-then-egress"
    ),
    "claims_not_suspended": lambda c: c["semantics"].__setitem__(
        "claims", "activation-keeps-the-reference-claims"
    ),
    "cloud_coupled": lambda c: c["semantics"].__setitem__(
        "cloud_independence", "the-local-model-verdict-requires-cloud-on"
    ),
    "cached_verdict": lambda c: c["semantics"].__setitem__(
        "evaluation", "verdict-cached-per-session"
    ),
    "turn_on_without_opt_in": lambda c: c["semantics"].__setitem__(
        "turn_on", "off-to-on-applies-immediately"
    ),
    "turn_off_needs_approval": lambda c: c["semantics"].__setitem__(
        "turn_off", "on-to-off-needs-an-approval"
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
        "egress_requested", "host_refused"
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
    "claims_caller_set": lambda c: c["identifiers"]["reference_claims"].__setitem__(
        "source", "caller-set"
    ),
    "host_grammar_widened": lambda c: c["identifiers"]["host"].__setitem__(
        "grammar", "exact-built-in-str-any"
    ),
    "record_field_dropped": lambda c: c["record"]["fields"].remove("reference_claims"),
    "record_exact_int": lambda c: c["record"].__setitem__("exact", 1),
    "role_not_scope_changed": lambda c: c["role"].__setitem__("not_scope", "none"),
    "failure_class_dropped": lambda c: c["failures"]["classes"].remove("host_not_desktop"),
    "failure_class_renamed": lambda c: c["failures"]["classes"].__setitem__(0, "bad_request"),
    "trigger_reworded": lambda c: c["failures"]["triggers"].__setitem__(
        "egress_requested", "cloud-destination"
    ),
    "failures_extra_key": lambda c: c["failures"].__setitem__("notes", "x"),
    "properties_no_egress_changed": lambda c: c["properties"].__setitem__(
        "no_egress", "best-effort"
    ),
    "base_path_changed": lambda c: c["versioning"].__setitem__(
        "base_path", "/privacy/local-model/v2"
    ),
    "link_changed": lambda c: c["links"].__setitem__(
        "architecture_decision", "docs/adr/ADR-0004.md"
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


def test_register_defaults_off_with_valid_claims():
    m = LocalModels()
    assert m.register(INS) == {
        "install_id": INS,
        "large_model": "off",
        "revision": 0,
        "reference_claims": "valid",
    }


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("iid", [INS, INS2])
def test_small_tier_is_always_available_on_desktop(mode, iid):
    m = _world()
    if mode == "on":
        _set(m, "on", 0)
    expected = ALLOW_SUSPENDED if (mode == "on" and iid == INS) else ALLOW_VALID
    assert _ask(m, "small", "desktop", "local", iid) == expected


@pytest.mark.parametrize("iid", [INS, INS2])
def test_large_tier_is_off_by_default(iid):
    m = _world()
    _raises("large_model_off", _ask, m, "large", "desktop", "local", iid)


def test_turn_on_with_bound_opt_in_opens_large_and_suspends_claims():
    m = _world()
    assert _set(m, "on", 0) == {
        "install_id": INS,
        "large_model": "on",
        "revision": 1,
        "reference_claims": "suspended",
    }
    assert _ask(m, "large") == ALLOW_SUSPENDED
    assert _ask(m, "small") == ALLOW_SUSPENDED


def test_turn_off_needs_no_opt_in_restores_claims_and_applies_before_the_next_verdict():
    m = _world()
    _set(m, "on", 0)
    assert _set(m, "off", 1, opt_in=None)["reference_claims"] == "valid"
    _raises("large_model_off", _ask, m, "large")
    assert _ask(m, "small") == ALLOW_VALID


def test_unknown_install_reads_off_even_when_another_install_is_on():
    m = _world()
    _set(m, "on", 0)
    _raises("large_model_off", _ask, m, "large", "desktop", "local", INS2)
    assert _ask(m, "small", "desktop", "local", INS2) == ALLOW_VALID


@pytest.mark.parametrize("host", ["web", "server"])
@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("mode", _MODES)
def test_web_and_server_never_run_a_model(host, tier, mode):
    m = _world()
    if mode == "on":
        _set(m, "on", 0)
    _raises("host_not_desktop", _ask, m, tier, host)


@pytest.mark.parametrize("host", _HOSTS)
@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("mode", _MODES)
def test_a_non_local_destination_is_always_refused(host, tier, mode):
    m = _world()
    if mode == "on":
        _set(m, "on", 0)
    _raises("egress_requested", _ask, m, tier, host, "cloud")


def test_precedence_egress_then_host_then_mode():
    m = _world()  # large off
    _raises("egress_requested", _ask, m, "large", "web", "cloud")
    _raises("host_not_desktop", _ask, m, "large", "web", "local")
    _raises("large_model_off", _ask, m, "large", "desktop", "local")


def test_verdict_is_read_at_invocation_time_never_cached():
    m = _world()
    _raises("large_model_off", _ask, m, "large")
    _set(m, "on", 0)
    assert _ask(m, "large") == ALLOW_SUSPENDED
    _set(m, "off", 1)
    _raises("large_model_off", _ask, m, "large")
    assert _ask(m, "small") == ALLOW_VALID


@pytest.mark.parametrize("mode", _MODES)
def test_same_value_is_noop(mode):
    m = _world()
    if mode == "on":
        _set(m, "on", 0)
    before = m.record(INS)
    assert _set(m, mode, before["revision"], opt_in=None) == before


def test_reregister_never_resets_mode_revision_or_claims():
    m = _world()
    _set(m, "on", 0)
    assert m.register(INS) == {
        "install_id": INS,
        "large_model": "on",
        "revision": 1,
        "reference_claims": "suspended",
    }


def test_well_formed_opt_in_on_turn_off_is_ignored():
    m = _world()
    _set(m, "on", 0)
    assert _set(m, "off", 1, opt_in="lmo1:" + "f" * 64)["revision"] == 2


# ---- opt-in / conflict / unknown -------------------------------------------


@pytest.mark.parametrize(
    "tag",
    ["none", "other_install", "other_revision", "target_other", "random", "cloud_off_token"],
)
def test_turn_on_without_a_bound_opt_in_is_refused(tag):
    m = _world()
    token = {
        "none": None,
        "other_install": opt_in_for(INS2, 0),
        "other_revision": opt_in_for(INS, 1),
        "target_other": opt_in_for(INS, 0, target="on"),
        "random": "lmo1:" + "e" * 64,
        # a T0365 cloud opt-in digest under the local-model prefix
        "cloud_off_token": "lmo1:" + hashlib.sha256(f"{INS}|0|on".encode()).hexdigest(),
    }[tag]
    before = m.record(INS)
    _raises("opt_in_missing", _set, m, "on", 0, INS, token)
    assert m.record(INS) == before
    _raises("large_model_off", _ask, m, "large")


def test_an_opt_in_is_bound_to_its_install_both_ways():
    m = _world()
    m.register(INS2)
    _raises("opt_in_missing", _set, m, "on", 0, INS2, opt_in_for(INS, 0))
    _raises("opt_in_missing", _set, m, "on", 0, INS, opt_in_for(INS2, 0))
    assert _set(m, "on", 0, INS2)["large_model"] == "on"
    _raises("large_model_off", _ask, m, "large")


def test_an_old_opt_in_is_never_reusable():
    m = _world()
    old = opt_in_for(INS, 0)
    _set(m, "on", 0, opt_in=old)
    _set(m, "off", 1)
    _raises("opt_in_missing", _set, m, "on", 2, INS, old)
    assert _set(m, "on", 2)["revision"] == 3


@pytest.mark.parametrize("rev", [1, 2, _MAX_REV])
def test_stale_revision_is_conflict(rev):
    m = _world()
    before = m.record(INS)
    _raises("stale_revision", _set, m, "on", rev)
    assert m.record(INS) == before


def test_stale_revision_edges_after_transitions():
    m = _world()
    _set(m, "on", 0)
    _set(m, "off", 1)
    for rev in (1, 3):
        _raises("stale_revision", _set, m, "on", rev)
        _raises("stale_revision", _set, m, "off", rev)
    assert _set(m, "on", 2)["revision"] == 3


def test_unknown_install_on_transition():
    m = _world()
    _raises("unknown_install", _set, m, "on", 0, INS2)
    _raises("unknown_install", _set, m, "off", 0, INS2)
    assert set(m._state) == {INS}


@pytest.mark.parametrize("which", ["unknown", "stale"])
def test_malformed_opt_in_precedes_unknown_and_stale(which):
    m = _world()
    iid, rev = (INS2, 0) if which == "unknown" else (INS, 5)
    _raises("malformed_model_request", _set, m, "on", rev, iid, "LMO1:" + "a" * 64)


def test_unknown_precedes_stale_and_stale_precedes_opt_in():
    m = _world()
    _raises("unknown_install", _set, m, "on", 7, INS2, None)
    _raises("stale_revision", _set, m, "on", 7, INS, None)


@pytest.mark.parametrize(
    "token",
    ["junk", "LMO1:" + "a" * 64, "STRSUB", 7, "EQRAISES", "lmo1:" + "a" * 63],
    ids=["junk", "upper", "strsub", "int", "eq_raises", "hex63"],
)
def test_malformed_opt_in_on_turn_off_is_malformed(token):
    m = _world()
    _set(m, "on", 0)
    if token == "STRSUB":
        token = StrSub(opt_in_for(INS, 1))
    elif token == "EQRAISES":
        token = EqRaises(opt_in_for(INS, 1))
    before = m.record(INS)
    CALLS.clear()
    _raises("malformed_model_request", _set, m, "off", 1, INS, token)
    assert CALLS == [] and m.record(INS) == before
    assert _ask(m, "large") == ALLOW_SUSPENDED  # still on


# ---- saturation ---------------------------------------------------------------


def _at(m, rev, mode="off"):
    m._state[INS] = (mode, rev)


@pytest.mark.parametrize("target", _MODES)
def test_change_at_max_revision_fails_typed_state_unchanged(target):
    m = _world()
    _at(m, _MAX_REV, "on" if target == "off" else "off")
    before = m.record(INS)
    _raises("revision_exhausted", _set, m, target, _MAX_REV)
    assert m.record(INS) == before


@pytest.mark.parametrize("target", _MODES)
def test_change_just_below_max_revision_commits_to_max(target):
    m = _world()
    _at(m, _MAX_REV - 1, "on" if target == "off" else "off")
    assert _set(m, target, _MAX_REV - 1)["revision"] == _MAX_REV


def test_unapproved_turn_on_at_max_revision_is_opt_in_missing():
    m = _world()
    _at(m, _MAX_REV)
    _raises("opt_in_missing", _set, m, "on", _MAX_REV, INS, None)


@pytest.mark.parametrize("mode", _MODES)
def test_same_value_at_max_revision_is_a_noop(mode):
    m = _world()
    _at(m, _MAX_REV, mode)
    assert _set(m, mode, _MAX_REV, opt_in=None)["revision"] == _MAX_REV


@pytest.mark.parametrize("rev", [_MAX_REV, 0])
def test_revision_edges_are_well_formed(rev):
    m = _world()
    _at(m, 1)
    _raises("stale_revision", _set, m, "on", rev)


# ---- rollback: rejected transitions are rolled back whole ------------------


def test_undoing_on_is_turn_off_and_undoing_off_needs_a_fresh_opt_in():
    m = _world()
    first = opt_in_for(INS, 0)
    _set(m, "on", 0, opt_in=first)
    _set(m, "off", 1)
    _raises("opt_in_missing", _set, m, "on", 2, INS, first)
    _raises("large_model_off", _ask, m, "large")


@pytest.mark.parametrize(
    "case",
    ["opt_in_missing", "stale", "unknown", "exhausted", "malformed"],
)
def test_rejected_transition_leaves_everything_unchanged(case):
    m = _world()
    _set(m, "on", 0)
    _set(m, "off", 1)
    if case == "exhausted":
        _at(m, _MAX_REV, "on")
    before, states = m.record(INS), dict(m._state)
    req = {
        "opt_in_missing": {"install_id": INS, "large_model": "on", "expected_revision": 2},
        "stale": {"install_id": INS, "large_model": "on", "expected_revision": 1},
        "unknown": {"install_id": INS2, "large_model": "on", "expected_revision": 0},
        "exhausted": {"install_id": INS, "large_model": "off", "expected_revision": _MAX_REV},
        "malformed": {"install_id": INS, "large_model": "ON", "expected_revision": 2},
    }[case]
    req["opt_in"] = None
    snap = copy.deepcopy(req)
    with pytest.raises(ModelError):
        m.transition(req)
    assert m.record(INS) == before and dict(m._state) == states and req == snap


@pytest.mark.parametrize(
    "args",
    [("large", "desktop", "local"), ("small", "web", "local"), ("small", "desktop", "cloud")],
    ids=["large_off", "web", "cloud"],
)
def test_refused_verdict_changes_nothing(args):
    m = _world()
    before, states = m.record(INS), dict(m._state)
    with pytest.raises(ModelError):
        _ask(m, *args)
    assert m.record(INS) == before and dict(m._state) == states


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


def _base(kind):
    if kind == "verdict":
        return {"install_id": INS, "tier": "large", "host": "desktop", "destination": "local"}
    if kind == "verdict_small":
        return {"install_id": INS, "tier": "small", "host": "desktop", "destination": "local"}
    return {
        "install_id": INS,
        "large_model": "on",
        "expected_revision": 0,
        "opt_in": opt_in_for(INS, 0),
    }


def _hostile_rows():
    rows = []
    for kind in ("verdict", "verdict_small", "transition"):
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
            if type(base[key]) is str:
                r = dict(base)
                r[key] = EqRaises(base[key])
                rows.append((op, f"{key}_eq_raises_value", r))
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
        if kind == "verdict_small":
            rows[start:] = [(o, f"small_{n}", r) for o, n, r in rows[start:]]
    v = _base("verdict_small")
    for field, values in (
        ("tier", ("Small", "medium", "", "small ", None, True)),
        ("host", ("Desktop", "laptop", "", "desktop\n", None, 1)),
        ("destination", ("Local", "remote", "", "local ", None, False)),
    ):
        for bad in values:
            rows.append(("verdict", f"{field}_{bad!r}", dict(v, **{field: bad})))
    t = _base("transition")
    for mode in ("On", "ON", "", "on ", None, True, 1):
        rows.append(("transition", f"mode_{mode!r}", dict(t, large_model=mode)))
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
        ("upper", "LMO1:" + "a" * 64),
        ("newline", good + "\n"),
        ("hex63", good[:-1]),
        ("hex65", good + "a"),
        ("opt1_prefix", "opt1:" + good[5:]),
        ("strsub", StrSub(good)),
        ("bytes", good.encode()),
        ("false", False),
    ):
        rows.append(("transition", f"opt_in_{tag}", dict(t, opt_in=token)))
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
    m = _world()
    before, states = m.record(INS), dict(m._state)
    snap = _snap(req)
    CALLS.clear()
    _raises("malformed_model_request", getattr(m, op), req)
    assert CALLS == []
    assert m.record(INS) == before and dict(m._state) == states
    assert _unchanged(req, snap)


@pytest.mark.parametrize(
    "bad",
    [StrSub(INS), INS.upper(), None, [INS], INS + "\n", b"ins1"],
    ids=["strsub", "upper", "none", "list", "newline", "bytes"],
)
def test_register_hostile_ids(bad):
    m = LocalModels()
    _raises("malformed_model_request", m.register, bad)
    assert m._state == {}


# ---- executable kill check: one-edit mutants of the reference engine ------
# Each edit is applied to THIS file's source; the mutated copy's battery
# (minus this section) must go red. The identity edit must stay green.

MUTANTS = {
    "default_on": (
        'return state is None or state[0] != "on"',
        'return state is not None and state[0] != "on"',
    ),
    "truthy_mode": (
        'state[0] != "on"',
        "not state[0]",
    ),
    "claims_inverted": (
        'return "valid" if self.reads_off(iid) else "suspended"',
        'return "suspended" if self.reads_off(iid) else "valid"',
    ),
    "claims_always_valid": (
        'return "valid" if self.reads_off(iid) else "suspended"',
        'return "valid"',
    ),
    "verdict_drops_claims": (
        'return {"verdict": "allow", "reference_claims": self.claims(iid)}',
        'return {"verdict": "allow", "reference_claims": "valid"}',
    ),
    "egress_allowed": (
        '        if dest != "local":\n            _fail("egress_requested")\n',
        "",
    ),
    "host_any": (
        '        if host != "desktop":\n            _fail("host_not_desktop")\n',
        "",
    ),
    "host_web_ok": (
        'if host != "desktop":',
        'if host == "server":',
    ),
    "large_ungated": (
        '        if tier == "large" and self.reads_off(iid):',
        "        if False:",
    ),
    "small_gated": (
        '        if tier == "large" and self.reads_off(iid):',
        "        if self.reads_off(iid):",
    ),
    "host_before_egress": (
        (
            '        if dest != "local":\n'
            '            _fail("egress_requested")\n'
            '        if host != "desktop":\n'
            '            _fail("host_not_desktop")\n'
        ),
        (
            '        if host != "desktop":\n'
            '            _fail("host_not_desktop")\n'
            '        if dest != "local":\n'
            '            _fail("egress_requested")\n'
        ),
    ),
    "mode_before_host": (
        (
            '        if host != "desktop":\n'
            '            _fail("host_not_desktop")\n'
            '        if tier == "large" and self.reads_off(iid):\n'
            '            _fail("large_model_off")\n'
        ),
        (
            '        if tier == "large" and self.reads_off(iid):\n'
            '            _fail("large_model_off")\n'
            '        if host != "desktop":\n'
            '            _fail("host_not_desktop")\n'
        ),
    ),
    "member_any": (
        "    if not _exact_str(v) or v not in allowed:",
        "    if not _exact_str(v):",
    ),
    "member_isinstance": (
        "    if not _exact_str(v) or v not in allowed:",
        "    if not isinstance(v, str) or v not in allowed:",
    ),
    "iid_search": (
        "if not _exact_str(iid) or _INS_RE.fullmatch(iid) is None:",
        "if not _exact_str(iid) or _INS_RE.search(iid) is None:",
    ),
    "opt_match": (
        "_OPT_RE.fullmatch(token) is None",
        "_OPT_RE.match(token) is None",
    ),
    "opt_unbound": (
        'return token == opt_in_for(iid, req["expected_revision"])',
        "return True",
    ),
    "opt_revision_ignored": (
        'return token == opt_in_for(iid, req["expected_revision"])',
        "return token == opt_in_for(iid, 0)",
    ),
    "opt_install_ignored": (
        'return token == opt_in_for(iid, req["expected_revision"])',
        'return token == opt_in_for(INS, req["expected_revision"])',
    ),
    "opt_target_ignored": (
        '    raw = f"{install_id}|{expected_revision}|{target}".encode()',
        '    raw = f"{install_id}|{expected_revision}|on".encode()',
    ),
    "opt_none_ok": (
        "    if token is None:\n        return False",
        "    if token is None:\n        return True",
    ),
    "opt_falsy_none": (
        "    if token is None:\n        return False",
        "    if not token:\n        return False",
    ),
    "opt_in_parsed_only_on_turn_on": (
        "        approved = _opt_in_ok(req, iid)\n",
        '        approved = _opt_in_ok(req, iid) if target == "on" else False\n',
    ),
    "opt_in_after_unknown": (
        (
            "        approved = _opt_in_ok(req, iid)\n"
            "        if iid not in self._state:\n"
            '            _fail("unknown_install")\n'
        ),
        (
            "        if iid not in self._state:\n"
            '            _fail("unknown_install")\n'
            "        approved = _opt_in_ok(req, iid)\n"
        ),
    ),
    "turn_off_needs_opt_in": (
        'if target == "on" and not approved:',
        "if not approved:",
    ),
    "no_same_value_noop": (
        "        if target == mode:\n            return self.record(iid)\n",
        "",
    ),
    "stale_unchecked": (
        '        if req["expected_revision"] != rev:\n            _fail("stale_revision")\n',
        "",
    ),
    "unknown_unchecked": (
        '        if iid not in self._state:\n            _fail("unknown_install")\n',
        "",
    ),
    "bump_saturates": (
        "    if rev >= _MAX_REV:",
        "    if rev > _MAX_REV:",
    ),
    "rev_isinstance": (
        "return type(v) is int and 0 <= v <= _MAX_REV",
        "return isinstance(v, int) and 0 <= v <= _MAX_REV",
    ),
    "rev_unbounded": (
        "0 <= v <= _MAX_REV",
        "0 <= v",
    ),
    "rev_lower_edge": (
        "0 <= v <= _MAX_REV",
        "-1 <= v <= _MAX_REV",
    ),
    "rev_upper_edge": (
        "0 <= v <= _MAX_REV",
        "0 <= v < _MAX_REV",
    ),
    "rev_check_after_unknown": (
        (
            '        if not _exact_rev(req["expected_revision"]):\n'
            '            _fail("malformed_model_request")\n'
            "        approved"
        ),
        "        approved",
    ),
    "keys_subset": (
        "if set(req) != keys:",
        "if not keys <= set(req):",
    ),
    "keys_same_arity": (
        "if set(req) != keys:",
        "if len(req) != len(keys):",
    ),
    "key_type_skip": (
        (
            "    for k in list(req.keys()):\n"
            "        if type(k) is not str:\n"
            '            _fail("malformed_model_request")\n'
        ),
        "",
    ),
    "req_isinstance": (
        '    if type(req) is not dict:\n        _fail("malformed_model_request")',
        '    if not isinstance(req, dict):\n        _fail("malformed_model_request")',
    ),
    "str_isinstance": (
        "    return type(v) is str\n",
        "    return isinstance(v, str)\n",
    ),
    "register_unchecked": (
        (
            "        if not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None:\n"
            '            _fail("malformed_model_request")\n'
        ),
        "",
    ),
    "reregister_resets": (
        'self._state.setdefault(install_id, ("off", 0))',
        'self._state[install_id] = ("off", 0)',
    ),
    "register_default_on": (
        'self._state.setdefault(install_id, ("off", 0))',
        'self._state.setdefault(install_id, ("on", 0))',
    ),
    "exhausted_before_opt_in": (
        (
            '        if target == "on" and not approved:\n'
            '            _fail("opt_in_missing")\n'
            "        self._state[iid] = (target, _bump(rev))"
        ),
        (
            "        new_rev = _bump(rev)\n"
            '        if target == "on" and not approved:\n'
            '            _fail("opt_in_missing")\n'
            "        self._state[iid] = (target, new_rev)"
        ),
    ),
    "exhausted_before_noop": (
        (
            "        if target == mode:\n"
            "            return self.record(iid)\n"
            '        if target == "on"'
        ),
        (
            "        _bump(rev)\n"
            "        if target == mode:\n"
            "            return self.record(iid)\n"
            '        if target == "on"'
        ),
    ),
    "dest_checked_after_mode": (
        (
            '        dest = _member(req["destination"], _DESTINATIONS)\n'
            '        if dest != "local":\n'
            '            _fail("egress_requested")\n'
        ),
        (
            '        dest = req["destination"]\n'
            '        if dest != "local" and dest in _DESTINATIONS:\n'
            '            _fail("egress_requested")\n'
        ),
    ),
}
EQUIVALENT = {
    # mode is only ever "on"/"off", so != "on" and == "off" agree
    "reads_off_eq_off": ('state[0] != "on"', 'state[0] == "off"'),
}
_SELF = Path(__file__)


# the battery without this section: every test above, parametrized rows
# expanded; a green copy must run exactly this many and pass them all
EXPECTED_BATTERY = 283


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
