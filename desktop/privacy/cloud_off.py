"""Production privacy cloud_off policy.

Contract-derived local state and fail-closed verdicts. No network, provider,
credential, payment or hosted-sync behavior is implemented here.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from tools.cloud_off_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[2]

DOC = yaml.safe_load((ROOT / "data/contracts/cloud_off.yaml").read_text())
_CC = DOC["contract"]
_FIELDS = _CC["record"]["fields"]
_INS_RE = re.compile(_CC["identifiers"]["install_id"]["grammar"])
_OPT_RE = re.compile(_CC["identifiers"]["opt_in"]["grammar"])
_COL_RE = re.compile(
    yaml.safe_load((ROOT / _CC["links"]["sensitive_flag_contract"]).read_text())["contract"][
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
