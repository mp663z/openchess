"""T2624 - Lichess games-export response client, driven by the T2623
contract (data/contracts/lichess_response.yaml).

Every rule is read from the contract document at runtime - the YAML is
the single source of truth, so a contract change changes enforcement
without code edits:

- Requests carry exactly the pinned Accept header (application/x-ndjson;
  the spec default is PGN) and only declared params, with boundary
  checks (since/until minimum, perfType closed enum, ndjson-only params).
- The response is parsed as NDJSON: one GameJson object per line. A
  malformed line fails ONLY that record fail-closed and never aborts
  already-parsed records.
- Each game object is validated against the contract: required fields
  present and typed, variant/speed/status closed enums, optional field
  type map, unknown fields tolerated and NEVER stored.
- HTTP failures map per the contract: 404 is endpoint-level (never a
  per-game failure), 429 always backs off at least 60 seconds, 5xx is
  source_unavailable.
- Errors use the contract's closed enum and {code, message, retryable}
  shape.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "data" / "contracts" / "lichess_response.yaml"

C = yaml.safe_load(CONTRACT_PATH.read_text())["contract"]
_ENDPOINT = C["endpoint"]
_REQUEST = C["request"]
_GAME = C["game_object"]
_ENVELOPE = C["error_envelope"]
FAILURE_MAPPING = C["failure_mapping"]

ACCEPT_HEADER = _REQUEST["headers"]["Accept"]["value"]
ENDPOINT_PATH = _ENDPOINT["path"]
ENDPOINT_METHOD = _ENDPOINT["method"]
PARAMS = _REQUEST["params"]
INCREMENTAL_PARAMS = list(_REQUEST["incremental_params"])
NDJSON_ONLY_PARAMS = list(_REQUEST["ndjson_only_params"])
REQUIRED_FIELDS = list(_GAME["required_fields"])
FIELD_TYPES = dict(_GAME["field_types"])
OPTIONAL_FIELD_TYPES = dict(_GAME["optional_field_types"])
VARIANT_VALUES = list(_GAME["variant_values"])
SPEED_VALUES = list(_GAME["speed_values"])
STATUS_VALUES = list(_GAME["status_values"])
UNKNOWN_FIELD_POLICY = _GAME["unknown_field_policy"]
UNKNOWN_FIELD_STORAGE = _GAME["unknown_field_storage"]
REQUIRED_VIOLATION = dict(_GAME["required_violation"])
RETRY_AFTER_429_SECONDS = 60  # contract: 429 retry = wait-at-least-60s


class ContractViolation(Exception):
    """A request would violate the pinned contract (adjacent-wrong)."""


@dataclass(frozen=True)
class ImportError_:
    """Structured error per the contract's error shape."""
    code: str
    message: str
    retryable: bool


@dataclass
class RecordResult:
    """Outcome for one NDJSON line: either a validated game or a
    fail-closed record error. One malformed line never aborts the
    stream."""
    ok: bool
    game: dict[str, Any] | None = None
    error: ImportError_ | None = None
    line_no: int | None = None


@dataclass
class StreamResult:
    games: list[dict[str, Any]] = field(default_factory=list)
    record_errors: list[RecordResult] = field(default_factory=list)


def build_request(username: str, **params: Any) -> dict[str, Any]:
    """Build a contract-conforming request description (method, path,
    headers, query params). Only declared params pass; boundary rules
    from the contract are enforced fail-closed."""
    if type(username) is not str or not username.strip():
        raise ContractViolation("username must be a nonempty string")
    query: dict[str, str] = {}
    for name, value in params.items():
        if name not in PARAMS:
            raise ContractViolation(f"undeclared param {name!r}")
        spec = PARAMS[name]
        ptype = spec["type"]
        if ptype in ("integer", "integer-ms"):
            if type(value) is not int:
                raise ContractViolation(f"param {name}: integer required")
            if "min" in spec and value < spec["min"]:
                raise ContractViolation(
                    f"param {name}: below spec minimum {spec['min']}")
            query[name] = str(value)
        elif ptype == "boolean":
            if type(value) is not bool:
                raise ContractViolation(f"param {name}: boolean required")
            query[name] = "true" if value else "false"
        elif ptype == "string-csv":
            values = value if isinstance(value, list) else [value]
            if not values or not all(type(v) is str for v in values):
                raise ContractViolation(f"param {name}: string list required")
            enum = spec.get("enum")
            if enum:
                bad = [v for v in values if v not in enum]
                if bad:
                    raise ContractViolation(
                        f"param {name}: values {bad} outside closed enum")
            query[name] = ",".join(values)
        elif ptype == "string":
            if type(value) is not str:
                raise ContractViolation(f"param {name}: string required")
            enum = spec.get("enum")
            if enum and value not in enum:
                raise ContractViolation(
                    f"param {name}: {value!r} outside closed enum")
            query[name] = value
        else:
            raise ContractViolation(f"param {name}: unknown spec type")
    return {
        "method": ENDPOINT_METHOD,
        "path": ENDPOINT_PATH.format(username=username),
        "headers": {"Accept": ACCEPT_HEADER},
        "params": query,
    }


def _type_ok(value: Any, declared: str) -> bool:
    if declared in ("string", "string-enum"):
        return type(value) is str
    if declared == "boolean":
        return type(value) is bool
    if declared in ("integer", "integer-int64"):
        return type(value) is int and type(value) is not bool
    if declared == "object" or declared == "object-white-black":
        return type(value) is dict
    if declared == "array":
        return type(value) is list
    return False


def _optional_type_ok(value: Any, spec: Any) -> bool:
    if type(spec) is str:
        return _type_ok(value, spec)
    otype = spec.get("type")
    if otype == "array":
        if type(value) is not list:
            return False
        item = spec.get("items")
        if item == "integer":
            return all(type(v) is int and type(v) is not bool for v in value)
        if item == "object":
            return all(type(v) is dict for v in value)
        return True
    if otype == "object":
        if type(value) is not dict:
            return False
        for req_key in spec.get("required", []):
            if req_key not in value:
                return False
        ftypes = spec.get("field_types", {})
        for fname, ftype in ftypes.items():
            if fname in value and not _type_ok(value[fname], ftype):
                return False
        return True
    if otype == "string":
        if type(value) is not str:
            return False
        enum = spec.get("enum")
        return not enum or value in enum
    return _type_ok(value, otype)


def validate_game(obj: Any) -> dict[str, Any]:
    """Validate one GameJson object against the contract. Returns the
    stored projection: declared fields only - unknown fields are
    tolerated but NEVER stored. Raises ContractViolation fail-closed on
    any contract violation."""
    if type(obj) is not dict:
        raise ContractViolation("game record is not an object")
    for fname in REQUIRED_FIELDS:
        if fname not in obj:
            raise ContractViolation(f"required field {fname} missing")
        if not _type_ok(obj[fname], FIELD_TYPES[fname]):
            raise ContractViolation(
                f"required field {fname} mistyped "
                f"(want {FIELD_TYPES[fname]})")
    if obj["variant"] not in VARIANT_VALUES:
        raise ContractViolation(f"variant {obj['variant']!r} not in enum")
    if obj["speed"] not in SPEED_VALUES:
        raise ContractViolation(f"speed {obj['speed']!r} not in enum")
    if obj["status"] not in STATUS_VALUES:
        raise ContractViolation(f"status {obj['status']!r} not in enum")
    players = obj["players"]
    pshape = _GAME["players_shape"]
    user_req = list(pshape["user_required"])
    ai_req = list(pshape["ai_required"])
    for side in pshape["required"]:
        p = players.get(side)
        if type(p) is not dict:
            raise ContractViolation(f"players.{side} missing or not an object")
        is_user = all(k in p for k in user_req)
        is_ai = all(k in p for k in ai_req)
        if is_user and is_ai:
            raise ContractViolation(
                f"players.{side} matches both user and ai shapes (oneOf)")
        if not is_user and not is_ai:
            raise ContractViolation(
                f"players.{side} matches neither user nor ai shape")
    stored: dict[str, Any] = {f: obj[f] for f in REQUIRED_FIELDS}
    for fname, spec in OPTIONAL_FIELD_TYPES.items():
        if fname not in obj:
            continue
        if not _optional_type_ok(obj[fname], spec):
            raise ContractViolation(f"optional field {fname} mistyped")
        stored[fname] = obj[fname]
    if UNKNOWN_FIELD_POLICY != "tolerate-additive" or \
            UNKNOWN_FIELD_STORAGE != "never-stored":
        raise ContractViolation("contract unknown-field policy changed")
    return stored


def parse_stream(lines: Iterator[str] | list[str]) -> StreamResult:
    """Parse an NDJSON game stream. A malformed line fails ONLY that
    record (fail-closed) and never aborts already-parsed records."""
    result = StreamResult()
    for i, line in enumerate(lines):
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
            game = validate_game(obj)
        except (json.JSONDecodeError, ContractViolation) as e:
            result.record_errors.append(RecordResult(
                ok=False,
                error=ImportError_(
                    code=FAILURE_MAPPING["malformed_response"]["error"],
                    message=f"line {i + 1}: {e}",
                    retryable=False),
                line_no=i + 1))
            continue
        result.games.append(game)
    return result


def _envelope_ok(body: str) -> bool:
    """The error body must parse as JSON and match the pinned envelope
    shape exactly (error: string, no extra or missing members)."""
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return False
    shape = _ENVELOPE["shape"]
    if type(obj) is not dict or set(obj) != set(shape):
        return False
    return all(_type_ok(obj[fname], ftype) for fname, ftype in shape.items())


def map_http_error(status: int, body: str = "") -> ImportError_:
    """Map an HTTP failure per the contract error envelope. 404 is
    endpoint-level (never a per-game failure); 429 always backs off at
    least 60 seconds; 5xx is source_unavailable. The body must match the
    pinned envelope shape; a malformed envelope is a malformed_response,
    never silently mapped."""
    statuses = _ENVELOPE["statuses"]
    key = str(status) if str(status) in statuses else (
        "5xx" if 500 <= status <= 599 else None)
    if key is None:
        raise ContractViolation(f"unmapped HTTP status {status}")
    if not _envelope_ok(body):
        return ImportError_(
            code=FAILURE_MAPPING["malformed_response"]["error"],
            message=f"http {status}: error envelope malformed",
            retryable=False)
    cls = statuses[key]["class"]
    if key == "429":
        retry = statuses[key]["retry"]
        if retry != "wait-at-least-60s":
            raise ContractViolation("contract 429 retry policy changed")
    return ImportError_(
        code=FAILURE_MAPPING[cls]["error"],
        message=f"http {status}: {statuses[key]['meaning']}",
        retryable=(key in ("429", "5xx")))
