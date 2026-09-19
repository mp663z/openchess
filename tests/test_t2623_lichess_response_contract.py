"""T2623: lichess response contract battery - the lint must PROVE the
normative content of data/contracts/lichess_response.yaml by exact
structured comparison. Every rule family gets contradiction AND
reversal mutations; typed leaves get the bool/int conflation
treatment; the YAML-unquoted-numeric-key family must surface as
ContractError, never a raw TypeError. A mutation that passes is a
hole."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.lichess_response_contract_lint import CONTRACT, lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load(CONTRACT.read_text())


def _mut(fn, where: str):
    doc = copy.deepcopy(DOC)
    if where == "top-c":
        fn(doc["contract"])
    else:
        fn(doc["contract"] if where.startswith("contract") else doc)
    return doc


def _bad(fn, where: str = "contract"):
    doc = _mut(fn, where)
    with pytest.raises(ContractError) as ei:
        lint(doc)
    assert str(ei.value).startswith("lichess response contract:"), str(ei.value)
    return str(ei.value)


def test_real_contract_lints_clean():
    lint(copy.deepcopy(DOC))


def test_unknown_keys_rejected_at_every_level():
    _bad(lambda d: d.__setitem__("bogus", 1), "top")
    _bad(lambda d: d["endpoint"].__setitem__("bogus", 1))
    _bad(lambda d: d["endpoint"]["auth"].__setitem__("bogus", 1))
    _bad(lambda d: d["endpoint"]["response_format"].__setitem__("bogus", 1))
    _bad(lambda d: d["request"].__setitem__("bogus", 1))
    _bad(lambda d: d["request"]["params"]["max"].__setitem__("bogus", 1))
    _bad(lambda d: d["game_object"].__setitem__("bogus", 1))
    _bad(lambda d: d["error_envelope"].__setitem__("bogus", 1))
    _bad(lambda d: d["error_envelope"]["statuses"]["429"].__setitem__("bogus", 1))


def test_schema_version_exact_int():
    for bad in (1.0, "1", True, 2, None):
        _bad(lambda d, b=bad: d.__setitem__("schema_version", b), "top")


def test_contract_id_exact():
    _bad(lambda d: d.__setitem__("id", "lichess"))
    _bad(lambda d: d.__setitem__("id", "lichess-response-v2"))


def test_endpoint_identity_exact():
    _bad(lambda d: d["endpoint"].__setitem__("path", "/api/games/user"))
    _bad(lambda d: d["endpoint"].__setitem__("method", "POST"))
    _bad(lambda d: d["endpoint"]["auth"].__setitem__("spec_security", "none"))
    _bad(lambda d: d["endpoint"]["auth"].__setitem__("anonymous_observed", "works"))
    _bad(lambda d: d["endpoint"]["auth"]["throttles"]
         .__setitem__("anonymous", "60-games-per-second"))
    _bad(lambda d: d["endpoint"]["auth"]["throttles"].__delitem__("oauth2-own-games"))


def test_response_format_exact():
    _bad(lambda d: d["endpoint"]["response_format"].__setitem__("stream", "json-array"))
    # reversal
    _bad(lambda d: d["endpoint"]["response_format"].__setitem__("one_object_per_line", False))
    # conflation
    _bad(lambda d: d["endpoint"]["response_format"].__setitem__("one_object_per_line", 1))
    _bad(lambda d: d["endpoint"]["response_format"].__setitem__("one_object_per_line", "true"))


def test_request_params_exact():
    _bad(lambda d: d["request"]["params"].__delitem__("since"))  # dropped incremental cursor
    _bad(lambda d: d["request"]["params"]
         .__setitem__("bogus_param", {"type": "string", "required": False}))
    _bad(lambda d: d["request"]["params"]["max"].__setitem__("min", 0))  # zero-max allowed
    _bad(lambda d: d["request"]["params"]["max"].__delitem__("min"))
    _bad(lambda d: d["request"]["params"]["moves"].__setitem__("default", False))  # default flip
    _bad(lambda d: d["request"]["params"]["moves"].__setitem__("default", 1))  # conflation
    _bad(lambda d: d["request"]["params"]["moves"].__setitem__("required", True))  # made required
    _bad(lambda d: d["request"]["params"]["color"].__setitem__("enum", ["white"]))  # shrink
    _bad(lambda d: d["request"]["params"]["sort"].__setitem__("default", "dateAsc"))
    # ms precision lost
    _bad(lambda d: d["request"]["params"]["since"].__setitem__("type", "integer"))
    _bad(lambda d: d["request"]["params"]["perfType"].__setitem__("type", "string"))  # csv lost


def test_game_object_exact():
    _bad(lambda d: d["game_object"]["required_fields"].remove("status"))
    _bad(lambda d: d["game_object"]["required_fields"].append("winner"))  # winner is optional
    _bad(lambda d: d["game_object"]["required_fields"].reverse())
    _bad(lambda d: d["game_object"]["optional_fields"].remove("moves"))
    _bad(lambda d: d["game_object"]["optional_fields"].append("analysisUrl"))
    # reversal
    _bad(lambda d: d["game_object"].__setitem__("unknown_field_policy", "reject-unknown"))
    _bad(lambda d: d["game_object"]["field_types"].__setitem__("rated", "string"))  # type drift
    # int64 lost
    _bad(lambda d: d["game_object"]["field_types"].__setitem__("createdAt", "integer"))
    _bad(lambda d: d["game_object"]["field_types"].__delitem__("status"))


def test_status_values_exact_and_ordered():
    _bad(lambda d: d["game_object"]["status_values"].remove("cheat"))
    _bad(lambda d: d["game_object"]["status_values"].append("resigned"))
    _bad(lambda d: d["game_object"]["status_values"].reverse())
    _bad(lambda d: d["game_object"]["status_values"].__setitem__(3, "checkmate"))  # rename


def test_error_envelope_exact():
    _bad(lambda d: d["error_envelope"]["shape"].__setitem__("error", "object"))
    _bad(lambda d: d["error_envelope"]["shape"].__setitem__("message", "string"))  # extra key
    _bad(lambda d: d["error_envelope"].__setitem__("observed", "not-verified"))
    # storm
    _bad(lambda d: d["error_envelope"]["statuses"]["429"].__setitem__("retry", "retry-immediately"))
    _bad(lambda d: d["error_envelope"]["statuses"]["429"].__delitem__("retry"))
    # misroute
    _bad(lambda d: d["error_envelope"]["statuses"]["404"].__setitem__("class", "rate_limited"))
    _bad(lambda d: d["error_envelope"]["statuses"].__delitem__("5xx"))


def test_unquoted_numeric_status_key_is_contract_error():
    """YAML parses an unquoted 404 as an INT key. The lint must reject
    it as a ContractError, never crash with a raw TypeError."""
    def unquote(d):
        st = d["error_envelope"]["statuses"]
        st[404] = st.pop("404")
    _bad(unquote)


def test_failure_classes_and_mapping_exact():
    _bad(lambda d: d["failure_classes"].reverse())
    _bad(lambda d: d["failure_classes"].remove("auth_required"))
    _bad(lambda d: d["failure_classes"].append("orphan_class"))

    def undeclared_error(d):
        d["failure_mapping"]["rate_limited"]["error"] = "not_in_enum"
    _bad(undeclared_error)

    def wrong_error(d):
        d["failure_mapping"]["unknown_status_value"]["error"] = "source_unavailable"
    _bad(wrong_error)

    def trigger_changed(d):
        d["failure_mapping"]["rate_limited"]["trigger"] = "http-404"
    _bad(trigger_changed)


def test_error_enum_and_shape_exact():
    _bad(lambda d: d["errors"]["closed_enum"].remove("auth_required"))
    _bad(lambda d: d["errors"]["closed_enum"].append("illegal_move"))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["retryable"]
         .__setitem__("type", "string"))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["code"]
         .__setitem__("required", False))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["code"]
         .__setitem__("required", 1))  # bool/int conflation


def test_versioning_exact():
    _bad(lambda d: d["versioning"].__setitem__("base_path", "/lichess-response/v2"))
    _bad(lambda d: d["versioning"].__setitem__("client_pin", "MINOR"))
    _bad(lambda d: d["versioning"].__setitem__("minor_policy", "subtractive-allowed"))
    _bad(lambda d: d["versioning"]["minor_additions"].append("breaking-changes"))
    _bad(lambda d: d["versioning"].__setitem__("downgrade_policy", "no-downgrade"))
    _bad(lambda d: d["versioning"].__setitem__("major_bump", "same-base-path"))
    _bad(lambda d: d["versioning"].__delitem__("client_pin"))
    _bad(lambda d: d["versioning"].__setitem__("bogus", 1))
    _bad(lambda d: d["versioning"].__delitem__("rule"))
    _bad(lambda d: d["versioning"].__setitem__("rule", ""))


def test_documentation_fields_nonempty():
    _bad(lambda d: d["endpoint"]["auth"].__setitem__("rule", ""))
    _bad(lambda d: d["request"].__setitem__("rule", "  "))
    _bad(lambda d: d["game_object"].__setitem__("rule", 7))
    _bad(lambda d: d["error_envelope"].__setitem__("rule", ""))


def test_missing_and_wrong_container_family():
    """The whole malformed-SHAPE family converts to ContractError with
    the advertised prefix - no raw KeyError/AttributeError escapes."""
    cases = [
        lambda d: d["contract"].__delitem__("endpoint"),
        lambda d: d["contract"]["endpoint"].__delitem__("auth"),
        lambda d: d["contract"]["endpoint"].__delitem__("response_format"),
        lambda d: d["contract"].__delitem__("request"),
        lambda d: d["contract"]["request"].__delitem__("params"),
        lambda d: d["contract"]["request"].__setitem__("params", []),
        lambda d: d["contract"].__delitem__("game_object"),
        lambda d: d["contract"]["game_object"].__delitem__("status_values"),
        lambda d: d["contract"].__delitem__("error_envelope"),
        lambda d: d["contract"]["error_envelope"].__delitem__("statuses"),
        lambda d: d["contract"].__delitem__("errors"),
        lambda d: d["contract"].__delitem__("failure_mapping"),
        lambda d: d["contract"].__delitem__("versioning"),
        lambda d: d.__setitem__("contract", []),
        lambda d: d["contract"].__setitem__("game_object", "ndjson"),
    ]
    for fn in cases:
        doc = copy.deepcopy(DOC)
        fn(doc)
        with pytest.raises(ContractError) as ei:
            lint(doc)
        assert str(ei.value).startswith("lichess response contract:"), str(ei.value)


def test_cli_boundary_clean_and_failing():
    ok = subprocess.run([sys.executable, "tools/lichess_response_contract_lint.py"],
                        cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0 and "OK lichess response contract lint" in ok.stdout
    bad_doc = copy.deepcopy(DOC)
    bad_doc["contract"]["game_object"]["status_values"].remove("cheat")
    bad_path = ROOT / "data" / "contracts" / ".tmp_bad_lichess_response.yaml"
    try:
        bad_path.write_text(yaml.safe_dump(bad_doc))
        fail = subprocess.run(
            [sys.executable, "tools/lichess_response_contract_lint.py", str(bad_path)],
            cwd=ROOT, capture_output=True, text=True)
        assert fail.returncode == 1
        assert "lichess response contract:" in fail.stderr
        assert "Traceback" not in fail.stderr
    finally:
        bad_path.unlink(missing_ok=True)


def test_swept_prose_claims_now_structured():
    """Post-sweep: every normative claim that used to live only in a
    rule string is an exactly-compared structured field with
    contradiction and reversal mutations."""
    _bad(lambda d: d["endpoint"]["auth"]["anonymous_fallback"]
         .__setitem__("policy", "retry-storm-allowed"))
    _bad(lambda d: d["endpoint"]["auth"]["anonymous_fallback"]
         .__setitem__("retry_storm", "allowed"))  # reversal
    _bad(lambda d: d["endpoint"]["auth"].__delitem__("anonymous_fallback"))
    _bad(lambda d: d["endpoint"]["response_format"]["malformed_line"]
         .__setitem__("policy", "abort-stream"))  # reversal
    _bad(lambda d: d["endpoint"]["response_format"]["malformed_line"]
         .__setitem__("effect", "aborts-parsed-records"))
    _bad(lambda d: d["endpoint"]["response_format"].__delitem__("malformed_line"))
    _bad(lambda d: d["request"].__setitem__("incremental_params", ["until"]))  # since lost
    _bad(lambda d: d["request"].__setitem__("incremental_params", ["since", "until", "max"]))
    _bad(lambda d: d["request"].__delitem__("incremental_params"))
    _bad(lambda d: d["game_object"]["required_violation"]
         .__setitem__("partial_game", "allowed"))  # reversal
    _bad(lambda d: d["game_object"]["required_violation"]
         .__setitem__("error", "source_unavailable"))  # misroute
    _bad(lambda d: d["game_object"].__setitem__("unknown_field_storage",
                                                "stored-for-later"))  # reversal
    _bad(lambda d: d["game_object"]["status_violation"]
         .__setitem__("effect", "pass-through"))
    _bad(lambda d: d["game_object"].__delitem__("required_violation"))
    _bad(lambda d: d["game_object"].__delitem__("unknown_field_storage"))
    _bad(lambda d: d["game_object"].__delitem__("status_violation"))
    _bad(lambda d: d["error_envelope"]["statuses"]["404"]
         .__setitem__("scope", "per-game-failure"))  # reversal
    _bad(lambda d: d["error_envelope"]["statuses"]["404"].__delitem__("scope"))
    _bad(lambda d: d["error_envelope"]["statuses"]["5xx"]
         .__setitem__("scope", "endpoint-level"))


def test_request_headers_and_format_consistency():
    """The PGN-default hole (v2): without an exact Accept header the
    endpoint returns PGN while the contract pins NDJSON parsing."""
    # the exact verifier attack: Accept PGN, stream still ndjson
    _bad(lambda d: d["request"]["headers"]["Accept"]
         .__setitem__("value", "application/x-chess-pgn"))
    # header missing entirely (spec default is PGN)
    _bad(lambda d: d["request"].__delitem__("headers"))
    # Accept not required
    _bad(lambda d: d["request"]["headers"]["Accept"]
         .__setitem__("required", False))
    _bad(lambda d: d["request"]["headers"]["Accept"]
         .__setitem__("required", 1))  # bool/int conflation
    # extra smuggled header
    _bad(lambda d: d["request"]["headers"]
         .__setitem__("Authorization", {"required": False, "value": "x"}))
    # ndjson-only param family: dropped list, shrunk, renamed
    _bad(lambda d: d["request"].__delitem__("ndjson_only_params"))
    _bad(lambda d: d["request"]
         .__setitem__("ndjson_only_params", ["pgnInJson", "lastFen"]))
    _bad(lambda d: d["request"]["ndjson_only_params"].append("sort"))
    # params/header family split: pgnInJson dropped from params but
    # still claimed ndjson-only
    def drop_param(d):
        del d["request"]["params"]["pgnInJson"]
    _bad(drop_param)


def test_spec_provenance_exact():
    _bad(lambda d: d.__delitem__("spec_provenance"), "top-c")
    _bad(lambda d: d["spec_provenance"]
         .__setitem__("repo_head", "0" * 40), "top-c")
    _bad(lambda d: d["spec_provenance"]
         .__setitem__("endpoint_spec_last_commit", "2026-09-01"), "top-c")
    _bad(lambda d: d["spec_provenance"].__setitem__("bogus", 1), "top-c")


def test_request_boundary_constraints():
    """Boundary family (v3): spec-minimum timestamps and the closed
    PerfType enum are exactly pinned."""
    # minimum deleted / lowered / raised / wrong type
    _bad(lambda d: d["request"]["params"]["since"].__delitem__("min"))
    _bad(lambda d: d["request"]["params"]["since"]
         .__setitem__("min", 1356998400069))
    _bad(lambda d: d["request"]["params"]["until"]
         .__setitem__("min", 1356998400071))
    _bad(lambda d: d["request"]["params"]["until"]
         .__setitem__("min", "1356998400070"))
    _bad(lambda d: d["request"]["params"]["since"]
         .__setitem__("min", True))  # bool is not the integer min
    # perfType enum deleted / shrunk / extended / reordered
    _bad(lambda d: d["request"]["params"]["perfType"].__delitem__("enum"))
    _bad(lambda d: d["request"]["params"]["perfType"]["enum"].remove("atomic"))
    _bad(lambda d: d["request"]["params"]["perfType"]["enum"]
         .append("garbage"))
    _bad(lambda d: d["request"]["params"]["perfType"]["enum"].reverse())
    _bad(lambda d: d["request"]["params"]["perfType"]
         .__setitem__("enum", "bullet"))
    # csv semantics retained: type flip rejected
    _bad(lambda d: d["request"]["params"]["perfType"]
         .__setitem__("type", "string"))


def test_game_object_full_schema_boundary():
    """Whole-GameJson sweep (v4): closed enums, optional type map,
    structural players delegation - all exactly pinned."""
    g = "game_object"
    # variant/speed closed enums
    _bad(lambda d: d[g]["variant_values"].append("garbage"))
    _bad(lambda d: d[g]["variant_values"].remove("fromPosition"))
    _bad(lambda d: d[g]["variant_values"].reverse())
    _bad(lambda d: d[g].__delitem__("variant_values"))
    _bad(lambda d: d[g]["speed_values"].remove("correspondence"))
    _bad(lambda d: d[g]["speed_values"].append("hyperBullet"))
    _bad(lambda d: d[g]["variant_violation"].__setitem__("effect", "warn"))
    _bad(lambda d: d[g]["speed_violation"]
         .__setitem__("error", "illegal_position"))
    _bad(lambda d: d[g].__delitem__("speed_violation"))
    # optional type map: wrong types, bogus field, missing field
    _bad(lambda d: d[g]["optional_field_types"].__setitem__("moves", []))
    _bad(lambda d: d[g]["optional_field_types"]
         .__setitem__("daysPerTurn", "string"))
    _bad(lambda d: d[g]["optional_field_types"]
         .__setitem__("clocks", {"type": "array", "items": "string"}))
    _bad(lambda d: d[g]["optional_field_types"]
         .__setitem__("winner", {"type": "string", "enum": ["white"]}))
    _bad(lambda d: d[g]["optional_field_types"]
         .__setitem__("bogusField", "string"))
    _bad(lambda d: d[g]["optional_field_types"].__delitem__("pgn"))
    _bad(lambda d: d[g]["optional_field_types"]["opening"]["required"]
         .remove("ply"))
    _bad(lambda d: d[g]["optional_field_types"]["clock"]["field_types"]
         .__setitem__("totalTime", "string"))
    _bad(lambda d: d[g]["optional_field_types"]["analysis"]
         .__setitem__("item_field_validation", "pinned-here"))
    _bad(lambda d: d[g].__delitem__("optional_field_types"))
    # players shape: delegation must be structural, not implicit
    _bad(lambda d: d[g]["players_shape"]
         .__setitem__("nested_field_validation", "implicit"))
    _bad(lambda d: d[g]["players_shape"]["required"].remove("black"))
    _bad(lambda d: d[g]["players_shape"]
         .__setitem__("side_shape", "always-user"))
    _bad(lambda d: d[g]["players_shape"]["user_required"]
         .remove("rating"))
    _bad(lambda d: d[g]["players_shape"]
         .__setitem__("ai_required", []))
    _bad(lambda d: d[g].__delitem__("players_shape"))
