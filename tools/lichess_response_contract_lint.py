"""T2623: lichess response contract lint.

Enforces the normative CONTENT of data/contracts/lichess_response.yaml
by EXACT STRUCTURED COMPARISON, never prose-marker presence: the
endpoint identity and auth posture, the exact request parameter set
with types/defaults/enums, the game object's required and optional
field sets with field types and the exact status_values enum, the
unknown-field tolerance policy, the error-envelope shape and the exact
status-code mapping (incl. the 429 >= 60s backoff), the
failure_mapping (no orphan classes, no undeclared error codes), the
closed error enum + exact shape, and the structured versioning. The
linked import contract artifact is linted first when present.

    python tools/lichess_response_contract_lint.py [path]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "lichess_response.yaml"

ENDPOINT = {
    "path": "/api/games/user/{username}",
    "method": "GET",
}
AUTH = {
    "spec_security": "oauth2-declared",
    "anonymous_observed": "not-found-envelope-2026-09-19",
    "anonymous_fallback": {"policy": "fall-back-per-failure-mapping",
                           "retry_storm": "forbidden"},
    "throttles": {
        "anonymous": "20-games-per-second",
        "oauth2": "30-games-per-second",
        "oauth2-own-games": "60-games-per-second",
    },
}
RESPONSE_FORMAT = {
    "stream": "ndjson",
    "one_object_per_line": True,
    "malformed_line": {"policy": "fail-closed-record",
                       "effect": "never-aborts-parsed-records"},
}
INCREMENTAL_PARAMS = ["since", "until"]
REQUIRED_VIOLATION = {"effect": "reject-record", "error": "malformed_request",
                      "partial_game": "never"}
UNKNOWN_FIELD_STORAGE = "never-stored"
STATUS_VIOLATION = {"effect": "reject-record", "error": "malformed_request"}
PARAMS = {
    "since": {"type": "integer-ms", "required": False},
    "until": {"type": "integer-ms", "required": False},
    "max": {"type": "integer", "min": 1, "required": False},
    "vs": {"type": "string", "required": False},
    "rated": {"type": "boolean", "required": False},
    "perfType": {"type": "string-csv", "required": False},
    "color": {"type": "string", "enum": ["white", "black"],
              "required": False},
    "analysed": {"type": "boolean", "required": False},
    "moves": {"type": "boolean", "default": True, "required": False},
    "pgnInJson": {"type": "boolean", "default": False, "required": False},
    "tags": {"type": "boolean", "default": True, "required": False},
    "clocks": {"type": "boolean", "default": False, "required": False},
    "evals": {"type": "boolean", "default": False, "required": False},
    "accuracy": {"type": "boolean", "default": False, "required": False},
    "opening": {"type": "boolean", "default": False, "required": False},
    "division": {"type": "boolean", "default": False, "required": False},
    "ongoing": {"type": "boolean", "default": False, "required": False},
    "finished": {"type": "boolean", "default": True, "required": False},
    "literate": {"type": "boolean", "default": False, "required": False},
    "lastFen": {"type": "boolean", "default": False, "required": False},
    "withBookmarked": {"type": "boolean", "default": False,
                       "required": False},
    "sort": {"type": "string", "enum": ["dateAsc", "dateDesc"],
             "default": "dateDesc", "required": False},
}
REQUIRED_FIELDS = ["id", "rated", "variant", "speed", "perf",
                   "createdAt", "lastMoveAt", "status", "players"]
OPTIONAL_FIELDS = ["source", "initialFen", "winner", "opening", "moves",
                   "pgn", "daysPerTurn", "analysis", "arenaTour",
                   "swissTour", "clock", "clocks", "division"]
UNKNOWN_FIELD_POLICY = "tolerate-additive"
FIELD_TYPES = {
    "id": "string",
    "rated": "boolean",
    "variant": "string",
    "speed": "string",
    "perf": "string",
    "createdAt": "integer-int64",
    "lastMoveAt": "integer-int64",
    "status": "string-enum",
    "players": "object-white-black",
}
STATUS_VALUES = ["created", "started", "aborted", "mate", "resign",
                 "stalemate", "timeout", "draw", "outoftime", "cheat",
                 "noStart", "unknownFinish", "insufficientMaterialClaim",
                 "variantEnd"]
ERROR_ENVELOPE = {
    "shape": {"error": "string"},
    "observed": "404-not-found-plain-error-string",
    "statuses": {
        "404": {"meaning": "not-found-or-anonymous-blocked",
                "class": "source_unavailable",
                "scope": "endpoint-level-never-per-game"},
        "429": {"meaning": "rate-limited", "class": "rate_limited",
                "retry": "wait-at-least-60s"},
        "5xx": {"meaning": "server-error", "class": "source_unavailable",
                "retry": "backoff"},
    },
}
FAILURE_CLASSES = ["malformed_response", "unknown_status_value",
                   "source_unavailable", "rate_limited", "auth_required"]
FAILURE_MAPPING = {
    "malformed_response": {
        "trigger": "line-not-json-or-required-field-missing-or-mistyped",
        "error": "malformed_request"},
    "unknown_status_value": {"trigger": "status-not-in-status_values",
                             "error": "malformed_request"},
    "source_unavailable": {"trigger": "non-200-non-429-or-unreachable",
                           "error": "source_unavailable"},
    "rate_limited": {"trigger": "http-429", "error": "rate_limited"},
    "auth_required": {
        "trigger": "endpoint-demands-credentials-we-do-not-have",
        "error": "auth_required"},
}
ERROR_ENUM = ["malformed_request", "source_unavailable", "rate_limited",
              "auth_required", "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}
VERSIONING = {
    "base_path": "/lichess-response/v1",
    "client_pin": "MAJOR",
    "minor_policy": "additive-only",
    "minor_additions": ["new-optional-fields", "new-status-values"],
    "downgrade_policy": "any-earlier-minor-within-major-without-migration",
    "major_bump": "new-base-path-required",
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {"id", "endpoint", "request", "game_object",
                    "error_envelope", "failure_classes", "failure_mapping",
                    "errors", "versioning"}
ALLOWED_PARAM = {"type", "required", "min", "default", "enum"}
ALLOWED_GAME_OBJECT = {"required_fields", "optional_fields",
                       "unknown_field_policy", "field_types",
                       "status_values", "required_violation",
                       "unknown_field_storage", "status_violation", "rule"}
ALLOWED_ENVELOPE_STATUS = {"meaning", "class", "retry", "scope"}


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(f"lichess response contract: {problem}")


def _keys(node: dict, allowed: set[str], where: str) -> None:
    _need(type(node) is dict, f"{where}: mapping required")
    unknown = set(node) - allowed
    _need(not unknown, f"{where}: unknown keys {sorted(unknown)}")


def _get(node: object, key: str, where: str) -> object:
    """Presence-checked access: a missing key is a contract violation,
    never a raw KeyError."""
    _need(type(node) is dict, f"{where}: mapping required")
    _need(key in node, f"{where}: missing key {key!r}")
    return node[key]


def _text(node: object, where: str) -> str:
    _need(type(node) is str and node.strip(), f"{where}: nonempty documentation string required")
    return node


def _strict_eq(actual: object, expected: object, where: str) -> None:
    _need(type(actual) is type(expected),
          f"{where}: type {type(actual).__name__} != {type(expected).__name__}")
    if isinstance(actual, dict):
        # keys compared as strings first: a YAML-unquoted numeric key must
        # surface as a ContractError, never a raw TypeError from sorted()
        if set(map(str, actual)) != set(map(str, expected)) or set(actual) != set(expected):
            raise ContractError(
                f"lichess response contract: {where}: keys "
                f"{sorted(map(str, actual))} != {sorted(map(str, expected))}")
        for k in actual:
            _strict_eq(actual[k], expected[k], f"{where}.{k}")
    elif isinstance(actual, list):
        _need(actual == expected, f"{where}: {actual!r} != {expected!r}")
    else:
        _need(actual == expected, f"{where}: {actual!r} != {expected!r}")


def lint(doc: object, root: Path = ROOT) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _need(doc.get("schema_version") == 1 and type(doc.get("schema_version")) is int,
          "schema_version: exact int 1")
    c = _get(doc, "contract", "document")
    _keys(c, ALLOWED_CONTRACT, "contract")
    for required in ALLOWED_CONTRACT:
        _get(c, required, "contract")
    _need(c["id"] == "lichess-response",
          "contract.id must be lichess-response")

    endpoint = _get(c, "endpoint", "contract")
    _keys(endpoint, {"path", "method", "auth", "response_format"},
          "contract.endpoint")
    _strict_eq({k: endpoint[k] for k in ("path", "method")},
               ENDPOINT, "contract.endpoint.identity")
    auth = _get(endpoint, "auth", "contract.endpoint")
    _keys(auth, {"spec_security", "anonymous_observed",
                 "anonymous_fallback", "throttles", "rule"},
          "contract.endpoint.auth")
    _strict_eq({k: v for k, v in auth.items() if k != "rule"},
               AUTH, "contract.endpoint.auth.fields")
    _text(_get(auth, "rule", "contract.endpoint.auth"),
          "contract.endpoint.auth.rule")
    rf = _get(endpoint, "response_format", "contract.endpoint")
    _keys(rf, {"stream", "one_object_per_line", "malformed_line", "rule"},
          "contract.endpoint.response_format")
    _strict_eq({k: v for k, v in rf.items() if k != "rule"},
               RESPONSE_FORMAT, "contract.endpoint.response_format.fields")
    _need(rf["one_object_per_line"] is True,
          "contract.endpoint.response_format.one_object_per_line must be true")
    _text(_get(rf, "rule", "contract.endpoint.response_format"),
          "contract.endpoint.response_format.rule")

    request = _get(c, "request", "contract")
    _keys(request, {"params", "incremental_params", "rule"},
          "contract.request")
    _strict_eq(_get(request, "incremental_params", "contract.request"),
               INCREMENTAL_PARAMS, "contract.request.incremental_params")
    params = _get(request, "params", "contract.request")
    _need(type(params) is dict, "contract.request.params: mapping required")
    for name, spec in params.items():
        _keys(spec, ALLOWED_PARAM, f"contract.request.params.{name}")
    _strict_eq(params, PARAMS, "contract.request.params")
    for name, spec in PARAMS.items():
        _need(spec["required"] is False,
              f"contract.request.params.{name}: all params are optional")
    _text(_get(request, "rule", "contract.request"),
          "contract.request.rule")

    game = _get(c, "game_object", "contract")
    _keys(game, ALLOWED_GAME_OBJECT, "contract.game_object")
    _strict_eq(_get(game, "required_fields", "contract.game_object"),
               REQUIRED_FIELDS, "contract.game_object.required_fields")
    _strict_eq(_get(game, "optional_fields", "contract.game_object"),
               OPTIONAL_FIELDS, "contract.game_object.optional_fields")
    _need(_get(game, "unknown_field_policy", "contract.game_object")
          == UNKNOWN_FIELD_POLICY,
          "contract.game_object.unknown_field_policy")
    _strict_eq(_get(game, "field_types", "contract.game_object"),
               FIELD_TYPES, "contract.game_object.field_types")
    _strict_eq(_get(game, "status_values", "contract.game_object"),
               STATUS_VALUES, "contract.game_object.status_values")
    _strict_eq(_get(game, "required_violation", "contract.game_object"),
               REQUIRED_VIOLATION, "contract.game_object.required_violation")
    _need(_get(game, "unknown_field_storage", "contract.game_object")
          == UNKNOWN_FIELD_STORAGE,
          "contract.game_object.unknown_field_storage")
    _strict_eq(_get(game, "status_violation", "contract.game_object"),
               STATUS_VIOLATION, "contract.game_object.status_violation")
    _text(_get(game, "rule", "contract.game_object"),
          "contract.game_object.rule")
    _need(set(FIELD_TYPES) == set(REQUIRED_FIELDS),
          "field_types must cover exactly the required fields")

    envelope = _get(c, "error_envelope", "contract")
    _keys(envelope, {"shape", "observed", "statuses", "rule"},
          "contract.error_envelope")
    _strict_eq(_get(envelope, "shape", "contract.error_envelope"),
               ERROR_ENVELOPE["shape"], "contract.error_envelope.shape")
    _need(_get(envelope, "observed", "contract.error_envelope")
          == ERROR_ENVELOPE["observed"],
          "contract.error_envelope.observed")
    statuses = _get(envelope, "statuses", "contract.error_envelope")
    _need(type(statuses) is dict, "contract.error_envelope.statuses: mapping required")
    for code, spec in statuses.items():
        _keys(spec, ALLOWED_ENVELOPE_STATUS,
              f"contract.error_envelope.statuses.{code}")
    _strict_eq(statuses, ERROR_ENVELOPE["statuses"],
               "contract.error_envelope.statuses")
    _text(_get(envelope, "rule", "contract.error_envelope"),
          "contract.error_envelope.rule")

    _strict_eq(_get(c, "failure_classes", "contract"),
               FAILURE_CLASSES, "contract.failure_classes")
    mapping = _get(c, "failure_mapping", "contract")
    _strict_eq(mapping, FAILURE_MAPPING, "contract.failure_mapping")
    errors = _get(c, "errors", "contract")
    _strict_eq(errors, {"closed_enum": ERROR_ENUM, "shape": ERROR_SHAPE},
               "contract.errors")
    for cls in FAILURE_CLASSES:
        err = mapping[cls]["error"]
        _need(err in errors["closed_enum"],
              f"failure class {cls} maps to undeclared error {err}")

    versioning = _get(c, "versioning", "contract")
    _keys(versioning, {"base_path", "client_pin", "minor_policy",
                       "minor_additions", "downgrade_policy", "major_bump",
                       "rule"}, "contract.versioning")
    _strict_eq({k: v for k, v in versioning.items() if k != "rule"},
               VERSIONING, "contract.versioning.fields")
    _text(_get(versioning, "rule", "contract.versioning"),
          "contract.versioning.rule")


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        path = Path(argv[1]).resolve()
        root = path.parents[2] if path.parent.name == "contracts" else ROOT
    else:
        path, root = CONTRACT, ROOT
    try:
        lint(yaml.safe_load(path.read_text()), root)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("OK lichess response contract lint: lichess response contract v1 clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
