"""Production local privacy byom contract.

No hosted/provider dispatch, keys, payments or sync behavior. Contract-derived
request gates and deterministic local state. Redaction stores no call inputs.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from desktop.privacy.cloud_off import CloudError
from tools.byom_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[2]

DOC = yaml.safe_load((ROOT / "data/contracts/byom.yaml").read_text())
_CC = DOC["contract"]
_ID = _CC["identifiers"]
_FIELDS = _CC["record"]["fields"]
_INS_RE = re.compile(_ID["install_id"]["grammar"])
_PRV_RE = re.compile(_ID["provider_id"]["grammar"])
_TRM_RE = re.compile(_ID["terms_digest"]["grammar"])
_BYT_RE = re.compile(_ID["approval"]["grammar"])
_REGION_RE = re.compile(_ID["terms"]["region"].split("matching-", 1)[1])
_COL_RE = re.compile(
    yaml.safe_load((ROOT / _CC["links"]["sensitive_flag_contract"]).read_text())["contract"][
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
