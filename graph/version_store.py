"""Content-addressed immutable graph-version store.

Production implementation of data/contracts/version.yaml. Public methods copy
at trust boundaries; failed inserts and merges are atomic.
"""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import date

from tools.version_contract_lint import FAILURE_MAPPING

_ID = re.compile(r"^gv1:[0-9a-f]{64}$")
_DIGEST = re.compile(r"^gdv1:[0-9a-f]{64}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_LABEL = re.compile(r"^[ -~]{1,120}$")
FIELDS = {"version_id", "parent_ids", "graph_digest", "created_at", "label"}


class VersionError(Exception):
    def __init__(self, failure_class, witness=None):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.witness = witness


def _parents(value):
    if type(value) is not list:
        raise VersionError("malformed_version_record")
    try:
        if any(type(item) is not str or _ID.fullmatch(item) is None for item in value):
            raise VersionError("malformed_version_record")
        return sorted(set(value))
    except VersionError:
        raise
    except BaseException as exc:
        raise VersionError("malformed_version_record") from exc


def version_id(parent_ids, graph_digest):
    parents = _parents(parent_ids)
    if type(graph_digest) is not str or _DIGEST.fullmatch(graph_digest) is None:
        raise VersionError("malformed_version_record")
    body = "gv1-content\n" + "".join(parent + "\n" for parent in parents) + graph_digest + "\n"
    return "gv1:" + hashlib.sha256(body.encode()).hexdigest()


def _timestamp(v):
    if type(v) is not str or not _TS.fullmatch(v):
        return False
    try:
        y, m, d = map(int, (v[:4], v[5:7], v[8:10]))
        date(y, m, d)
    except ValueError:
        return False
    return int(v[11:13]) < 24 and int(v[14:16]) < 60 and int(v[17:19]) < 60


class VersionStore:
    def __init__(self):
        self._records = {}
        self._root_id = None

    @property
    def records(self):
        return copy.deepcopy(self._records)

    @property
    def root_id(self):
        return self._root_id

    def make_record(self, parent_ids, graph_digest, created_at, label):
        parents = _parents(parent_ids)
        if type(created_at) is not str or type(label) is not str:
            raise VersionError("malformed_version_record")
        return {
            "version_id": version_id(parents, graph_digest),
            "parent_ids": parents,
            "graph_digest": graph_digest,
            "created_at": created_at,
            "label": label,
        }

    def _validate(self, r):
        if type(r) is not dict or set(r) != FIELDS:
            raise VersionError("malformed_version_record")
        _parents(r["parent_ids"])
        if type(r["version_id"]) is not str or not _ID.fullmatch(r["version_id"]):
            raise VersionError("malformed_version_record")
        if type(r["graph_digest"]) is not str or not _DIGEST.fullmatch(r["graph_digest"]):
            raise VersionError("malformed_version_record")
        if (
            not _timestamp(r["created_at"])
            or type(r["label"]) is not str
            or not _LABEL.fullmatch(r["label"])
        ):
            raise VersionError("malformed_version_record")
        if r["version_id"] != version_id(r["parent_ids"], r["graph_digest"]):
            raise VersionError("malformed_version_record")

    def insert(self, record):
        if type(record) is not dict:
            raise VersionError("malformed_version_record")
        try:
            self._validate(record)
            r = copy.deepcopy(record)
        except VersionError:
            raise
        except BaseException as exc:
            raise VersionError("malformed_version_record") from exc
        self._validate(r)
        r["parent_ids"] = sorted(set(r["parent_ids"]))
        vid = r["version_id"]
        if vid in self._records:
            if self._records[vid] != r:
                raise VersionError(
                    "conflicting_version",
                    {"stored": copy.deepcopy(self._records[vid]), "incoming": r},
                )
            return copy.deepcopy(self._records[vid])
        if not r["parent_ids"]:
            if self._root_id is not None:
                raise VersionError("root_violation")
        else:
            for p in r["parent_ids"]:
                if p not in self._records:
                    raise VersionError("unknown_parent")
                if r["created_at"] < self._records[p]["created_at"]:
                    raise VersionError("nonmonotonic_version")
        self._records[vid] = copy.deepcopy(r)
        if not r["parent_ids"]:
            self._root_id = vid
        return copy.deepcopy(r)

    def canonical_view(self):
        return sorted(self._records)

    def merge(self, other):
        if not isinstance(other, VersionStore):
            raise VersionError("malformed_version_record")
        staged = VersionStore()
        staged._records = copy.deepcopy(self._records)
        staged._root_id = self._root_id
        pending = [copy.deepcopy(other._records[k]) for k in other.canonical_view()]
        for r in pending:
            staged._validate(r)
        while pending:
            ready = [r for r in pending if all(p in staged._records for p in r["parent_ids"])]
            if not ready:
                staged.insert(pending[0])
            for r in ready:
                staged.insert(r)
                pending.remove(r)
        self._records = staged._records
        self._root_id = staged._root_id
        return self
