"""Production local privacy redaction contract.

No hosted/provider dispatch, keys, payments or sync behavior. Contract-derived
request gates and deterministic local state. Redaction stores no call inputs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tools.redaction_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[2]

DOC = yaml.safe_load((ROOT / "data/contracts/redaction.yaml").read_text())
_CC = DOC["contract"]
_KEYS = set(_CC["request"]["fields"])
MARKER = "redacted"
_MAX_TEXT = 1024
_MAX_SECRETS = 64
_MAX_SECRET = 4096
_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")


class RedactionError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail():
    raise RedactionError("malformed_redaction_request")


def _request(req):
    if type(req) is not dict:
        _fail()
    for k in list(req.keys()):
        if type(k) is not str:
            _fail()
    if set(req) != _KEYS:
        _fail()
    text = req["text"]
    if type(text) is not str:
        _fail()
    secrets = req["secrets"]
    if type(secrets) is not list or len(secrets) > _MAX_SECRETS:
        _fail()
    for s in secrets:
        if type(s) is not str or not 1 <= len(s) <= _MAX_SECRET:
            _fail()
    return text, secrets


def _hits(text, secrets):
    if len(text) > _MAX_TEXT:
        return True
    for ch in text:
        if not " " <= ch <= "~":
            return True
    folded = text.casefold()
    for s in secrets:
        if s.casefold() in folded:
            return True
    return _RUN_RE.search(text) is not None


def redact(req):
    text, secrets = _request(req)
    return MARKER if _hits(text, secrets) else text


def emit_text(req):
    """The sink path: never raises for a malformed request, never emits raw."""
    try:
        return redact(req)
    except RedactionError:
        return MARKER
