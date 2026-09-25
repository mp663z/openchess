"""T0446 error-envelope reference contract and hostile-type battery."""
from __future__ import annotations

# ruff: noqa: E501  (explicit source-mutant rows)
import ast
import copy
import inspect
import re
from pathlib import Path

import pytest
import yaml

from tests.test_t0419_openapi_contract import derive as strict_source_derivation
from tools.control_plane_errors_contract_lint import CONTRACT, lint
from tools.openapi_contract_lint import CHESS_TOKENS
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text())
TOKENS = CHESS_TOKENS
FEN_PLACEMENT = yaml.safe_load((ROOT / "data/contracts/fen.yaml").read_text())["contract"]["placement"]
FEN_PIECES = frozenset(FEN_PLACEMENT["piece_letters"])
FEN_DIGITS = {int(n) for n in FEN_PLACEMENT["empty_run_digits"]}



class ErrorsError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.failure_class = "malformed_error_result"
        self.code = "malformed_request"
        self.retryable = False


def _fail(message):
    raise ErrorsError(message)


def _source(source):
    bad = False
    try:
        strict_source_derivation(source)
        enum = source["contract"]["transport"]["errors"]["closed_enum"]
    except Exception:
        bad = True
    if bad:
        _fail("source")
    return enum


def _text(text):
    return type(text) is str and not any("\ud800" <= ch <= "\udfff" for ch in text)


def _tokens(key):
    # The T0419 token list is normative; split camelCase boundaries as
    # well as punctuation before comparing whole lowercase tokens.
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    return [part.lower() for part in re.split(r"[._-]", separated) if part]


def _fen_placement_in(text):
    # Constructed from fen.yaml's piece letters, run digits and rank
    # count. A rank must sum to 8, and a placement has exactly 8 ranks.
    # Search every starting offset, not only whitespace-delimited tokens.
    # A slash on either edge means this is part of a longer placement.
    rank_chars = "".join(re.escape(ch) for ch in FEN_PIECES | set(FEN_PLACEMENT["empty_run_digits"]))
    rank = f"[{rank_chars}]+"
    pattern = re.compile((rank + re.escape(FEN_PLACEMENT["rank_separator"])) * (FEN_PLACEMENT["rank_count"] - 1) + rank)
    for start in range(len(text)):
        match = pattern.match(text, start)
        if match is None or (start and text[start - 1] in "0123456789"):
            continue
        if match.end() < len(text) and text[match.end()] in "0123456789":
            continue
        ranks = match.group().split(FEN_PLACEMENT["rank_separator"])
        if all(sum(int(ch) if ch.isdigit() else 1 for ch in r) == FEN_PLACEMENT["rank_sum"]
               and not re.search(r"[1-8]{2}", r) for r in ranks):
            return True
    return False


def _safe_key(key, declared=False):
    return (type(key) is str and _text(key)
            and not TOKENS.intersection(_tokens(key))
            and (declared or (key.lower() not in {"key_material", "password_hash_client", "token", "secret"}
                              and not ({"provider", "key"} <= set(_tokens(key))))))


def _json_safe(value, depth=0, allowed=frozenset()):
    if depth > 8:
        return False
    if value is None or type(value) in (bool, int):
        return True
    if type(value) is str:
        return _text(value) and not _fen_placement_in(value)
    if type(value) is float:
        return value == value and abs(value) != float("inf")
    if type(value) is list:
        return len(value) <= 256 and all(_json_safe(v, depth + 1) for v in value)
    if type(value) is dict:
        return len(value) <= 256 and all(_safe_key(k, k in allowed) and _json_safe(v, depth + 1) for k, v in value.items())
    return False


def classify(source, status, payload, operation=None):
    """Reference only: status class controls error signalling, not key presence."""
    enum = _source(source)
    if type(status) is not int or not (200 <= status <= 299 or 400 <= status <= 599):
        _fail("status")
    declared = frozenset()
    if operation is not None:
        if type(operation) is not str:
            _fail("operation")
        try:
            area, name = operation.split(".")
            declared = frozenset(source["areas"][area]["ops"][name]["response"]["fields"]) if status < 300 else frozenset()
        except Exception:
            _fail("operation")
    if type(payload) is not dict or not _json_safe(payload, allowed=declared):
        _fail("payload")
    if status < 300:
        return {"kind": "success"}
    error = payload.get("error")
    if type(error) is not dict:
        _fail("error object")
    for field in ("code", "message", "retryable"):
        if field not in error:
            _fail("missing field")
    code = error["code"]
    if type(code) is not str or code not in enum or not _text(error["message"]) or type(error["retryable"]) is not bool:
        _fail("error field")
    if any(k in error["message"].lower() for k in ("key_material", "password_hash_client")):
        _fail("secret in message")
    return {"kind": "error", "code": code, "message": error["message"], "retryable": error["retryable"]}


def envelope(code="not_found", message="missing", retryable=False):
    return {"error": {"code": code, "message": message, "retryable": retryable}}


@pytest.mark.parametrize("status", [200, 201, 299])
def test_success_status_ignores_error_field_as_signal(status):
    body = {"error": {"code": "not_found", "message": "data", "retryable": False}}
    before = copy.deepcopy(body)
    assert classify(SOURCE, status, body) == {"kind": "success"}
    assert body == before


@pytest.mark.parametrize("status", [400, 401, 429, 500, 599])
def test_error_class_and_additive_fields(status):
    body = envelope()
    body["trace_id"] = "trace-1"
    body["error"]["support_id"] = "s-1"
    before = copy.deepcopy(body)
    assert classify(SOURCE, status, body) == {"kind": "error", "code": "not_found", "message": "missing", "retryable": False}
    assert body == before


@pytest.mark.parametrize("status", [0, 100, 199, 300, 399, 600, True, 400.0])
def test_unsupported_status_is_typed(status):
    with pytest.raises(ErrorsError) as e:
        classify(SOURCE, status, envelope())
    assert e.value.code == "malformed_request" and e.value.__cause__ is None and e.value.__context__ is None


@pytest.mark.parametrize("change", [
    lambda d: d.pop("error"),
    lambda d: d.update(error=[]),
    lambda d: d["error"].pop("code"),
    lambda d: d["error"].update(code="outside_enum"),
    lambda d: d["error"].update(code=1),
    lambda d: d["error"].update(message=True),
    lambda d: d["error"].update(message="\ud800"),
    lambda d: d["error"].update(retryable=1),
    lambda d: d.update({1: "bad key"}),
    lambda d: d["error"].update(key_material="secret"),
    lambda d: d["error"].update(message="key_material = secret"),
    lambda d: d.update(analysis="private"),
    lambda d: d.update(extra=object()),
    lambda d: d.update(extra=float("nan")),
])
def test_malformed_error_rejects_without_mutation(change):
    source = copy.deepcopy(SOURCE)
    body = envelope()
    change(body)
    before = copy.deepcopy((source, body))
    with pytest.raises(ErrorsError) as e:
        classify(source, 400, body)
    assert e.value.failure_class == "malformed_error_result" and e.value.__context__ is None
    assert source == before[0]
    if "extra" not in body or type(body["extra"]) not in (object, float):
        assert body == before[1]


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version=True),
    lambda d: d["contract"]["transport"]["errors"].update(closed_enum=[object()]),
    lambda d: d["contract"]["transport"]["errors"]["shape"]["error"]["fields"].update({1: None}),
])
def test_hostile_source_rejects_typed(change):
    source = copy.deepcopy(SOURCE)
    change(source)
    with pytest.raises(ErrorsError) as e:
        classify(source, 400, envelope())
    assert e.value.code == "malformed_request" and e.value.__context__ is None


def test_detached_result_and_fresh_error():
    body = envelope()
    result = classify(SOURCE, 404, body)
    body["error"]["message"] = "changed"
    assert result["message"] == "missing"
    errors = []
    for _ in range(2):
        with pytest.raises(ErrorsError) as e:
            classify(SOURCE, 300, envelope())
        errors.append(e.value)
    assert errors[0] is not errors[1]


def test_lint_and_mutated_contract(tmp_path):
    lint()
    doc = yaml.safe_load(CONTRACT.read_text())
    doc["contract"]["result"]["success"] = "error-key-signals-failure"
    path = tmp_path / "mutant.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


REFERENCE_MUTANTS = {
    "error-key-controls-success": ("if status < 300:", "if status < 300 and \"error\" not in payload:", "success"),
    "error-code-outside-enum": ("code not in enum", "False", "outside_enum"),
    "error-bool-as-integer": ("type(error[\"retryable\"]) is not bool", "type(error[\"retryable\"]) not in (bool, int)", "bad_bool"),
    "missing-error-accepted": ("if type(error) is not dict:\n        _fail(\"error object\")", "if type(error) is not dict:\n        error = {\"code\": \"not_found\", \"message\": \"missing\", \"retryable\": False}", "missing"),
    "allow-secret-key": ("key.lower() not in {\"key_material\", \"password_hash_client\", \"token\", \"secret\"}", "True", "secret"),
    "allow-chess-content": ("not TOKENS.intersection(_tokens(key))", "True", "chess"),
    "status-float-accepted": ("type(status) is not int", "type(status) not in (int, float)", "status_float"),
    "ignore-utf8": ("return type(text) is str and not any(\"\\ud800\" <= ch <= \"\\udfff\" for ch in text)", "return type(text) is str", "surrogate"),
    "max-status-off-by-one": ("status <= 599", "status <= 600", "status_600"),
    "strict-source-removed": ("strict_source_derivation(source)", "None", "source"),
    "declared-secret-exemption-removed": ("_safe_key(k, k in allowed)", "_safe_key(k)", "declared_success"),
    "error-token-exempted": ("declared = frozenset(source[\"areas\"][area][\"ops\"][name][\"response\"][\"fields\"]) if status < 300 else frozenset()", "declared = frozenset(source[\"areas\"][area][\"ops\"][name][\"response\"][\"fields\"]) if status < 300 else frozenset({\"token\"})", "error_token"),
    "depth-nine-accepted": ("depth > 8", "depth > 9", "depth"),
    "depth-eight-refused": ("depth > 8", "depth > 7", "depth"),
    "dict-257-accepted": ("len(value) <= 256 and all(_safe_key", "len(value) <= 257 and all(_safe_key", "dict_size"),
    "list-257-accepted": ("len(value) <= 256 and all(_json_safe", "len(value) <= 257 and all(_json_safe", "list_size"),
    "infinity-accepted": ('and abs(value) != float("inf")', "", "infinity"),
    "case-key-accepted": ("key.lower() not in", "key not in", "case_key"),
    "case-message-accepted": ('error["message"].lower()', 'error["message"]', "case_message"),
    "hyphenated-chess-key-accepted": ('r"[._-]"', 'r"[._]"', "hyphen"),
    "int-subclass-accepted": ("type(value) in (bool, int)", "isinstance(value, int)", "int_subclass"),
    "success-payload-validation-skipped": ("if type(payload) is not dict or not _json_safe(payload, allowed=declared):", "if status >= 400 and (type(payload) is not dict or not _json_safe(payload, allowed=declared)):", "hostile_2xx"),
    "low-surrogate-allowed": ('"\\udfff"', '"\\udbff"', "low_surrogate"),
    "extra-string-surrogate-allowed": ("return _text(value) and not _fen_placement_in(value)", "return not _fen_placement_in(value)", "extra_surrogate"),
    "key-surrogate-allowed": ("type(key) is str and _text(key)", "type(key) is str", "key_surrogate"),
    "secret-extra-allowed": ('"secret"}', '}', "secret_extra"),
    "password-extra-allowed": ('"password_hash_client", ', '', "password_extra"),
    "password-message-allowed": ('("key_material", "password_hash_client")', '("key_material",)', "password_message"),
    "message-required-dropped": ('("code", "message", "retryable")', '("code", "retryable")', "missing_message"),
    "retryable-required-dropped": ('("code", "message", "retryable")', '("code", "message")', "missing_retryable"),
    "string-error-accepted": ("if type(error) is not dict:", "if error is None:", "string_error"),
    "retryable-forced-false": ('"retryable": error["retryable"]', '"retryable": False', "retryable_true"),
    "null-extra-refused": ("if value is None or type(value) in (bool, int):", "if type(value) in (bool, int):", "json_null"),
    "nested-declared-exemption": ("_json_safe(v, depth + 1) for k, v", "_json_safe(v, depth + 1, allowed) for k, v", "nested_declared"),
    "operation-extra-segment": ('operation.split(".")', 'operation.split(".")[:2]', "operation_extra"),
    "camelcase-boundary-lost": ('re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", separated)', 'separated', "camelcase"),
    "fen-value-check-lost": ("and not _fen_placement_in(value)", "", "fen"),
    "dot-chess-key-accepted": ('r"[._-]"', 'r"[_-]"', "dot"),
    "list-fen-check-lost": ("_json_safe(v, depth + 1) for v in value", "_text(v) if type(v) is str else _json_safe(v, depth + 1) for v in value", "list_fen"),
    "rank-sum-nine": ('== FEN_PLACEMENT["rank_sum"]', '== 9', "rank_nine"),
    "rank-sum-at-most": ('== FEN_PLACEMENT["rank_sum"]', '<= FEN_PLACEMENT["rank_sum"]', "rank_seven"),
    "digit-membership-dropped": ('set(FEN_PLACEMENT["empty_run_digits"])', 'set("0123456789")', "zero_digit"),
    "split-spaces-only": ('for start in range(len(text)):', 'for start in (0,):', "punctuation"),
    "digit-camelcase-boundary-lost": ('([a-z0-9])([A-Z])', '([a-z])([A-Z])', "digit_camel"),
    "provider-key-intersection": ('{"provider", "key"} <= set(_tokens(key))', '{"provider", "key"} & set(_tokens(key))', "provider_kind"),
    "nine-ranks-accepted": ('(FEN_PLACEMENT["rank_count"] - 1)', '(FEN_PLACEMENT["rank_count"])', "nine_ranks"),
    "rank-count-less-than": ('(FEN_PLACEMENT["rank_count"] - 1)', '(FEN_PLACEMENT["rank_count"] - 2)', "seven_ranks"),
    "acronym-boundary-lost": ('re.sub(r"([A-Z]+)([A-Z][a-z])", r"\\1_\\2", key)', 'key', "acronym"),
}


def _mutant(name):
    before, after, _ = REFERENCE_MUTANTS[name]
    source = "\n".join(inspect.getsource(f) for f in (_text, _tokens, _fen_placement_in, _safe_key, _json_safe, _source, classify))
    assert source.count(before) == 1, name
    scope = dict(globals())
    exec(compile(ast.parse(source.replace(before, after, 1)), f"<reference-mutant:{name}>", "exec"), scope)
    return scope["classify"]


@pytest.mark.parametrize("name", REFERENCE_MUTANTS)
def test_reference_mutant_red(name):
    target = _mutant(name)
    source = copy.deepcopy(SOURCE)
    body = envelope()
    status = 400
    if name == "error-key-controls-success":
        status = 200
        assert classify(source, status, body) == {"kind": "success"}
        assert target(source, status, body) != {"kind": "success"}
        return
    if name == "error-code-outside-enum":
        body["error"]["code"] = "outside_enum"
    elif name == "error-bool-as-integer":
        body["error"]["retryable"] = 1
    elif name == "missing-error-accepted":
        body.pop("error")
    elif name == "allow-secret-key":
        body["error"]["key_material"] = "secret"
    elif name == "allow-chess-content":
        body["error"]["analysis"] = "private"
    elif name == "status-float-accepted":
        status = 400.0
    elif name == "ignore-utf8":
        body["error"]["message"] = "\ud800"
    elif name == "max-status-off-by-one":
        status = 600
    elif name == "low-surrogate-allowed":
        body["error"]["message"] = "\udc00"
    elif name == "extra-string-surrogate-allowed":
        body["extra"] = "\udc00"
    elif name == "key-surrogate-allowed":
        body["x\udc00"] = "bad"
    elif name == "secret-extra-allowed":
        body["secret"] = "bad"
    elif name == "password-extra-allowed":
        body["password_hash_client"] = "bad"
    elif name == "password-message-allowed":
        body["error"]["message"] = "password_hash_client leaked"
    elif name == "message-required-dropped":
        body["error"].pop("message")
        with pytest.raises(ErrorsError):
            classify(source, status, body)
        with pytest.raises(KeyError):
            target(source, status, body)
        return
    elif name == "retryable-required-dropped":
        body["error"].pop("retryable")
        with pytest.raises(ErrorsError):
            classify(source, status, body)
        with pytest.raises(KeyError):
            target(source, status, body)
        return
    elif name == "string-error-accepted":
        body["error"] = "code message retryable"
        with pytest.raises(ErrorsError):
            classify(source, status, body)
        with pytest.raises(TypeError):
            target(source, status, body)
        return
    elif name == "retryable-forced-false":
        body = envelope("provider_unavailable", "down", True)
        assert classify(source, 503, body)["retryable"] is True
        assert target(source, 503, body)["retryable"] is False
        return
    elif name == "null-extra-refused":
        body["extra"] = None
        assert classify(source, status, body)["kind"] == "error"
        with pytest.raises(ErrorsError):
            target(source, status, body)
        return
    elif name == "nested-declared-exemption":
        body = {"x": {"token": "bad"}}
        with pytest.raises(ErrorsError):
            classify(source, 200, body, "identity.login")
        assert target(source, 200, body, "identity.login")["kind"] == "success"
        return
    elif name == "camelcase-boundary-lost":
        body["gameMoves"] = "private"
    elif name == "dot-chess-key-accepted":
        body["game.moves"] = "private"
    elif name == "list-fen-check-lost":
        body["extra"] = ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"]
    elif name == "rank-sum-nine":
        body["extra"] = "8p/8p/8p/8p/8p/8p/8p/8p"
        assert classify(source, status, body)["kind"] == "error"
        with pytest.raises(ErrorsError):
            target(source, status, body)
        return
    elif name == "nine-ranks-accepted":
        body["extra"] = "8/8/8/8/8/8/8/8"
    elif name == "rank-count-less-than":
        body["extra"] = "8/8/8/8/8/8/8"
        assert classify(source, status, body)["kind"] == "error"
        with pytest.raises(ErrorsError):
            target(source, status, body)
        return
    elif name == "rank-sum-at-most":
        body["extra"] = "7/8/8/8/8/8/8/8"
        assert classify(source, status, body)["kind"] == "error"
        with pytest.raises(ErrorsError):
            target(source, status, body)
        return
    elif name == "digit-membership-dropped":
        body["extra"] = "08/8/8/8/8/8/8/8"
        assert classify(source, status, body)["kind"] == "error"
        with pytest.raises(ErrorsError):
            target(source, status, body)
        return
    elif name == "split-spaces-only":
        body["extra"] = "fen:rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
    elif name == "digit-camelcase-boundary-lost":
        body["top3Moves"] = "private"
    elif name == "provider-key-intersection":
        body["provider_kind"] = "generic-a"
        assert classify(source, status, body)["kind"] == "error"
        with pytest.raises(ErrorsError):
            target(source, status, body)
        return
    elif name == "acronym-boundary-lost":
        body["FENString"] = "private"
    elif name == "fen-value-check-lost":
        body["extra"] = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
    elif name == "operation-extra-segment":
        body = {}
        with pytest.raises(ErrorsError):
            classify(source, 200, body, "identity.login.x")
        assert target(source, 200, body, "identity.login.x")["kind"] == "success"
        return
    elif name == "declared-secret-exemption-removed":
        body = {"token": "tok-1"}
        assert classify(source, 200, body, "identity.login") == {"kind": "success"}
        with pytest.raises(ErrorsError):
            target(source, 200, body, "identity.login")
        return
    elif name == "error-token-exempted":
        body["token"] = "tok-1"
        with pytest.raises(ErrorsError):
            classify(source, 400, body, "identity.login")
        assert target(source, 400, body, "identity.login")["kind"] == "error"
        return
    elif name in ("depth-nine-accepted", "depth-eight-refused"):
        body = {"x": 1}
        for _ in range(7 if name == "depth-eight-refused" else 8):
            body = {"x": body}
        if name == "depth-eight-refused":
            assert classify(source, 200, body) == {"kind": "success"}
            with pytest.raises(ErrorsError):
                target(source, 200, body)
        else:
            with pytest.raises(ErrorsError):
                classify(source, 200, body)
            assert target(source, 200, body) == {"kind": "success"}
        return
    elif name in ("dict-257-accepted", "list-257-accepted"):
        body["extra"] = {f"x{i}": i for i in range(257)} if name == "dict-257-accepted" else list(range(257))
    elif name == "infinity-accepted":
        body["extra"] = float("inf")
    elif name == "case-key-accepted":
        body["Key_Material"] = "secret"
    elif name == "case-message-accepted":
        body["error"]["message"] = "KEY_MATERIAL=x"
    elif name == "hyphenated-chess-key-accepted":
        body["game-id"] = "private"
    elif name == "int-subclass-accepted":
        body["extra"] = type("IntChild", (int,), {})(1)
    elif name == "success-payload-validation-skipped":
        body = {"extra": float("nan")}
        with pytest.raises(ErrorsError):
            classify(source, 200, body)
        assert target(source, 200, body) == {"kind": "success"}
        return
    else:
        source["contract"]["transport"]["errors"]["shape"]["error"]["fields"]["code"]["required"] = False
    with pytest.raises(ErrorsError):
        classify(source, status, body)
    assert target(source, status, body) == {"kind": "error", "code": body.get("error", {}).get("code", "not_found"), "message": body.get("error", {}).get("message", "missing"), "retryable": body.get("error", {}).get("retryable", False)}


def test_every_declared_operation_success_example_is_allowed():
    for area, a in SOURCE["areas"].items():
        for name, op in a["ops"].items():
            # The source's examples give one valid value for every required leaf.
            def example(fields):
                out = {}
                for key, field in fields.items():
                    if field["required"]:
                        out[key] = example(field["fields"]) if field["type"] == "object" else field["example"]
                return out
            body = example(op["response"]["fields"])
            assert classify(SOURCE, 200, body, f"{area}.{name}") == {"kind": "success"}


@pytest.mark.parametrize("status,operation", [(400, "identity.register"), (200, "entitlements.get")])
def test_undeclared_token_is_refused(status, operation):
    body = envelope() if status >= 400 else {}
    body["token"] = "tok-1"
    with pytest.raises(ErrorsError):
        classify(SOURCE, status, body, operation)


@pytest.mark.parametrize("body", [{"extra": float("nan")}, {1: "bad"}])
def test_hostile_success_body_refused(body):
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, body, "entitlements.get")


@pytest.mark.parametrize("factory,allowed", [
    (lambda: {"extra": float("inf")}, False),
    (lambda: {"extra": -float("inf")}, False),
    (lambda: {"Key_Material": "secret"}, False),
    (lambda: {"TOKEN": "secret"}, False),
    (lambda: {"game-id": "private"}, False),
    (lambda: {"extra": type("IntChild", (int,), {})(1)}, False),
])
def test_refused_extra_shapes(factory, allowed):
    body = envelope()
    body.update(factory())
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, body)


def test_uppercase_secret_in_error_message_refused():
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, envelope(message="KEY_MATERIAL=x"))


@pytest.mark.parametrize("kind", ["dict", "list"])
def test_256_extra_bound_accepted_and_257_refused(kind):
    if kind == "dict":
        good = {f"x{i}": i for i in range(256)}
        bad = {f"x{i}": i for i in range(257)}
    else:
        good = list(range(256))
        bad = list(range(257))
    body = envelope()
    body["extra"] = good
    assert classify(SOURCE, 400, body)["kind"] == "error"
    body["extra"] = bad
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, body)


def test_depth_eight_accepted_nine_refused():
    # Top-level payload depth 0; last scalar at depth 8.
    body = {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": 1}}}}}}}}
    assert _json_safe(body)
    deeper = {"x": body}
    assert not _json_safe(deeper)
    success = {"extra": 0}
    cursor = success
    for _ in range(7):
        cursor["extra"] = {"x": 0}
        cursor = cursor["extra"]
    assert classify(SOURCE, 200, success)["kind"] == "success"
    cursor["x"] = {"x": 0}
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, success)


@pytest.mark.parametrize("body", [
    {"error": {"code": "not_found", "message": "\udc00", "retryable": False}},
    {"error": {"code": "not_found", "message": "ok", "retryable": False, "x": "\udc00"}},
    {"error": {"code": "not_found", "message": "ok", "retryable": False}, "x\udc00": "bad"},
    {"error": {"code": "not_found", "message": "ok", "retryable": False}, "secret": "bad"},
    {"error": {"code": "not_found", "message": "ok", "retryable": False}, "password_hash_client": "bad"},
    {"error": {"code": "not_found", "message": "password_hash_client leaked", "retryable": False}},
    {"error": {"code": "not_found", "message": "ok"}},
    {"error": {"code": "not_found", "retryable": False}},
    {"error": "code message retryable"},
    {"error": {"code": "not_found", "message": "ok", "retryable": False}, "x": {"token": "bad"}},
])
def test_v2_malformed_error_shapes_are_typed(body):
    with pytest.raises(ErrorsError) as e:
        classify(SOURCE, 400, body)
    assert e.value.__cause__ is None and e.value.__context__ is None


def test_retryable_true_preserved():
    assert classify(SOURCE, 503, envelope("provider_unavailable", "down", True))["retryable"] is True


def test_json_null_extra_allowed():
    body = envelope()
    body["x"] = None
    assert classify(SOURCE, 400, body)["kind"] == "error"


def test_declared_exemption_is_top_level_only():
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, {"x": {"token": "leak"}}, "identity.login")


def test_overlong_operation_refused():
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, {}, "identity.login.x")


@pytest.mark.parametrize("key", ("gameMoves", "providerKey", "GameMoves", "FENString", "GAMEMoves", "provider.key", "game.moves", "game.id", "top3Moves"))
def test_camelcase_chess_or_secret_tokens_refused(key):
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, {"error": envelope()["error"], key: "private"})


@pytest.mark.parametrize("message", (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
))
def test_fen_placement_in_message_refused(message):
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, envelope(message=message))


def test_fen_placement_nested_extra_refused_and_seven_rank_near_miss_allowed():
    placement = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
    body = envelope()
    body["extra"] = {"detail": ["x", placement]}
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, body)
    body["extra"]["detail"][1] = "/".join(placement.split("/")[:7])
    assert classify(SOURCE, 400, body)["kind"] == "error"


@pytest.mark.parametrize("body,status,operation", [
    ({"extra": "prefixrnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR suffix"}, 400, None),
    ({"extra": ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"]}, 400, None),
    ({"future_field": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"}, 200, "identity.login"),
    ({"token": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"}, 200, "identity.login"),
])
def test_fen_any_response_string_value_refused(body, status, operation):
    with pytest.raises(ErrorsError):
        classify(SOURCE, status, body, operation)


def test_clean_declared_success_response_passes():
    assert classify(SOURCE, 200, {"token": "tok-1"}, "identity.login") == {"kind": "success"}


@pytest.mark.parametrize("placement", ["8/8/8/8/8/8/8", "8p/8p/8p/8p/8p/8p/8p/8p", "7/8/8/8/8/8/8/8", "08/8/8/8/8/8/8/8", "0p7/8/8/8/8/8/8/8"])
def test_fen_near_misses_pass(placement):
    assert classify(SOURCE, 200, {"future_field": placement}, "identity.login") == {"kind": "success"}


@pytest.mark.parametrize("prefix", ["fen:", "'", '"', ",", "glued"])
def test_fen_adjacent_punctuation_or_glued_prefix_refused(prefix):
    body = {"extra": prefix + "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"}
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, body)


def test_nine_ranks_containing_valid_eight_window_refused():
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, {"extra": "8/8/8/8/8/8/8/8/8"})


def test_error_extra_provider_kind_is_safe():
    body = envelope()
    body["provider_kind"] = "generic-a"
    assert classify(SOURCE, 400, body)["kind"] == "error"
