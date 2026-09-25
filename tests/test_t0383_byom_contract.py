"""T0383: privacy BYOM contract behavior battery.

Reference engine derived from data/contracts/byom.yaml. Per install and
provider, the user accepts the provider terms the adapter exposes
(retention, region, training) by digest, with an approval bound to that
install, provider, terms digest and revision. A hosted-BYOM send is
allowed only when its payload passes the closed ADR-0004 schema, the
T0365 cloud verdict (composed, never re-owned) allows cloud for the
collection, and the adapter's current terms digest equals the accepted
one. Any terms change lapses the acceptance. Revoking needs no approval.
Every failure is typed and leaves terms state, switch state, flag state
and inputs unchanged.
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
from tests.test_t0365_cloud_off_contract import (  # noqa: E402
    CloudError,
    CloudSwitch,
    opt_in_for,
)
from tools.byom_contract_lint import (  # noqa: E402
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
_ID = _CC["identifiers"]
_FIELDS = _CC["record"]["fields"]
_INS_RE = re.compile(_ID["install_id"]["grammar"])
_PRV_RE = re.compile(_ID["provider_id"]["grammar"])
_TRM_RE = re.compile(_ID["terms_digest"]["grammar"])
_BYT_RE = re.compile(_ID["approval"]["grammar"])
_REGION_RE = re.compile(_ID["terms"]["region"].split("matching-", 1)[1])
_COL_RE = re.compile(
    yaml.safe_load((REPO / _CC["links"]["sensitive_flag_contract"]).read_text())["contract"][
        "identifiers"
    ]["collection_id"]["grammar"]
)
_MAX_REV = 2**63 - 1
assert str(_MAX_REV) in _ID["revision"]["grammar"]
_MAX_DAYS = 3650
assert "0-through-3650" in _ID["terms"]["retention_days"]
_TERMS_KEYS = set(_ID["terms"]["fields"])
_PL = _ID["payload"]
_PAYLOAD_KEYS = set(_PL["allowed_keys"])
_MANDATORY = set(_PL["mandatory_keys"])
_TASKS = ("delta-explanation", "error-diagnosis", "weekly-plan", "review-conversation")
assert all(t in _PL["task"] for t in _TASKS)
_FEN_MAX, _MOVES_MAX, _MOVE_MAX, _TOKENS_MAX = 128, 512, 16, 4096
assert "1-through-128" in _PL["fen"] and "0-through-512" in _PL["moves"]
assert "1-through-16" in _PL["moves"] and "1-through-4096" in _PL["max-tokens"]
_SEND_KEYS = {"install_id", "provider_id", "collection_id", "terms", "payload"}
_TRANSITION_KEYS = {"install_id", "provider_id", "terms", "expected_revision", "approval"}
_CLOUD_REFUSALS = ("cloud_mode_off", "collection_sensitive")


class ByomError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(cls):
    raise ByomError(cls)


def terms_digest(terms):
    """The pinned terms digest: retention_days|region|trains_on_inputs."""
    trains = "true" if terms["trains_on_inputs"] else "false"
    raw = f"{terms['retention_days']}|{terms['region']}|{trains}".encode()
    return "trm1:" + hashlib.sha256(raw).hexdigest()


def byom_approval_for(install_id, provider_id, digest, expected_revision):
    """The pinned approval binding: install, provider, terms digest, revision."""
    raw = f"{install_id}|{provider_id}|{digest}|{expected_revision}".encode()
    return "byt1:" + hashlib.sha256(raw).hexdigest()


def _exact_str(v):
    return type(v) is str


def _exact_rev(v):
    return type(v) is int and 0 <= v <= _MAX_REV


def _printable(s, lo):
    return all(lo <= ord(c) <= 0x7E for c in s)


def _request(req, keys):
    if type(req) is not dict:
        _fail("malformed_byom_request")
    for k in list(req.keys()):
        if type(k) is not str:
            _fail("malformed_byom_request")
    if set(req) != keys:
        _fail("malformed_byom_request")
    iid = req["install_id"]
    if not _exact_str(iid) or _INS_RE.fullmatch(iid) is None:
        _fail("malformed_byom_request")
    pid = req["provider_id"]
    if not _exact_str(pid) or _PRV_RE.fullmatch(pid) is None:
        _fail("malformed_byom_request")
    return iid, pid


def _terms(t):
    if type(t) is not dict:
        _fail("malformed_byom_request")
    for k in list(t.keys()):
        if type(k) is not str:
            _fail("malformed_byom_request")
    if set(t) != _TERMS_KEYS:
        _fail("malformed_byom_request")
    days = t["retention_days"]
    if type(days) is not int or not 0 <= days <= _MAX_DAYS:
        _fail("malformed_byom_request")
    region = t["region"]
    if not _exact_str(region) or _REGION_RE.fullmatch(region) is None:
        _fail("malformed_byom_request")
    if type(t["trains_on_inputs"]) is not bool:
        _fail("malformed_byom_request")
    return terms_digest(t)


def _payload(p):
    if type(p) is not dict:
        _fail("payload_rejected")
    for k in list(p.keys()):
        if type(k) is not str:
            _fail("payload_rejected")
    keys = set(p)
    if not keys <= _PAYLOAD_KEYS or not keys >= _MANDATORY:
        _fail("payload_rejected")
    fen = p["fen"]
    if not _exact_str(fen) or not 1 <= len(fen) <= _FEN_MAX or not _printable(fen, 0x20):
        _fail("payload_rejected")
    if "moves" in keys:
        moves = p["moves"]
        if type(moves) is not list or len(moves) > _MOVES_MAX:
            _fail("payload_rejected")
        for m in moves:
            if not _exact_str(m) or not 1 <= len(m) <= _MOVE_MAX or not _printable(m, 0x21):
                _fail("payload_rejected")
    task = p["task"]
    if not _exact_str(task) or task not in _TASKS:
        _fail("payload_rejected")
    if "max-tokens" in keys:
        n = p["max-tokens"]
        if type(n) is not int or not 1 <= n <= _TOKENS_MAX:
            _fail("payload_rejected")


def _approval_ok(req, iid, pid, target):
    token = req["approval"]
    if token is None:
        return False
    if not _exact_str(token) or _BYT_RE.fullmatch(token) is None:
        _fail("malformed_byom_request")
    if target is None:
        return False
    return token == byom_approval_for(iid, pid, target, req["expected_revision"])


def _bump(rev):
    """Fail-closed saturation: never saturate or wrap past the grammar."""
    if rev >= _MAX_REV:
        _fail("revision_exhausted")
    return rev + 1


class ByomStore:
    def __init__(self, cloud):
        self._cloud = cloud
        self._state = {}

    def register(self, install_id, provider_id):
        if not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None:
            _fail("malformed_byom_request")
        if not _exact_str(provider_id) or _PRV_RE.fullmatch(provider_id) is None:
            _fail("malformed_byom_request")
        self._state.setdefault((install_id, provider_id), (None, 0))
        return self.record(install_id, provider_id)

    def record(self, iid, pid):
        accepted, rev = self._state[(iid, pid)]
        return dict(zip(_FIELDS, (iid, pid, accepted, rev), strict=True))

    def accepted(self, iid, pid):
        state = self._state.get((iid, pid))
        return None if state is None else state[0]

    def _cloud_refusal(self, iid, cid):
        try:
            self._cloud.verdict({"install_id": iid, "collection_id": cid, "destination": "cloud"})
        except CloudError as exc:
            cls = exc.failure_class
            return cls if cls in _CLOUD_REFUSALS else "malformed_byom_request"
        return None

    def send(self, req):
        iid, pid = _request(req, _SEND_KEYS)
        cid = req["collection_id"]
        if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:
            _fail("malformed_byom_request")
        digest = _terms(req["terms"])
        _payload(req["payload"])
        refusal = self._cloud_refusal(iid, cid)
        if refusal is not None:
            _fail(refusal)
        accepted = self.accepted(iid, pid)
        if accepted is None:
            _fail("terms_not_accepted")
        if accepted != digest:
            _fail("terms_changed")
        return {"verdict": "allow", "terms_digest": digest}

    def transition(self, req):
        iid, pid = _request(req, _TRANSITION_KEYS)
        terms = req["terms"]
        target = None if terms is None else _terms(terms)
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_byom_request")
        approved = _approval_ok(req, iid, pid, target)
        if (iid, pid) not in self._state:
            _fail("unknown_provider")
        accepted, rev = self._state[(iid, pid)]
        if req["expected_revision"] != rev:
            _fail("stale_revision")
        if target == accepted:
            return self.record(iid, pid)
        if target is not None and not approved:
            _fail("approval_missing")
        # a revoke is never refused: at max revision it writes null in place
        new_rev = rev if target is None and rev == _MAX_REV else _bump(rev)
        self._state[(iid, pid)] = (target, new_rev)
        return self.record(iid, pid)


INS = "ins1:" + "1" * 64
INS2 = "ins1:" + "2" * 64
P = "prv1:acme"
P2 = "prv1:other"
A = "col1:" + "a" * 64
B = "col1:" + "b" * 64
T = {"retention_days": 30, "region": "eu", "trains_on_inputs": False}
T2 = {"retention_days": 0, "region": "us", "trains_on_inputs": False}
FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
PAYLOAD = {"fen": FEN, "moves": ["e4", "e5"], "task": "delta-explanation", "max-tokens": 512}


def _world(cloud_on=True, open_a=True):
    """Collection A open (flag false) when OPEN_A, B sensitive; install INS
    cloud mode on when CLOUD_ON; provider P registered with no terms."""
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
    cloud = CloudSwitch(flags)
    cloud.register(INS)
    if cloud_on:
        cloud.transition(
            {
                "install_id": INS,
                "cloud_mode": "on",
                "expected_revision": 0,
                "opt_in": opt_in_for(INS, 0),
            }
        )
    store = ByomStore(cloud)
    store.register(INS, P)
    return store, cloud, flags


_BOUND = object()


def _set(s, terms, rev, iid=INS, pid=P, approval=_BOUND):
    if approval is _BOUND:
        approval = None if terms is None else byom_approval_for(iid, pid, terms_digest(terms), rev)
    return s.transition(
        {
            "install_id": iid,
            "provider_id": pid,
            "terms": terms,
            "expected_revision": rev,
            "approval": approval,
        }
    )


_DEFAULT = object()


def _send(s, terms=_DEFAULT, payload=_DEFAULT, cid=A, iid=INS, pid=P):
    return s.send(
        {
            "install_id": iid,
            "provider_id": pid,
            "collection_id": cid,
            "terms": dict(T) if terms is _DEFAULT else terms,
            "payload": dict(PAYLOAD) if payload is _DEFAULT else payload,
        }
    )


def _raises(cls, fn, *args):
    with pytest.raises(ByomError) as ei:
        fn(*args)
    assert ei.value.failure_class == cls
    assert ei.value.code == FAILURE_MAPPING[cls]
    assert ei.value.__cause__ is None
    return ei.value


def _all_state(s, cloud, flags):
    return (
        dict(s._state),
        dict(cloud._state),
        {cid: flags.record(cid) for cid in (A, B)},
    )


def _at(s, rev, accepted=None):
    s._state[(INS, P)] = (accepted, rev)


# ---- contract / lint ------------------------------------------------------


def test_contract_lints_clean():
    lint()


def test_error_model_is_closed_and_consistent():
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASSES)
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert _CC["errors"]["shape"]["retryable_true_only_for"] == ["internal"]
    assert FAILURE_MAPPING["cloud_mode_off"] == FAILURE_MAPPING["collection_sensitive"]
    assert FAILURE_MAPPING["terms_not_accepted"] == FAILURE_MAPPING["terms_changed"]


def test_linked_documents_exist_and_agree():
    adr = (REPO / _CC["links"]["architecture_decision"]).read_text()
    front = yaml.safe_load(adr.split("---")[1])
    h = front["external_providers"]["hosted-byom"]
    assert h["opt_in"] is True and h["default"] is False  # YAML `off`
    assert h["mode"] == "hosted" and h["invocation_additional_properties"] is False
    assert h["relay_plaintext"] is False and h["critical_path"] is False
    schema = h["payload_schema"]
    assert set(schema) == _PAYLOAD_KEYS
    assert {k for k, v in schema.items() if v["mandatory"]} == _MANDATORY
    assert schema["fen"]["max_length"] == _FEN_MAX
    assert schema["moves"]["max_items"] == _MOVES_MAX
    assert schema["moves"]["item_max_length"] == _MOVE_MAX
    assert tuple(schema["task"]["values"]) == _TASKS
    assert (schema["max-tokens"]["min"], schema["max-tokens"]["max"]) == (1, _TOKENS_MAX)
    assert h["never_receives"] == _PL["never_receives"]
    assert "hosted-byom-opt-in-only" in front["invariants"]
    co = yaml.safe_load((REPO / _CC["links"]["cloud_off_contract"]).read_text())["contract"]
    assert co["id"] == "privacy-cloud-off"
    assert co["identifiers"]["install_id"]["grammar"] == _ID["install_id"]["grammar"]
    sf = yaml.safe_load((REPO / _CC["links"]["sensitive_flag_contract"]).read_text())
    assert sf["contract"]["id"] == "privacy-sensitive-flag"
    assert sf["contract"]["identifiers"]["collection_id"]["grammar"] == _COL_RE.pattern


def _setter(path, value):
    def mutate(c):
        node = c
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _dropper(path):
    def mutate(c):
        node = c
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mutate


LINT_MUTATIONS = {
    "default_accepted": _setter(("semantics", "default"), "unknown-provider-reads-accepted"),
    "send_without_terms": _setter(("semantics", "send"), "allowed-when-cloud-allows"),
    "precedence_terms_first": _setter(
        ("semantics", "precedence"), "malformed-then-terms-then-cloud-then-payload"
    ),
    "terms_change_kept": _setter(("semantics", "terms_change"), "acceptance-survives-changes"),
    "cached_verdict": _setter(("semantics", "evaluation"), "verdict-cached-per-session"),
    "accept_without_approval": _setter(("semantics", "accept"), "accept-applies-immediately"),
    "revoke_needs_approval": _setter(("semantics", "revoke"), "revoke-needs-an-approval"),
    "rollback_history": _setter(("semantics", "rollback"), "pops-one-history-entry"),
    "reregister_resets": _setter(("semantics", "reregister"), "registering-resets-terms"),
    "saturation_wraps": _setter(("semantics", "saturation"), "revision-wraps-to-zero"),
    "keys_allowed": _setter(("semantics", "keys"), "provider-key-in-payload"),
    "composition_reowned": _setter(("semantics", "composition"), "cloud-mode-re-owned"),
    "extra_section": lambda c: c.__setitem__("notes", {"x": 1}),
    "error_added": lambda c: c["errors"]["closed_enum"].append("denied"),
    "mapping_swapped": _setter(("failures", "mapping", "terms_changed"), "conflict"),
    "open_failures": _setter(("failures", "closed"), False),
    "retryable_widened": _setter(
        ("errors", "shape", "retryable_true_only_for"), ["internal", "conflict"]
    ),
    "failure_class_dropped": lambda c: c["failures"]["classes"].remove("terms_changed"),
    "trigger_reworded": _setter(("failures", "triggers", "payload_rejected"), "bad-payload"),
    "record_field_dropped": lambda c: c["record"]["fields"].remove("accepted_terms"),
    "record_exact_int": _setter(("record", "exact"), 1),
    "terms_exact_int": _setter(("identifiers", "terms", "exact"), 1),
    "terms_field_dropped": lambda c: c["identifiers"]["terms"]["fields"].remove("region"),
    "retention_unbounded": _setter(
        ("identifiers", "terms", "retention_days"), "exact-built-in-int-0-or-more"
    ),
    "trains_truthy": _setter(("identifiers", "terms", "trains_on_inputs"), "truthy"),
    "terms_source_widened": _setter(("identifiers", "terms", "source"), "any-caller"),
    "provider_grammar_widened": _setter(("identifiers", "provider_id", "grammar"), "^prv1:.+$"),
    "accepted_caller_set": _setter(("identifiers", "accepted_terms", "source"), "caller-set"),
    "approval_binding_loosened": _setter(
        ("identifiers", "approval", "binding"), "sha256-over-install-id"
    ),
    "approval_source_widened": _setter(("identifiers", "approval", "source"), "any-caller"),
    "approval_preimage_order": _setter(
        ("identifiers", "approval", "preimage", "fields"),
        ["provider_id", "install_id", "terms_digest", "expected_revision"],
    ),
    "approval_preimage_sep": _setter(("identifiers", "approval", "preimage", "separator"), ":"),
    "approval_trailing_true": _setter(
        ("identifiers", "approval", "preimage", "trailing_separator"), True
    ),
    "approval_trailing_int": _setter(
        ("identifiers", "approval", "preimage", "trailing_separator"), 0
    ),
    "approval_digest_upper": _setter(("identifiers", "approval", "digest"), "uppercase-hex"),
    "terms_preimage_order": _setter(
        ("identifiers", "terms_digest", "preimage", "fields"),
        ["region", "retention_days", "trains_on_inputs"],
    ),
    "terms_preimage_bool": _setter(
        ("identifiers", "terms_digest", "preimage", "trains_on_inputs"), "ascii-1-or-0"
    ),
    "terms_trailing_int": _setter(
        ("identifiers", "terms_digest", "preimage", "trailing_separator"), 0
    ),
    "terms_preimage_dropped": _dropper(("identifiers", "terms_digest", "preimage")),
    "payload_key_added": lambda c: c["identifiers"]["payload"]["allowed_keys"].append("pgn"),
    "payload_mandatory_dropped": lambda c: c["identifiers"]["payload"]["mandatory_keys"].remove(
        "task"
    ),
    "payload_fen_widened": _setter(("identifiers", "payload", "fen"), "exact-built-in-str"),
    "payload_moves_widened": _setter(
        ("identifiers", "payload", "moves"), "exact-built-in-list-0-through-1024-items"
    ),
    "payload_tokens_widened": _setter(
        ("identifiers", "payload", "max-tokens"), "exact-built-in-int-1-through-8192"
    ),
    "never_receives_dropped": lambda c: c["identifiers"]["payload"]["never_receives"].remove(
        "sync-keys"
    ),
    "closed_payload_changed": _setter(("properties", "closed_payload"), "best-effort"),
    "base_path_changed": _setter(("versioning", "base_path"), "/privacy/byom/v2"),
    "link_changed": _setter(("links", "cloud_off_contract"), "data/contracts/cloud.yaml"),
    "link_dropped": _dropper(("links", "sensitive_flag_contract")),
    "role_not_scope_changed": _setter(("role", "not_scope"), "none"),
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


def test_register_defaults_to_no_accepted_terms():
    s, _, _ = _world()
    assert s.record(INS, P) == {
        "install_id": INS,
        "provider_id": P,
        "accepted_terms": None,
        "revision": 0,
    }
    assert set(s.record(INS, P)) == set(_FIELDS)


def test_send_without_accepted_terms_is_refused():
    s, _, _ = _world()
    _raises("terms_not_accepted", _send, s)
    _raises("terms_not_accepted", _send, s, _DEFAULT, _DEFAULT, A, INS, P2)  # unknown provider


def test_accept_with_bound_approval_opens_send():
    s, _, _ = _world()
    rec = _set(s, T, 0)
    assert rec["accepted_terms"] == terms_digest(T) and rec["revision"] == 1
    assert _send(s) == {"verdict": "allow", "terms_digest": terms_digest(T)}


def test_minimal_payload_is_allowed():
    s, _, _ = _world()
    _set(s, T, 0)
    for task in _TASKS:
        assert _send(s, _DEFAULT, {"fen": FEN, "task": task})["verdict"] == "allow"


def test_cloud_off_blocks_send_even_with_terms_accepted():
    s, _, _ = _world(cloud_on=False)
    _set(s, T, 0)
    _raises("cloud_mode_off", _send, s)


@pytest.mark.parametrize("cid", [B, "col1:" + "c" * 64], ids=["sensitive", "unknown"])
def test_sensitive_or_unknown_collection_blocks_send(cid):
    s, _, _ = _world()
    _set(s, T, 0)
    _raises("collection_sensitive", _send, s, _DEFAULT, _DEFAULT, cid)


def test_unknown_install_reads_cloud_off():
    s, _, _ = _world()
    s.register(INS2, P)
    _set(s, T, 0, INS2)
    _raises("cloud_mode_off", _send, s, _DEFAULT, _DEFAULT, A, INS2)


def test_precedence_malformed_payload_cloud_flag_terms():
    s, _, _ = _world(cloud_on=False)
    bad = {"fen": FEN, "task": "nope"}
    # malformed envelope beats a bad payload
    _raises("malformed_byom_request", _send, s, dict(T, region="EU"), bad)
    # a bad payload beats cloud off and missing terms
    _raises("payload_rejected", _send, s, _DEFAULT, bad)
    # cloud off beats a sensitive collection and missing terms
    _raises("cloud_mode_off", _send, s, _DEFAULT, _DEFAULT, B)
    s2, _, _ = _world()
    # a sensitive collection beats missing terms
    _raises("collection_sensitive", _send, s2, _DEFAULT, _DEFAULT, B)
    # a bad payload beats a sensitive collection
    _raises("payload_rejected", _send, s2, _DEFAULT, bad, B)


@pytest.mark.parametrize(
    "cid",
    ["col1:" + "a" * 64 + "\n", "col1:" + "A" * 64, "junk", None],
    ids=["newline", "upper", "junk", "none"],
)
def test_malformed_collection_precedes_terms_payload_and_cloud(cid):
    s, _, _ = _world(cloud_on=False)
    bad = {"fen": FEN, "task": "nope"}
    _raises("malformed_byom_request", _send, s, _DEFAULT, bad, cid)
    _raises("malformed_byom_request", _send, s, _DEFAULT, _DEFAULT, cid)


@pytest.mark.parametrize(
    "field,value",
    [
        ("retention_days", 31),
        ("retention_days", 29),
        ("retention_days", 0),
        ("region", "us"),
        ("trains_on_inputs", True),
    ],
)
def test_any_terms_change_lapses_acceptance(field, value):
    s, _, _ = _world()
    _set(s, T, 0)
    _raises("terms_changed", _send, s, dict(T, **{field: value}))
    assert _send(s)["verdict"] == "allow"  # identical terms again: allowed


def test_trains_true_to_false_also_lapses():
    s, _, _ = _world()
    t = dict(T, trains_on_inputs=True)
    _set(s, t, 0)
    _raises("terms_changed", _send, s, dict(T))
    assert _send(s, dict(t))["verdict"] == "allow"


def test_revoke_needs_no_approval_and_applies_before_the_next_send():
    s, _, _ = _world()
    _set(s, T, 0)
    rec = _set(s, None, 1)
    assert rec["accepted_terms"] is None and rec["revision"] == 2
    _raises("terms_not_accepted", _send, s)


def test_well_formed_approval_on_revoke_is_ignored():
    s, _, _ = _world()
    _set(s, T, 0)
    rec = _set(s, None, 1, approval="byt1:" + "e" * 64)
    assert rec["accepted_terms"] is None


@pytest.mark.parametrize(
    "token",
    ["junk", "BYT1:" + "e" * 64, "byt1:" + "e" * 63, 1, True],
    ids=["junk", "upper", "short", "int", "bool"],
)
def test_malformed_approval_on_revoke_is_malformed(token):
    s, _, _ = _world()
    _set(s, T, 0)
    before = s.record(INS, P)
    _raises("malformed_byom_request", _set, s, None, 1, INS, P, token)
    assert s.record(INS, P) == before


def test_same_value_is_noop():
    s, _, _ = _world()
    assert _set(s, None, 0)["revision"] == 0  # revoke with none accepted
    _set(s, T, 0)
    assert _set(s, dict(T), 1, approval=None)["revision"] == 1  # same digest, no approval
    assert s.record(INS, P)["accepted_terms"] == terms_digest(T)


def test_changing_accepted_terms_needs_an_approval_for_the_new_terms():
    s, _, _ = _world()
    _set(s, T, 0)
    old = byom_approval_for(INS, P, terms_digest(T), 1)
    _raises("approval_missing", _set, s, T2, 1, INS, P, old)
    _raises("approval_missing", _set, s, T2, 1, INS, P, None)
    assert _set(s, T2, 1)["accepted_terms"] == terms_digest(T2)
    _raises("terms_changed", _send, s)


@pytest.mark.parametrize(
    "tag",
    ["none", "other_install", "other_provider", "other_terms", "other_revision", "random"],
)
def test_accept_without_a_bound_approval_is_refused(tag):
    s, _, _ = _world()
    d = terms_digest(T)
    token = {
        "none": None,
        "other_install": byom_approval_for(INS2, P, d, 0),
        "other_provider": byom_approval_for(INS, P2, d, 0),
        "other_terms": byom_approval_for(INS, P, terms_digest(T2), 0),
        "other_revision": byom_approval_for(INS, P, d, 1),
        "random": "byt1:" + "e" * 64,
    }[tag]
    before = s.record(INS, P)
    _raises("approval_missing", _set, s, T, 0, INS, P, token)
    assert s.record(INS, P) == before
    _raises("terms_not_accepted", _send, s)


def test_an_approval_is_bound_to_its_provider_both_ways():
    s, _, _ = _world()
    s.register(INS, P2)
    d = terms_digest(T)
    _raises("approval_missing", _set, s, T, 0, INS, P2, byom_approval_for(INS, P, d, 0))
    _raises("approval_missing", _set, s, T, 0, INS, P, byom_approval_for(INS, P2, d, 0))
    assert _set(s, T, 0, INS, P2)["accepted_terms"] == d
    _raises("terms_not_accepted", _send, s)  # P still has none


def test_an_old_approval_is_never_reusable():
    s, _, _ = _world()
    old = byom_approval_for(INS, P, terms_digest(T), 0)
    _set(s, T, 0, approval=old)
    _set(s, None, 1)
    _raises("approval_missing", _set, s, T, 2, INS, P, old)
    assert _set(s, T, 2)["revision"] == 3


@pytest.mark.parametrize("rev", [1, 2, _MAX_REV])
def test_stale_revision_is_conflict(rev):
    s, _, _ = _world()
    before = s.record(INS, P)
    _raises("stale_revision", _set, s, T, rev)
    _raises("stale_revision", _set, s, None, rev)
    assert s.record(INS, P) == before


def test_stale_revision_edges_after_transitions():
    s, _, _ = _world()
    _set(s, T, 0)
    _set(s, None, 1)
    for rev in (0, 1, 3):
        _raises("stale_revision", _set, s, T, rev)
    assert _set(s, T, 2)["revision"] == 3


def test_unknown_provider_on_transition():
    s, _, _ = _world()
    _raises("unknown_provider", _set, s, T, 0, INS, P2)
    _raises("unknown_provider", _set, s, None, 0, INS2, P)
    assert (INS, P2) not in s._state and (INS2, P) not in s._state


def test_malformed_approval_precedes_unknown_and_stale():
    s, _, _ = _world()
    _raises("malformed_byom_request", _set, s, T, 0, INS, P2, "junk")
    _raises("malformed_byom_request", _set, s, T, 5, INS, P, "junk")


def test_malformed_terms_precede_unknown_and_stale():
    s, _, _ = _world()
    bad = dict(T, region="EU")
    _raises("malformed_byom_request", _set, s, bad, 0, INS, P2, None)
    _raises("malformed_byom_request", _set, s, bad, 5, INS, P, None)


def test_unknown_precedes_stale_and_stale_precedes_approval():
    s, _, _ = _world()
    _raises("unknown_provider", _set, s, T, 5, INS, P2, None)
    _raises("stale_revision", _set, s, T, 5, INS, P, None)
    _raises("approval_missing", _set, s, T, 0, INS, P, None)


@pytest.mark.parametrize("start", [None, "T2"], ids=["from_none", "from_other_terms"])
def test_accept_at_max_revision_fails_typed_state_unchanged(start):
    s, _, _ = _world()
    _at(s, _MAX_REV, None if start is None else terms_digest(T2))
    before = s.record(INS, P)
    _raises("revision_exhausted", _set, s, T, _MAX_REV)
    assert s.record(INS, P) == before


def test_revoke_at_max_revision_is_terminal_never_refused():
    s, _, _ = _world()
    _at(s, _MAX_REV, terms_digest(T))
    assert _send(s)["verdict"] == "allow"
    rec = _set(s, None, _MAX_REV)
    assert rec["accepted_terms"] is None and rec["revision"] == _MAX_REV
    _raises("terms_not_accepted", _send, s)
    # terminal: a later accept is exhausted, a repeat revoke is a no-op
    _raises("revision_exhausted", _set, s, T, _MAX_REV)
    assert s.record(INS, P) == rec
    assert _set(s, None, _MAX_REV) == rec
    _raises("terms_not_accepted", _send, s)


def test_revoke_just_below_max_revision_commits_to_max():
    s, _, _ = _world()
    _at(s, _MAX_REV - 1, terms_digest(T))
    rec = _set(s, None, _MAX_REV - 1)
    assert rec["accepted_terms"] is None and rec["revision"] == _MAX_REV


def test_change_just_below_max_revision_commits_to_max():
    s, _, _ = _world()
    _at(s, _MAX_REV - 1)
    assert _set(s, T, _MAX_REV - 1)["revision"] == _MAX_REV


def test_unapproved_accept_at_max_revision_is_approval_missing():
    s, _, _ = _world()
    _at(s, _MAX_REV)
    _raises("approval_missing", _set, s, T, _MAX_REV, INS, P, None)


def test_same_value_at_max_revision_is_a_noop():
    s, _, _ = _world()
    _at(s, _MAX_REV, terms_digest(T))
    assert _set(s, T, _MAX_REV, approval=None)["revision"] == _MAX_REV
    _at(s, _MAX_REV)
    assert _set(s, None, _MAX_REV)["revision"] == _MAX_REV


@pytest.mark.parametrize("rev", [0, 1, _MAX_REV - 1, _MAX_REV])
def test_revision_edges_are_well_formed(rev):
    s, _, _ = _world()
    _at(s, rev)
    assert s.record(INS, P)["revision"] == rev
    _raises("stale_revision", _set, s, None, 0 if rev else 1, INS, P, None)


def test_send_verdict_is_read_at_send_time_never_cached():
    s, cloud, _ = _world()
    _set(s, T, 0)
    assert _send(s)["verdict"] == "allow"
    cloud.transition(
        {"install_id": INS, "cloud_mode": "off", "expected_revision": 1, "opt_in": None}
    )
    _raises("cloud_mode_off", _send, s)
    cloud.transition(
        {
            "install_id": INS,
            "cloud_mode": "on",
            "expected_revision": 2,
            "opt_in": opt_in_for(INS, 2),
        }
    )
    assert _send(s)["verdict"] == "allow"
    _set(s, None, 1)
    _raises("terms_not_accepted", _send, s)


def test_reregister_never_resets_terms_or_revision():
    s, _, _ = _world()
    _set(s, T, 0)
    before = s.record(INS, P)
    assert s.register(INS, P) == before


def test_undoing_accept_is_revoke_and_undoing_revoke_needs_a_fresh_approval():
    s, _, _ = _world()
    first = byom_approval_for(INS, P, terms_digest(T), 0)
    _set(s, T, 0, approval=first)
    _set(s, None, 1)
    _raises("approval_missing", _set, s, T, 2, INS, P, first)
    assert _set(s, T, 2)["accepted_terms"] == terms_digest(T)


def test_no_record_or_verdict_carries_a_key():
    s, _, _ = _world()
    _set(s, T, 0)
    assert set(s.record(INS, P)) == {"install_id", "provider_id", "accepted_terms", "revision"}
    assert set(_send(s)) == {"verdict", "terms_digest"}


# ---- payload schema boundaries --------------------------------------------


def _pl(**over):
    p = dict(PAYLOAD)
    for k, v in over.items():
        k = k.replace("_", "-")
        if v is _DROP:
            del p[k]
        else:
            p[k] = v
    return p


_DROP = object()

PAYLOAD_OK = {
    "fen_len_1": _pl(fen="k"),
    "fen_len_128": _pl(fen="k" * 128),
    "fen_space_0x20": _pl(fen="k k"),
    "fen_tilde_0x7e": _pl(fen="k~"),
    "moves_empty": _pl(moves=[]),
    "moves_512": _pl(moves=["e4"] * 512),
    "move_len_1": _pl(moves=["e"]),
    "move_len_16": _pl(moves=["e" * 16]),
    "move_bang_0x21": _pl(moves=["!"]),
    "move_tilde_0x7e": _pl(moves=["~"]),
    "no_moves": _pl(moves=_DROP),
    "no_max_tokens": _pl(max_tokens=_DROP),
    "tokens_1": _pl(max_tokens=1),
    "tokens_4096": _pl(max_tokens=4096),
}


@pytest.mark.parametrize("name", sorted(PAYLOAD_OK))
def test_payload_boundary_accepted(name):
    s, _, _ = _world()
    _set(s, T, 0)
    assert _send(s, _DEFAULT, PAYLOAD_OK[name])["verdict"] == "allow"


PAYLOAD_BAD = {
    "fen_empty": _pl(fen=""),
    "fen_len_129": _pl(fen="k" * 129),
    "fen_0x1f": _pl(fen="k\x1f"),
    "fen_0x7f": _pl(fen="k\x7f"),
    "fen_newline": _pl(fen=FEN + "\n"),
    "fen_non_ascii": _pl(fen="k\u00e9"),
    "fen_none": _pl(fen=None),
    "fen_bytes": _pl(fen=FEN.encode()),
    "missing_fen": _pl(fen=_DROP),
    "missing_task": _pl(task=_DROP),
    "moves_513": _pl(moves=["e4"] * 513),
    "moves_tuple": _pl(moves=("e4",)),
    "moves_str": _pl(moves="e4"),
    "moves_none": _pl(moves=None),
    "move_empty": _pl(moves=[""]),
    "move_len_17": _pl(moves=["e" * 17]),
    "move_space": _pl(moves=["e 4"]),
    "move_0x7f": _pl(moves=["e\x7f"]),
    "move_int": _pl(moves=[4]),
    "move_none": _pl(moves=[None]),
    "move_nested_list": _pl(moves=[["e4"]]),
    "task_unknown": _pl(task="summarise"),
    "task_upper": _pl(task="Delta-explanation"),
    "task_space": _pl(task="weekly-plan "),
    "task_none": _pl(task=None),
    "tokens_0": _pl(max_tokens=0),
    "tokens_4097": _pl(max_tokens=4097),
    "tokens_neg": _pl(max_tokens=-1),
    "tokens_true": _pl(max_tokens=True),
    "tokens_float": _pl(max_tokens=1.0),
    "tokens_str": _pl(max_tokens="1"),
    "tokens_none": _pl(max_tokens=None),
    "unknown_key": dict(PAYLOAD, pgn="1. e4"),
    "underscore_tokens": {
        **{k: v for k, v in PAYLOAD.items() if k != "max-tokens"},
        "max_tokens": 5,
    },
    "account_keys": dict(PAYLOAD, **{"account-keys": "x"}),
    "full_corpus": dict(PAYLOAD, **{"full-corpus": "x"}),
    "sync_keys": dict(PAYLOAD, **{"sync-keys": "x"}),
    "empty": {},
    "list": list(PAYLOAD.items()),
    "str": "fen",
    "none": None,
}


@pytest.mark.parametrize("name", sorted(PAYLOAD_BAD))
def test_payload_outside_the_schema_is_rejected(name):
    s, cloud, flags = _world()
    _set(s, T, 0)
    before = _all_state(s, cloud, flags)
    payload = PAYLOAD_BAD[name]
    snap = copy.deepcopy(payload)
    _raises("payload_rejected", _send, s, _DEFAULT, payload)
    assert _all_state(s, cloud, flags) == before
    assert payload == snap and type(payload) is type(snap)


# ---- terms boundaries -----------------------------------------------------


@pytest.mark.parametrize(
    "terms",
    [
        dict(T, retention_days=0),
        dict(T, retention_days=3650),
        dict(T, region="zz"),
        dict(T, trains_on_inputs=True),
    ],
    ids=["days_0", "days_3650", "region_zz", "trains_true"],
)
def test_terms_boundary_accepted(terms):
    s, _, _ = _world()
    _set(s, terms, 0)
    assert _send(s, dict(terms))["verdict"] == "allow"


# ---- preimages: byte-exact, from the contract text ------------------------

_TPRE = _ID["terms_digest"]["preimage"]
_APRE = _ID["approval"]["preimage"]

# sha256 of the literal bytes "30|eu|false" and of
# "ins1:" + "1" * 64 + "|prv1:acme|" + <that trm1 digest> + "|0", computed
# outside the reference; split so no long hex literal sits on one line
_TERMS_KAT = "5721c15e8db6ce817329de7dd15e59e6" + "7dfab94d3d07731e5506a20f190f2002"
_APPROVAL_KAT = "98c2639bd1b2a61258e6f14e3af36b3a" + "e964424c9290262033a0217bc9824e26"


def _terms_bytes(terms):
    """The terms preimage built only from the contract text."""
    assert _TPRE["fields"] == ["retention_days", "region", "trains_on_inputs"]
    assert _TPRE["trailing_separator"] is False
    enc = _TPRE["encoding"]
    trains = {True: "true", False: "false"}[terms["trains_on_inputs"]]
    parts = [str(terms["retention_days"]).encode("ascii"), terms["region"].encode(enc)]
    return _TPRE["separator"].encode(enc).join([*parts, trains.encode("ascii")])


def _approval_bytes(iid, pid, digest, rev):
    """The approval preimage built only from the contract text."""
    assert _APRE["fields"] == ["install_id", "provider_id", "terms_digest", "expected_revision"]
    assert _APRE["trailing_separator"] is False
    enc = _APRE["encoding"]
    parts = [iid.encode(enc), pid.encode(enc), digest.encode(enc), str(rev).encode("ascii")]
    return _APRE["separator"].encode(enc).join(parts)


def test_terms_preimage_known_answer():
    raw = b"30|eu|false"
    assert _terms_bytes(T) == raw
    assert hashlib.sha256(raw).hexdigest() == _TERMS_KAT
    assert terms_digest(T) == "trm1:" + _TERMS_KAT
    assert _TPRE["example"] == "30|eu|false"


def test_approval_preimage_known_answer():
    raw = ("ins1:" + "1" * 64 + "|prv1:acme|trm1:" + _TERMS_KAT + "|0").encode("ascii")
    assert _approval_bytes(INS, P, "trm1:" + _TERMS_KAT, 0) == raw
    assert hashlib.sha256(raw).hexdigest() == _APPROVAL_KAT
    assert byom_approval_for(INS, P, terms_digest(T), 0) == "byt1:" + _APPROVAL_KAT


@pytest.mark.parametrize("rev", [0, 1, 9, 10, 100, _MAX_REV - 1])
def test_approval_matches_the_contract_preimage(rev):
    d = "trm1:" + hashlib.sha256(_terms_bytes(T)).hexdigest()
    assert terms_digest(T) == d
    want = "byt1:" + hashlib.sha256(_approval_bytes(INS, P, d, rev)).hexdigest()
    s, _, _ = _world()
    _at(s, rev)
    assert _set(s, T, rev, INS, P, want)["accepted_terms"] == d


@pytest.mark.parametrize(
    "terms", [dict(T, retention_days=7, trains_on_inputs=True), dict(T2)], ids=["t7", "t2"]
)
def test_terms_digest_matches_the_contract_preimage(terms):
    assert terms_digest(terms) == "trm1:" + hashlib.sha256(_terms_bytes(terms)).hexdigest()


def _approval_variant(tag, iid, pid, d, rev):
    return {
        "colon_sep": f"{iid}:{pid}:{d}:{rev}",
        "no_sep": f"{iid}{pid}{d}{rev}",
        "trailing_sep": f"{iid}|{pid}|{d}|{rev}|",
        "leading_sep": f"|{iid}|{pid}|{d}|{rev}",
        "install_provider_swapped": f"{pid}|{iid}|{d}|{rev}",
        "revision_first": f"{rev}|{iid}|{pid}|{d}",
        "rev_padded": f"{iid}|{pid}|{d}|{rev:03d}",
        "rev_signed": f"{iid}|{pid}|{d}|+{rev}",
        "digest_unprefixed": f"{iid}|{pid}|{d[5:]}|{rev}",
        "provider_unprefixed": f"{iid}|{pid[5:]}|{d}|{rev}",
    }[tag]


_APPROVAL_VARIANTS = [
    "colon_sep",
    "no_sep",
    "trailing_sep",
    "leading_sep",
    "install_provider_swapped",
    "revision_first",
    "rev_padded",
    "rev_signed",
    "digest_unprefixed",
    "provider_unprefixed",
]


@pytest.mark.parametrize("tag", _APPROVAL_VARIANTS)
@pytest.mark.parametrize("rev", [0, 7])
def test_approval_from_another_preimage_is_refused(tag, rev):
    s, _, _ = _world()
    _at(s, rev)
    d = terms_digest(T)
    wrong = "byt1:" + hashlib.sha256(_approval_variant(tag, INS, P, d, rev).encode()).hexdigest()
    before = s.record(INS, P)
    _raises("approval_missing", _set, s, T, rev, INS, P, wrong)
    assert s.record(INS, P) == before


def test_approval_utf16_preimage_is_refused():
    s, _, _ = _world()
    raw = f"{INS}|{P}|{terms_digest(T)}|0".encode("utf-16")
    _raises("approval_missing", _set, s, T, 0, INS, P, "byt1:" + hashlib.sha256(raw).hexdigest())


_TERMS_VARIANTS = {
    "colon_sep": "30:eu:false",
    "trailing_sep": "30|eu|false|",
    "order_swapped": "eu|30|false",
    "bool_capital": "30|eu|False",
    "bool_digit": "30|eu|0",
    "days_padded": "030|eu|false",
    "region_upper": "30|EU|false",
}


@pytest.mark.parametrize("tag", sorted(_TERMS_VARIANTS))
def test_approval_over_another_terms_preimage_is_refused(tag):
    s, _, _ = _world()
    d = "trm1:" + hashlib.sha256(_TERMS_VARIANTS[tag].encode()).hexdigest()
    assert d != terms_digest(T)
    _raises("approval_missing", _set, s, T, 0, INS, P, byom_approval_for(INS, P, d, 0))


# ---- rollback -------------------------------------------------------------


@pytest.mark.parametrize(
    "tag", ["unapproved", "stale", "unknown", "exhausted", "malformed_terms", "malformed_approval"]
)
def test_rejected_transition_leaves_everything_unchanged(tag):
    s, cloud, flags = _world()
    _set(s, T, 0)
    if tag == "exhausted":
        _at(s, _MAX_REV, terms_digest(T))
    before = _all_state(s, cloud, flags)
    args = {
        "unapproved": (T2, 1, INS, P, None),
        "stale": (T2, 0, INS, P, None),
        "unknown": (T2, 0, INS, P2, None),
        "exhausted": (T2, _MAX_REV, INS, P, byom_approval_for(INS, P, terms_digest(T2), _MAX_REV)),
        "malformed_terms": (dict(T2, region="US"), 1, INS, P, None),
        "malformed_approval": (T2, 1, INS, P, "junk"),
    }[tag]
    snap = copy.deepcopy(args)
    with pytest.raises(ByomError):
        _set(s, *args)
    assert _all_state(s, cloud, flags) == before and args == snap


@pytest.mark.parametrize(
    "cloud_on,cid,accepted", [(False, A, True), (True, B, True), (True, A, False)]
)
def test_refused_send_changes_nothing(cloud_on, cid, accepted):
    s, cloud, flags = _world(cloud_on=cloud_on)
    if accepted:
        _set(s, T, 0)
    before = _all_state(s, cloud, flags)
    with pytest.raises(ByomError):
        _send(s, _DEFAULT, _DEFAULT, cid)
    assert _all_state(s, cloud, flags) == before


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
    if op == "send":
        return {
            "install_id": INS,
            "provider_id": P,
            "collection_id": A,
            "terms": dict(T),
            "payload": dict(PAYLOAD),
        }
    return {
        "install_id": INS,
        "provider_id": P,
        "terms": dict(T),
        "expected_revision": 1,
        "approval": byom_approval_for(INS, P, terms_digest(T2), 1),
    }


def _key_forms(base, op, prefix, rows, wrap):
    """Container, value-type and key-form rows for one mapping boundary.
    WRAP places a mutated mapping back into a full request."""
    rows.append((op, f"{prefix}dict_sub", wrap(DictSub(base))))
    rows.append((op, f"{prefix}list", wrap(list(base.items()))))
    rows.append((op, f"{prefix}str", wrap(next(iter(base)))))
    rows.append((op, f"{prefix}none", wrap(None)))
    for key in base:
        r = dict(base)
        if type(r[key]) is str:
            r[key] = StrSub(r[key])
        elif type(r[key]) is int:
            r[key] = IntSub(r[key])
        elif type(r[key]) is bool:
            r[key] = int(r[key])
        elif type(r[key]) is dict:
            r[key] = DictSub(r[key])
        elif type(r[key]) is list:
            r[key] = ListSub(r[key])
        rows.append((op, f"{prefix}{key}_sub", wrap(r)))
        r = dict(base)
        r[key] = ListSub([r[key]])
        rows.append((op, f"{prefix}{key}_listsub", wrap(r)))
        r = dict(base)
        r[key] = DictSub({"v": r[key]})
        rows.append((op, f"{prefix}{key}_dictsub", wrap(r)))
        r = dict(base)
        del r[key]
        rows.append((op, f"{prefix}missing_{key}", wrap(r)))
        r = dict(base)
        r[StrSub(key)] = r.pop(key)  # real key replaced by a str subclass
        rows.append((op, f"{prefix}real_key_strsub_{key}", wrap(r)))
        r = dict(base)
        r[key + "_x"] = r.pop(key)  # same arity, one key renamed
        rows.append((op, f"{prefix}renamed_key_{key}", wrap(r)))
    for form, cls in (("plain", str), ("eq_raises", EqRaises), ("hash_collide", HashCollide)):
        r = dict(base)
        r[cls("extra")] = 1
        rows.append((op, f"{prefix}extra_key_{form}", wrap(r)))
    first = next(iter(base))
    r = dict(base)
    r[first.encode()] = r.pop(first)
    rows.append((op, f"{prefix}bytes_key", wrap(r)))
    r = dict(base)
    r[1] = r.pop(first)
    rows.append((op, f"{prefix}int_key", wrap(r)))


def _envelope_rows():
    rows = []
    for op in ("send", "transition"):
        base = _base(op)
        _key_forms(base, op, "", rows, lambda r: r)
        for iid in (
            "ins1:" + "A" * 64,
            "ins1:" + "1" * 63,
            "ins1:" + "1" * 65,
            "ins1:" + "1" * 64 + "\n",
            "INS1:" + "1" * 64,
        ):
            rows.append((op, f"iid_{len(iid)}_{iid[:4]}_{iid[-1]!r}", dict(base, install_id=iid)))
        for tag, pid in (
            ("empty", "prv1:"),
            ("len33", "prv1:" + "a" * 33),
            ("upper", "prv1:ACME"),
            ("hyphen", "prv1:ac-me"),
            ("newline", "prv1:acme\n"),
            ("prefix", "PRV1:acme"),
            ("none", None),
        ):
            rows.append((op, f"pid_{tag}", dict(base, provider_id=pid)))
        tb = dict(T)

        def put_terms(t, base=base):
            return dict(base, terms=t)

        # terms None is revoke on transition; only the send rows cover it
        _key_forms(tb, op, "terms_", rows, put_terms)
        if op == "transition":
            rows[:] = [row for row in rows if row[1] != "terms_none" or row[0] != op]
        for tag, value in (
            ("days_neg", -1),
            ("days_3651", 3651),
            ("days_true", True),
            ("days_float", 30.0),
            ("days_str", "30"),
            ("region_upper", "EU"),
            ("region_one", "e"),
            ("region_three", "eur"),
            ("region_digit", "e1"),
            ("region_newline", "eu\n"),
            ("region_empty", ""),
            ("trains_none", None),
            ("trains_str", "false"),
            ("trains_0", 0),
        ):
            field = {"days": "retention_days", "region": "region", "trains": "trains_on_inputs"}[
                tag.split("_")[0]
            ]
            rows.append((op, f"terms_{tag}", put_terms(dict(tb, **{field: value}))))
    s = _base("send")
    for cid in ("col1:" + "A" * 64, "col1:" + "a" * 63, "col1:" + "a" * 64 + "\n", None):
        rows.append(("send", f"cid_{cid!r:.12}_{len(cid or '')}", dict(s, collection_id=cid)))
    t = _base("transition")
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
    good = t["approval"]
    for tag, token in (
        ("upper", "BYT1:" + "a" * 64),
        ("newline", good + "\n"),
        ("hex63", good[:-1]),
        ("hex65", good + "a"),
        ("opt_prefix", "opt1:" + good[5:]),
        ("strsub", StrSub(good)),
        ("bytes", good.encode()),
        ("false", False),
    ):
        rows.append(("transition", f"approval_{tag}", dict(t, approval=token)))
    return rows


# payload value rows belong to the payload boundary (payload_rejected), below
ROWS = [
    r for r in _envelope_rows() if r[1] not in {"payload_sub", "payload_listsub", "payload_dictsub"}
]


def _payload_rows():
    rows = []
    base = dict(PAYLOAD)

    def put(p):
        return p

    _key_forms(base, "send", "", rows, put)
    # moves and max-tokens are optional: their missing rows are accepted
    # (PAYLOAD_OK no_moves / no_max_tokens), never rejected
    optional = {"missing_moves", "missing_max-tokens"}
    rows = [(n, p) for _, n, p in rows if n not in optional]
    rows.append(("envelope_payload_dictsub", DictSub(base)))
    rows.append(("moves_item_strsub", dict(base, moves=[StrSub("e4")])))
    rows.append(("moves_item_eq_raises", dict(base, moves=[EqRaises("e4")])))
    rows.append(("task_eq_raises", dict(base, task=EqRaises("weekly-plan"))))
    rows.append(("fen_eq_raises", dict(base, fen=EqRaises(FEN))))
    rows.append(("tokens_bool_false", dict(base, **{"max-tokens": False})))
    return rows


PAYLOAD_ROWS = _payload_rows()


def _snap(req):
    if isinstance(req, dict):
        return [(k, _snap(v)) for k, v in dict.items(req)]
    if isinstance(req, list):
        return (type(req), [_snap(v) for v in list.__iter__(req)])
    return (type(req), req if not isinstance(req, str) else str.__str__(req))


@pytest.mark.parametrize("op,name,req", ROWS, ids=[f"{o}-{n}" for o, n, _ in ROWS])
def test_hostile_request_fails_closed_typed(op, name, req):
    s, cloud, flags = _world()
    _set(s, T, 0)
    before = _all_state(s, cloud, flags)
    snap = _snap(req)
    CALLS.clear()
    _raises("malformed_byom_request", getattr(s, op), req)
    assert CALLS == []
    assert _all_state(s, cloud, flags) == before
    assert _snap(req) == snap


@pytest.mark.parametrize("name,payload", PAYLOAD_ROWS, ids=[n for n, _ in PAYLOAD_ROWS])
def test_hostile_payload_is_rejected_typed(name, payload):
    s, cloud, flags = _world()
    _set(s, T, 0)
    before = _all_state(s, cloud, flags)
    snap = _snap(payload)
    CALLS.clear()
    _raises("payload_rejected", _send, s, _DEFAULT, payload)
    assert CALLS == []
    assert _all_state(s, cloud, flags) == before
    assert _snap(payload) == snap


@pytest.mark.parametrize(
    "iid,pid",
    [
        (StrSub(INS), P),
        (INS, StrSub(P)),
        (INS.upper(), P),
        (INS, "prv1:ACME"),
        (None, P),
        (INS, None),
        ([INS], P),
        (INS, b"prv1:acme"),
        (INS + "\n", P),
        (INS, P + "\n"),
    ],
    ids=[
        "iid_strsub",
        "pid_strsub",
        "iid_upper",
        "pid_upper",
        "iid_none",
        "pid_none",
        "list",
        "bytes",
        "iid_newline",
        "pid_newline",
    ],
)
def test_register_hostile_ids(iid, pid):
    s = ByomStore(CloudSwitch(FlagStore()))
    CALLS.clear()
    _raises("malformed_byom_request", s.register, iid, pid)
    assert s._state == {} and CALLS == []


# ---- executable kill check: one-edit mutants of the reference engine ------
# Each edit is applied to THIS file's source; the mutated copy's battery
# (minus this section) must go red. The identity edit must stay green.

MUTANTS = {
    "req_isinstance": (
        '    if type(req) is not dict:\n        _fail("malformed_byom_request")',
        '    if not isinstance(req, dict):\n        _fail("malformed_byom_request")',
    ),
    "key_type_skip": (
        (
            "    for k in list(req.keys()):\n"
            "        if type(k) is not str:\n"
            '            _fail("malformed_byom_request")\n'
        ),
        "",
    ),
    "keys_subset": (
        "if set(req) != keys:",
        "if not keys <= set(req):",
    ),
    "keys_same_arity": (
        "if set(req) != keys:",
        "if len(req) != len(keys):",
    ),
    "iid_search": (
        "if not _exact_str(iid) or _INS_RE.fullmatch(iid) is None:",
        "if not _exact_str(iid) or _INS_RE.search(iid) is None:",
    ),
    "pid_search": (
        "if not _exact_str(pid) or _PRV_RE.fullmatch(pid) is None:",
        "if not _exact_str(pid) or _PRV_RE.search(pid) is None:",
    ),
    "pid_unchecked": (
        (
            '    pid = req["provider_id"]\n'
            "    if not _exact_str(pid) or _PRV_RE.fullmatch(pid) is None:\n"
            '        _fail("malformed_byom_request")\n'
        ),
        '    pid = req["provider_id"]\n',
    ),
    "str_isinstance": (
        "    return type(v) is str\n",
        "    return isinstance(v, str)\n",
    ),
    "terms_isinstance": (
        '    if type(t) is not dict:\n        _fail("malformed_byom_request")',
        '    if not isinstance(t, dict):\n        _fail("malformed_byom_request")',
    ),
    "terms_key_type_skip": (
        (
            "    for k in list(t.keys()):\n"
            "        if type(k) is not str:\n"
            '            _fail("malformed_byom_request")\n'
        ),
        "",
    ),
    "terms_keys_subset": (
        "    if set(t) != _TERMS_KEYS:",
        "    if not _TERMS_KEYS <= set(t):",
    ),
    "days_isinstance": (
        "    if type(days) is not int or not 0 <= days <= _MAX_DAYS:",
        "    if not isinstance(days, int) or not 0 <= days <= _MAX_DAYS:",
    ),
    "days_upper_edge": (
        "not 0 <= days <= _MAX_DAYS",
        "not 0 <= days < _MAX_DAYS",
    ),
    "days_lower_edge": (
        "not 0 <= days <= _MAX_DAYS",
        "not 1 <= days <= _MAX_DAYS",
    ),
    "days_unbounded": (
        "not 0 <= days <= _MAX_DAYS",
        "not 0 <= days",
    ),
    "region_search": (
        "_REGION_RE.fullmatch(region) is None",
        "_REGION_RE.search(region) is None",
    ),
    "trains_truthy": (
        '    if type(t["trains_on_inputs"]) is not bool:',
        '    if t["trains_on_inputs"] not in (True, False):',
    ),
    "digest_drops_trains": (
        "    raw = f\"{terms['retention_days']}|{terms['region']}|{trains}\".encode()",
        "    raw = f\"{terms['retention_days']}|{terms['region']}\".encode()",
    ),
    "digest_sep": (
        "    raw = f\"{terms['retention_days']}|{terms['region']}|{trains}\".encode()",
        "    raw = f\"{terms['retention_days']}:{terms['region']}:{trains}\".encode()",
    ),
    "digest_bool_render": (
        '    trains = "true" if terms["trains_on_inputs"] else "false"',
        '    trains = str(terms["trains_on_inputs"])',
    ),
    "payload_isinstance": (
        '    if type(p) is not dict:\n        _fail("payload_rejected")',
        '    if not isinstance(p, dict):\n        _fail("payload_rejected")',
    ),
    "payload_key_type_skip": (
        (
            "    for k in list(p.keys()):\n"
            "        if type(k) is not str:\n"
            '            _fail("payload_rejected")\n'
        ),
        "",
    ),
    "payload_extra_keys_ok": (
        "    if not keys <= _PAYLOAD_KEYS or not keys >= _MANDATORY:",
        "    if not keys >= _MANDATORY:",
    ),
    "payload_mandatory_skip": (
        "    if not keys <= _PAYLOAD_KEYS or not keys >= _MANDATORY:",
        "    if not keys <= _PAYLOAD_KEYS:",
    ),
    "fen_empty_ok": (
        "not 1 <= len(fen) <= _FEN_MAX",
        "not 0 <= len(fen) <= _FEN_MAX",
    ),
    "fen_upper_edge": (
        "not 1 <= len(fen) <= _FEN_MAX",
        "not 1 <= len(fen) < _FEN_MAX",
    ),
    "fen_any_chars": (
        "or not _printable(fen, 0x20)",
        "",
    ),
    "fen_space_banned": (
        "or not _printable(fen, 0x20)",
        "or not _printable(fen, 0x21)",
    ),
    "printable_top": (
        "    return all(lo <= ord(c) <= 0x7E for c in s)",
        "    return all(lo <= ord(c) <= 0x7F for c in s)",
    ),
    "printable_top_low": (
        "    return all(lo <= ord(c) <= 0x7E for c in s)",
        "    return all(lo <= ord(c) < 0x7E for c in s)",
    ),
    "moves_isinstance": (
        "        if type(moves) is not list or len(moves) > _MOVES_MAX:",
        "        if not isinstance(moves, (list, tuple)) or len(moves) > _MOVES_MAX:",
    ),
    "moves_upper_edge": (
        "len(moves) > _MOVES_MAX",
        "len(moves) >= _MOVES_MAX",
    ),
    "moves_unbounded": (
        "        if type(moves) is not list or len(moves) > _MOVES_MAX:",
        "        if type(moves) is not list:",
    ),
    "move_isinstance": (
        "            if not _exact_str(m) or not 1 <= len(m) <= _MOVE_MAX",
        "            if not isinstance(m, str) or not 1 <= len(m) <= _MOVE_MAX",
    ),
    "move_empty_ok": (
        "not 1 <= len(m) <= _MOVE_MAX",
        "not 0 <= len(m) <= _MOVE_MAX",
    ),
    "move_upper_edge": (
        "not 1 <= len(m) <= _MOVE_MAX",
        "not 1 <= len(m) < _MOVE_MAX",
    ),
    "move_space_ok": (
        "or not _printable(m, 0x21)",
        "or not _printable(m, 0x20)",
    ),
    "task_any": (
        "    if not _exact_str(task) or task not in _TASKS:",
        "    if not _exact_str(task):",
    ),
    "tokens_isinstance": (
        "        if type(n) is not int or not 1 <= n <= _TOKENS_MAX:",
        "        if not isinstance(n, int) or not 1 <= n <= _TOKENS_MAX:",
    ),
    "tokens_lower_edge": (
        "not 1 <= n <= _TOKENS_MAX",
        "not 0 <= n <= _TOKENS_MAX",
    ),
    "tokens_upper_edge": (
        "not 1 <= n <= _TOKENS_MAX",
        "not 1 <= n < _TOKENS_MAX",
    ),
    "payload_after_cloud": (
        (
            '        _payload(req["payload"])\n'
            "        refusal = self._cloud_refusal(iid, cid)\n"
            "        if refusal is not None:\n"
            "            _fail(refusal)\n"
        ),
        (
            "        refusal = self._cloud_refusal(iid, cid)\n"
            "        if refusal is not None:\n"
            "            _fail(refusal)\n"
            '        _payload(req["payload"])\n'
        ),
    ),
    "payload_before_terms": (
        '        digest = _terms(req["terms"])\n        _payload(req["payload"])\n',
        '        _payload(req["payload"])\n        digest = _terms(req["terms"])\n',
    ),
    "cloud_ignored": (
        "        if refusal is not None:\n            _fail(refusal)\n",
        "",
    ),
    "terms_before_cloud": (
        (
            "        refusal = self._cloud_refusal(iid, cid)\n"
            "        if refusal is not None:\n"
            "            _fail(refusal)\n"
            "        accepted = self.accepted(iid, pid)\n"
            "        if accepted is None:\n"
            '            _fail("terms_not_accepted")\n'
        ),
        (
            "        accepted = self.accepted(iid, pid)\n"
            "        if accepted is None:\n"
            '            _fail("terms_not_accepted")\n'
            "        refusal = self._cloud_refusal(iid, cid)\n"
            "        if refusal is not None:\n"
            "            _fail(refusal)\n"
        ),
    ),
    "cloud_refusal_collapsed": (
        '            return cls if cls in _CLOUD_REFUSALS else "malformed_byom_request"',
        '            return "cloud_mode_off"',
    ),
    "cid_unchecked": (
        (
            '        cid = req["collection_id"]\n'
            "        if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:\n"
            '            _fail("malformed_byom_request")\n'
        ),
        '        cid = req["collection_id"]\n',
    ),
    "cid_search": (
        "_COL_RE.fullmatch(cid) is None",
        "_COL_RE.search(cid) is None",
    ),
    "terms_change_ignored": (
        '        if accepted != digest:\n            _fail("terms_changed")\n',
        "",
    ),
    "changed_as_not_accepted": (
        '            _fail("terms_changed")',
        '            _fail("terms_not_accepted")',
    ),
    "default_accepted": (
        "        return None if state is None else state[0]",
        '        return "trm1:" + "0" * 64 if state is None else state[0]',
    ),
    "approval_none_ok": (
        "    if token is None:\n        return False",
        "    if token is None:\n        return True",
    ),
    "approval_match": (
        "_BYT_RE.fullmatch(token) is None",
        "_BYT_RE.match(token) is None",
    ),
    "approval_unbound": (
        '    return token == byom_approval_for(iid, pid, target, req["expected_revision"])',
        "    return True",
    ),
    "approval_provider_ignored": (
        '    return token == byom_approval_for(iid, pid, target, req["expected_revision"])',
        '    return token == byom_approval_for(iid, P, target, req["expected_revision"])',
    ),
    "approval_install_ignored": (
        '    return token == byom_approval_for(iid, pid, target, req["expected_revision"])',
        '    return token == byom_approval_for(INS, pid, target, req["expected_revision"])',
    ),
    "approval_revision_ignored": (
        '    return token == byom_approval_for(iid, pid, target, req["expected_revision"])',
        "    return token == byom_approval_for(iid, pid, target, 0)",
    ),
    "approval_terms_ignored": (
        '    return token == byom_approval_for(iid, pid, target, req["expected_revision"])',
        '    return token in (byom_approval_for(iid, pid, target, req["expected_revision"]), byom_approval_for(iid, pid, terms_digest(T), req["expected_revision"]))',  # noqa: E501
    ),
    "approval_sep": (
        '    raw = f"{install_id}|{provider_id}|{digest}|{expected_revision}".encode()',
        '    raw = f"{install_id}:{provider_id}:{digest}:{expected_revision}".encode()',
    ),
    "approval_trailing_sep": (
        '    raw = f"{install_id}|{provider_id}|{digest}|{expected_revision}".encode()',
        '    raw = f"{install_id}|{provider_id}|{digest}|{expected_revision}|".encode()',
    ),
    "approval_order": (
        '    raw = f"{install_id}|{provider_id}|{digest}|{expected_revision}".encode()',
        '    raw = f"{provider_id}|{install_id}|{digest}|{expected_revision}".encode()',
    ),
    "approval_rev_padded": (
        '    raw = f"{install_id}|{provider_id}|{digest}|{expected_revision}".encode()',
        '    raw = f"{install_id}|{provider_id}|{digest}|{expected_revision:03d}".encode()',
    ),
    "approval_parsed_only_on_accept": (
        "        approved = _approval_ok(req, iid, pid, target)\n",
        "        approved = _approval_ok(req, iid, pid, target) if target is not None else False\n",
    ),
    "approval_after_unknown": (
        (
            "        approved = _approval_ok(req, iid, pid, target)\n"
            "        if (iid, pid) not in self._state:\n"
            '            _fail("unknown_provider")\n'
        ),
        (
            "        if (iid, pid) not in self._state:\n"
            '            _fail("unknown_provider")\n'
            "        approved = _approval_ok(req, iid, pid, target)\n"
        ),
    ),
    "revoke_needs_approval": (
        "        if target is not None and not approved:",
        "        if not approved:",
    ),
    "no_same_value_noop": (
        "        if target == accepted:\n            return self.record(iid, pid)\n",
        "",
    ),
    "stale_unchecked": (
        '        if req["expected_revision"] != rev:\n            _fail("stale_revision")\n',
        "",
    ),
    "unknown_unchecked": (
        '        if (iid, pid) not in self._state:\n            _fail("unknown_provider")\n',
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
    "rev_lower_edge": (
        "0 <= v <= _MAX_REV",
        "-1 <= v <= _MAX_REV",
    ),
    "rev_upper_edge": (
        "0 <= v <= _MAX_REV",
        "0 <= v < _MAX_REV",
    ),
    "rev_check_after_approval": (
        (
            '        if not _exact_rev(req["expected_revision"]):\n'
            '            _fail("malformed_byom_request")\n'
            "        approved"
        ),
        "        approved",
    ),
    "exhausted_before_approval": (
        (
            "        if target is not None and not approved:\n"
            '            _fail("approval_missing")\n'
            "        # a revoke is never refused: at max revision it writes null in place\n"
            "        new_rev = rev if target is None and rev == _MAX_REV else _bump(rev)\n"
        ),
        (
            "        # a revoke is never refused: at max revision it writes null in place\n"
            "        new_rev = rev if target is None and rev == _MAX_REV else _bump(rev)\n"
            "        if target is not None and not approved:\n"
            '            _fail("approval_missing")\n'
        ),
    ),
    "exhausted_before_noop": (
        (
            "        if target == accepted:\n"
            "            return self.record(iid, pid)\n"
            "        if target is not None"
        ),
        (
            "        _bump(rev)\n"
            "        if target == accepted:\n"
            "            return self.record(iid, pid)\n"
            "        if target is not None"
        ),
    ),
    "register_unchecked": (
        (
            "        if not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None:\n"
            '            _fail("malformed_byom_request")\n'
        ),
        "",
    ),
    "register_pid_unchecked": (
        (
            "        if not _exact_str(provider_id) or _PRV_RE.fullmatch(provider_id) is None:\n"
            '            _fail("malformed_byom_request")\n'
        ),
        "",
    ),
    "reregister_resets": (
        "self._state.setdefault((install_id, provider_id), (None, 0))",
        "self._state[(install_id, provider_id)] = (None, 0)",
    ),
    "verdict_leaks_state": (
        '        return {"verdict": "allow", "terms_digest": digest}',
        '        return {"verdict": "allow", "terms_digest": digest, "revision": self._state[(iid, pid)][1]}',  # noqa: E501
    ),
    "revoke_exhausted": (
        "        new_rev = rev if target is None and rev == _MAX_REV else _bump(rev)\n",
        "        new_rev = _bump(rev)\n",
    ),
    "revoke_terminal_early": (
        "rev if target is None and rev == _MAX_REV else _bump(rev)",
        "rev if target is None and rev >= _MAX_REV - 1 else _bump(rev)",
    ),
    "accept_terminal_in_place": (
        "rev if target is None and rev == _MAX_REV else _bump(rev)",
        "rev if rev == _MAX_REV else _bump(rev)",
    ),
    "register_iid_match": (
        "        if not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None:",
        "        if not _exact_str(install_id) or _INS_RE.match(install_id) is None:",
    ),
    "register_pid_match": (
        "        if not _exact_str(provider_id) or _PRV_RE.fullmatch(provider_id) is None:",
        "        if not _exact_str(provider_id) or _PRV_RE.match(provider_id) is None:",
    ),
}
# terms and revision checks both raise malformed_byom_request before any
# state is read, so their order is unobservable
EQUIVALENT = {
    "terms_malformed_after_rev": (
        (
            "        target = None if terms is None else _terms(terms)\n"
            '        if not _exact_rev(req["expected_revision"]):\n'
            '            _fail("malformed_byom_request")\n'
        ),
        (
            '        if not _exact_rev(req["expected_revision"]):\n'
            '            _fail("malformed_byom_request")\n'
            "        target = None if terms is None else _terms(terms)\n"
        ),
    ),
}
_SELF = Path(__file__)


# the battery without this section: every test above, parametrized rows
# expanded; a green copy must run exactly this many and pass them all
EXPECTED_BATTERY = 466


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
