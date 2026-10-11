"""Production local privacy delete contract.

No hosted/provider dispatch, keys, payments or sync behavior. Contract-derived
request gates and deterministic local state. Redaction stores no call inputs.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from tools.delete_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[2]

DOC = yaml.safe_load((ROOT / "data/contracts/delete.yaml").read_text())
_CC = DOC["contract"]
_FIELDS = _CC["record"]["fields"]
_COL_RE = re.compile(_CC["identifiers"]["collection_id"]["grammar"])
_DEL_RE = re.compile(_CC["identifiers"]["approval"]["grammar"])
_STORES = tuple(_CC["identifiers"]["stores"]["names"])
_MAX_REV = 2**63 - 1
_ACCESS_KEYS = {"collection_id"}
_DELETE_KEYS = {"collection_id", "expected_revision", "decision"}


class DeleteError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(cls):
    raise DeleteError(cls)


def decision_for(collection_id, expected_revision):
    """The pinned delete-decision binding: collection, revision, delete."""
    raw = f"{collection_id}|{expected_revision}|delete".encode()
    return "del1:" + hashlib.sha256(raw).hexdigest()


def _exact_str(v):
    return type(v) is str


def _exact_rev(v):
    return type(v) is int and 0 <= v <= _MAX_REV


def _request(req, keys):
    if type(req) is not dict:
        _fail("malformed_delete_request")
    for k in list(req.keys()):
        if type(k) is not str:
            _fail("malformed_delete_request")
    if set(req) != keys:
        _fail("malformed_delete_request")
    cid = req["collection_id"]
    if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:
        _fail("malformed_delete_request")
    return cid


def _decision_ok(req, cid):
    token = req["decision"]
    if token is None:
        return False
    if not _exact_str(token) or _DEL_RE.fullmatch(token) is None:
        _fail("malformed_delete_request")
    return token == decision_for(cid, req["expected_revision"])


class Deleter:
    def __init__(self, stores):
        self._stores = stores
        self._state = {}

    def register(self, collection_id):
        if not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None:
            _fail("malformed_delete_request")
        st = self._state.get(collection_id)
        if st is not None and st[0] == "deleted":
            _fail("collection_deleted")
        self._state.setdefault(collection_id, ("live", 0, ()))
        return self.record(collection_id)

    def record(self, cid):
        state, rev, pending = self._state[cid]
        return dict(zip(_FIELDS, (cid, state, rev, list(pending)), strict=True))

    def access(self, req):
        cid = _request(req, _ACCESS_KEYS)
        st = self._state.get(cid)
        if st is None:
            _fail("unknown_collection")
        if st[0] != "live":
            _fail("collection_deleted")
        return "allow"

    def delete(self, req):
        cid = _request(req, _DELETE_KEYS)
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_delete_request")
        approved = _decision_ok(req, cid)
        if cid not in self._state:
            _fail("unknown_collection")
        state, rev, pending = self._state[cid]
        if req["expected_revision"] != rev:
            _fail("stale_revision")
        if state == "deleted":
            targets = pending
        else:
            if not approved:
                _fail("decision_missing")
            targets = _STORES
            rev = rev if rev == _MAX_REV else rev + 1
        self._state[cid] = ("deleted", rev, tuple(targets))  # tombstone first
        left = []
        for name in targets:
            try:
                self._stores[name].erase(cid)
            except Exception:  # noqa: BLE001 - a failed erase stays pending
                left.append(name)
        self._state[cid] = ("deleted", rev, tuple(left))
        return self.record(cid)
