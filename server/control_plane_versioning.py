"""Pure compatibility verdict for validated control-plane schema-v2 snapshots.

The strict source checks are local to production: no reference test or fixture is
used at runtime. A malformed document is distinct from a valid breaking change.
"""

from __future__ import annotations

import math
import re

BASE = re.compile(r"/[a-z]+/v([1-9][0-9]{0,2})", re.ASCII)
AREA = re.compile(r"[a-z][a-z_]{0,31}", re.ASCII)
PATH = re.compile(r"(/[a-z0-9-]{1,32}){1,8}", re.ASCII)
FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
FIELD_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
ITEM_TYPES = {"string", "integer", "number", "boolean"}
FIELD_KEYS = {"type", "required", "example", "items", "fields", "write_only"}
OP_KEYS = {"method", "path", "auth", "mutating", "request", "response", "errors"}
CODES = {
    "auth_expired",
    "auth_invalid",
    "idempotency_conflict",
    "quota_exhausted",
    "quota_reservation_expired",
    "entitlement_missing",
    "provider_key_invalid",
    "provider_unavailable",
    "cost_cap_exceeded",
    "rate_limited",
    "malformed_request",
    "not_found",
    "conflict",
    "internal",
}
CHESS_TOKENS = {
    "game",
    "games",
    "move",
    "moves",
    "fen",
    "pgn",
    "san",
    "uci",
    "analysis",
    "note",
    "notes",
    "position",
    "board",
    "eval",
}
MAX_EXAMPLE_INT = 2**53 - 1


class VersionError(Exception):
    """Fresh, context-free malformed-version-request refusal."""

    def __init__(self, message="invalid version request"):
        super().__init__(message)
        self.failure_class = "malformed_version_request"
        self.code = "malformed_request"
        self.retryable = False


def _require(condition):
    if not condition:
        raise VersionError()


def _safe_tree(root):
    """Reject adversarial containers before any user-defined operator runs.

    Only the active ancestry is checked for cycles; shared child objects are
    legal. Unbound builtin iterators prevent invoking subclass overrides.
    """
    active = set()
    stack = [(root, 0, False)]
    while stack:
        node, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(node))
        elif type(node) is dict or type(node) is list:
            # T0419 has no depth limit for opaque metadata. Active ancestry
            # catches cycles without rejecting finite nested plain values.
            _require(id(node) not in active)
            active.add(id(node))
            if type(node) is dict:
                _require(all(type(key) is str for key in dict.keys(node)))
                children = dict.values(node)
            else:
                children = list.__iter__(node)
            stack.append((node, depth, True))
            stack.extend((child, depth + 1, False) for child in children)
        else:
            _require(type(node) in (str, int, bool, float, type(None)))


def _equal(left, right):
    """Deep equality without recursive Python container comparison.

    Both operands passed the exact-builtin, acyclic safety walk. Equality
    is by content, not alias topology, matching ordinary dict/list equality.
    """
    pairs = [(left, right)]
    while pairs:
        a, b = pairs.pop()
        if type(a) is not type(b):
            return False
        if type(a) is dict:
            if dict.keys(a) != dict.keys(b):
                return False
            pairs.extend((value, b[key]) for key, value in dict.items(a))
        elif type(a) is list:
            if len(a) != len(b):
                return False
            pairs.extend(zip(list.__iter__(a), list.__iter__(b), strict=True))
        elif a != b:
            return False
    return True


def _mapping(node):
    _require(type(node) is dict)
    return node


def _keys(node, expected):
    _mapping(node)
    _require(set(node) == set(expected))
    return node


def _unique_strings(node):
    _require(type(node) is list and all(type(item) is str for item in node))
    _require(len(node) == len(set(node)))
    return node


def _matches(pattern, value):
    _require(type(value) is str and pattern.fullmatch(value) is not None)


def _example(kind, item, value):
    if kind == "string":
        return type(value) is str and not any("\ud800" <= ch <= "\udfff" for ch in value)
    if kind == "integer":
        return type(value) is int and -MAX_EXAMPLE_INT <= value <= MAX_EXAMPLE_INT
    if kind == "number":
        return (type(value) is float and math.isfinite(value)) or (
            type(value) is int and -MAX_EXAMPLE_INT <= value <= MAX_EXAMPLE_INT
        )
    if kind == "boolean":
        return type(value) is bool
    if kind == "array":
        return type(value) is list and all(_example(item, None, v) for v in value)
    return False


def _fields(fields, depth, side):
    _mapping(fields)
    _require(len(fields) <= 256)
    for name, spec in fields.items():
        _matches(FIELD, name)
        _require(not (CHESS_TOKENS & set(name.split("_"))))
        _mapping(spec)
        _require(set(spec) <= FIELD_KEYS and {"type", "required"} <= set(spec))
        kind = spec["type"]
        _require(type(kind) is str and kind in FIELD_TYPES)
        _require(type(spec["required"]) is bool)
        if kind == "array":
            _require(type(spec.get("items")) is str and spec["items"] in ITEM_TYPES)
        else:
            _require("items" not in spec)
        if kind == "object":
            _require(depth < 8 and "example" not in spec and "fields" in spec)
            _fields(spec["fields"], depth + 1, side)
        else:
            _require("fields" not in spec)
        if "write_only" in spec:
            _require(spec["write_only"] is True and side == "request")
        if "example" in spec:
            _require(_example(kind, spec.get("items"), spec["example"]))


def _source(doc):
    _safe_tree(doc)
    _keys(doc, ("schema_version", "contract", "areas"))
    _require(type(doc["schema_version"]) is int and doc["schema_version"] == 2)
    cc = _mapping(doc["contract"])
    _require("versioning" in cc and "transport" in cc)
    versioning = _mapping(cc["versioning"])
    _require("base_path" in versioning)
    _matches(BASE, versioning["base_path"])
    major = int(BASE.fullmatch(versioning["base_path"]).group(1))
    transport = _mapping(cc["transport"])
    _require({"auth", "read_only_operations", "errors"} <= set(transport))
    auth = _mapping(transport["auth"])
    _require("public_operations" in auth)
    public = _unique_strings(auth["public_operations"])
    read_only = _unique_strings(transport["read_only_operations"])
    errors = _mapping(transport["errors"])
    _require("shape" in errors and "closed_enum" in errors)
    closed = _unique_strings(errors["closed_enum"])
    _require(bool(closed) and set(closed) <= CODES)
    shape = _keys(errors["shape"], ("error",))
    inner = _keys(shape["error"], ("fields",))
    error_fields = _mapping(inner["fields"])
    _require(list(error_fields) == ["code", "message", "retryable"])
    _fields(error_fields, 1, "response")
    for name, kind in (("code", "string"), ("message", "string"), ("retryable", "boolean")):
        _require(error_fields[name]["type"] == kind and error_fields[name]["required"] is True)
    if "example" in error_fields["code"]:
        _require(error_fields["code"]["example"] in closed)

    areas = _mapping(doc["areas"])
    _require(1 <= len(areas) <= 64)
    seen_paths = set()
    actual_public, actual_read_only = set(), set()
    for area_name, area in areas.items():
        _matches(AREA, area_name)
        _mapping(area)
        _require("ops" in area)
        _require(all(key == "ops" or type(val) is str for key, val in area.items()))
        ops = _mapping(area["ops"])
        _require(1 <= len(ops) <= 64)
        for op_name, op in ops.items():
            _matches(AREA, op_name)
            _keys(op, OP_KEYS)
            _require(type(op["method"]) is str and op["method"] in ("GET", "POST"))
            _matches(PATH, op["path"])
            _require(op["path"] not in seen_paths)
            seen_paths.add(op["path"])
            _require(type(op["auth"]) is str and op["auth"] in ("public", "required"))
            _require(type(op["mutating"]) is bool)
            name = f"{area_name}.{op_name}"
            if op["auth"] == "public":
                actual_public.add(name)
            if not op["mutating"]:
                actual_read_only.add(name)
            for side in ("request", "response"):
                body = _keys(op[side], ("fields",))
                _fields(body["fields"], 1, side)
            if op["method"] == "GET":
                _require(not op["mutating"] and not op["request"]["fields"])
            codes = _unique_strings(op["errors"])
            _require(bool(codes) and set(codes) <= set(closed))
    _require(actual_public == set(public) and actual_read_only == set(read_only))
    return major


def _additive_fields(before, after):
    for name, original in before.items():
        if name not in after:
            return False
        updated = after[name]
        if original["type"] == "object" and updated["type"] == "object":
            if {k: v for k, v in original.items() if k != "fields"} != {
                k: v for k, v in updated.items() if k != "fields"
            } or not _additive_fields(original["fields"], updated["fields"]):
                return False
        elif not _equal(original, updated):
            return False
    return all(spec["required"] is False for name, spec in after.items() if name not in before)


def _same_major(old, new):
    old_areas, new_areas = old["areas"], new["areas"]
    additions = {
        (area, op)
        for area, spec in new_areas.items()
        for op in spec["ops"]
        if area not in old_areas or op not in old_areas[area]["ops"]
    }
    old_contract, new_contract = old["contract"], new["contract"]
    old_transport, new_transport = old_contract["transport"], new_contract["transport"]
    for key, eligible in (
        ("read_only_operations", lambda spec: spec["mutating"] is False),
        ("public_operations", lambda spec: spec["auth"] == "public"),
    ):
        prev, curr = (
            (old_transport["auth"], new_transport["auth"])
            if key == "public_operations"
            else (old_transport, new_transport)
        )
        allowed = {f"{area}.{op}" for area, op in additions if eligible(new_areas[area]["ops"][op])}
        if set(curr[key]) != set(prev[key]) | allowed:
            return False

    # Compare every non-allowlist declaration without mutating the caller.
    def strip_contract(cc):
        transport = cc["transport"]
        return {
            **cc,
            "transport": {
                **{k: v for k, v in transport.items() if k not in ("read_only_operations", "auth")},
                "auth": {k: v for k, v in transport["auth"].items() if k != "public_operations"},
            },
        }

    if not _equal(strip_contract(old_contract), strip_contract(new_contract)):
        return False
    for area, previous in old_areas.items():
        current = new_areas.get(area)
        if current is None or not _equal(
            {k: v for k, v in previous.items() if k != "ops"},
            {k: v for k, v in current.items() if k != "ops"},
        ):
            return False
        for name, op in previous["ops"].items():
            updated = current["ops"].get(name)
            if updated is None or any(
                not _equal(op[key], updated[key]) for key in OP_KEYS - {"request", "response"}
            ):
                return False
            if any(
                not _additive_fields(op[side]["fields"], updated[side]["fields"])
                for side in ("request", "response")
            ):
                return False
    old_routes = {
        (op["method"], op["path"]) for area in old_areas.values() for op in area["ops"].values()
    }
    return all(
        (new_areas[area]["ops"][op]["method"], new_areas[area]["ops"][op]["path"]) not in old_routes
        for area, op in additions
    )


def compare(old, new, old_minor, new_minor):
    """Return an exact bool, or reject either malformed snapshot/minor."""
    _require(type(old_minor) is int and type(new_minor) is int)
    _require(0 <= old_minor <= 2147483647 and 0 <= new_minor <= 2147483647)
    old_major = _source(old)
    new_major = _source(new)
    _require(new_major >= old_major)
    if new_major == old_major:
        _require(new_minor >= old_minor)
        if old["contract"]["versioning"]["base_path"] != new["contract"]["versioning"]["base_path"]:
            return False
        return _same_major(old, new)
    return True
