"""Production graph-state diff runtime for data/contracts/diff.yaml."""

from __future__ import annotations

import copy
import hashlib
import re

from tools.diff_contract_lint import FAILURE_MAPPING
from tools.variant_runtime import VariantError, identity, parse_position

_STATE_ID = re.compile(r"^gs1:[0-9a-f]{64}$")
_DIGEST = re.compile(r"^pdv1:[0-9a-f]{64}$")
_FIELDS = {"base_id", "target_id", "added", "removed", "changed"}
_RECORD = {"variant", "digest", "snapshot_fen"}


class DiffError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(failure_class):
    raise DiffError(failure_class)


def _record_identity(record):
    if type(record) is not dict or set(record) != _RECORD:
        _fail("malformed_diff_record")
    if any(type(record[field]) is not str for field in _RECORD):
        _fail("malformed_diff_record")
    if _DIGEST.fullmatch(record["digest"]) is None:
        _fail("malformed_diff_record")
    parts = record["snapshot_fen"].split(" ")
    if len(parts) != 6 or parts[-2:] != ["0", "1"]:
        _fail("malformed_diff_record")
    try:
        projection = identity(parse_position(record["variant"], record["snapshot_fen"]))
    except VariantError as error:
        raise DiffError("malformed_diff_record") from error
    return repr(tuple(projection.values()))


def _validate_state(state):
    if type(state) is not dict:
        _fail("malformed_diff_record")
    try:
        entries = list(dict.items(state))
    except BaseException as error:
        raise DiffError("malformed_diff_record") from error
    for key, record in entries:
        if type(key) is not str or _record_identity(record) != key:
            _fail("malformed_diff_record")


def state_id(state):
    _validate_state(state)
    parts = []
    for key in sorted(state):
        record = state[key]
        body = "|".join(f"{field}={record[field]}" for field in sorted(record))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256("".join(parts).encode()).hexdigest()


def compute(base, target):
    _validate_state(base)
    _validate_state(target)
    added = {key: copy.deepcopy(target[key]) for key in target if key not in base}
    removed = {key: copy.deepcopy(base[key]) for key in base if key not in target}
    changed = {
        key: {"base": copy.deepcopy(base[key]), "target": copy.deepcopy(target[key])}
        for key in base
        if key in target and base[key] != target[key]
    }
    return {
        "base_id": state_id(base),
        "target_id": state_id(target),
        "added": dict(sorted(added.items())),
        "removed": dict(sorted(removed.items())),
        "changed": dict(sorted(changed.items())),
    }


def validate_diff(diff):
    if type(diff) is not dict or set(diff) != _FIELDS:
        _fail("malformed_diff_record")
    if any(
        type(diff[field]) is not str or _STATE_ID.fullmatch(diff[field]) is None
        for field in ("base_id", "target_id")
    ):
        _fail("malformed_diff_record")
    for section in ("added", "removed"):
        _validate_state(diff[section])
    if type(diff["changed"]) is not dict:
        _fail("malformed_diff_record")
    for key, witness in dict.items(diff["changed"]):
        if type(key) is not str or type(witness) is not dict or set(witness) != {"base", "target"}:
            _fail("malformed_diff_record")
        if _record_identity(witness["base"]) != key or _record_identity(witness["target"]) != key:
            _fail("malformed_diff_record")
        if witness["base"] == witness["target"]:
            _fail("malformed_diff_record")
    sections = [set(diff[name]) for name in ("added", "removed", "changed")]
    if (sections[0] & sections[1]) or (sections[0] & sections[2]) or (sections[1] & sections[2]):
        _fail("malformed_diff_record")
    empty = not any(sections)
    if empty and diff["base_id"] != diff["target_id"]:
        _fail("divergent_target")
    if not empty and diff["base_id"] == diff["target_id"]:
        _fail("malformed_diff_record")


def apply(diff, base):
    validate_diff(diff)
    _validate_state(base)
    if state_id(base) != diff["base_id"] or set(diff["added"]) & set(base):
        _fail("conflicting_base")
    staged = copy.deepcopy(base)
    for key, record in diff["removed"].items():
        if key not in staged:
            _fail("unknown_identity")
        if staged[key] != record:
            _fail("conflicting_base")
    for key, witness in diff["changed"].items():
        if key not in staged:
            _fail("unknown_identity")
        if staged[key] != witness["base"]:
            _fail("conflicting_base")
    for key in diff["removed"]:
        del staged[key]
    for key, witness in diff["changed"].items():
        staged[key] = copy.deepcopy(witness["target"])
    for key, record in diff["added"].items():
        staged[key] = copy.deepcopy(record)
    if state_id(staged) != diff["target_id"]:
        _fail("divergent_target")
    return staged
