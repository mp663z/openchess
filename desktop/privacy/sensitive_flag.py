"""Production privacy sensitive_flag policy.

Contract-derived local state and fail-closed verdicts. No network, provider,
credential, payment or hosted-sync behavior is implemented here.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from tools.sensitive_flag_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[2]

DOC = yaml.safe_load((ROOT / "data/contracts/sensitive_flag.yaml").read_text())
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
