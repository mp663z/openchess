"""T2624: lichess response implementation - expected and adjacent-wrong
behavior. The module reads every rule from data/contracts/
lichess_response.yaml; these tests pin that contract-driven behavior."""

from __future__ import annotations

import json

import pytest
import yaml

from importers.lichess import response as r

DOC = yaml.safe_load(
    (r.ROOT / "data" / "contracts" / "lichess_response.yaml").read_text())
C = DOC["contract"]


def _game(**over):
    g = {
        "id": "abc123XY", "rated": True, "variant": "standard",
        "speed": "blitz", "perf": "blitz",
        "createdAt": 1758000000000, "lastMoveAt": 1758000060000,
        "status": "mate",
        "players": {"white": {"user": {"name": "a"}, "rating": 2000},
                    "black": {"user": {"name": "b"}, "rating": 1990}},
    }
    g.update(over)
    return g


def test_contract_premises_loaded_from_yaml():
    assert r.ACCEPT_HEADER == "application/x-ndjson"
    assert r.ENDPOINT_METHOD == "GET"
    assert r.PARAMS["since"]["min"] == 1356998400070
    assert len(r.PARAMS["perfType"]["enum"]) == 14
    assert set(r.FAILURE_MAPPING) == set(C["failure_mapping"])
    assert C["game_object"]["required_fields"] == r.REQUIRED_FIELDS


def test_build_request_expected():
    req = r.build_request("german11", since=1356998400070,
                          until=1758000000000, max=100,
                          perfType=["blitz", "rapid"], moves=True,
                          sort="dateAsc")
    assert req["headers"] == {"Accept": "application/x-ndjson"}
    assert req["method"] == "GET"
    assert req["path"] == "/api/games/user/german11"
    assert req["params"]["since"] == "1356998400070"
    assert req["params"]["perfType"] == "blitz,rapid"
    assert req["params"]["moves"] == "true"


def test_build_request_adjacent_wrong():
    with pytest.raises(r.ContractViolation):  # undeclared param
        r.build_request("german11", bogus=1)
    with pytest.raises(r.ContractViolation):  # below spec minimum
        r.build_request("german11", since=1356998400069)
    with pytest.raises(r.ContractViolation):  # bool is not the integer
        r.build_request("german11", since=True)
    with pytest.raises(r.ContractViolation):  # string is not boolean
        r.build_request("german11", moves="yes")
    with pytest.raises(r.ContractViolation):  # bogus enum value
        r.build_request("german11", perfType=["garbage"])
    with pytest.raises(r.ContractViolation):  # one bad in csv list
        r.build_request("german11", perfType=["blitz", "alsoGarbage"])
    with pytest.raises(r.ContractViolation):  # sort enum closed
        r.build_request("german11", sort="newest")
    with pytest.raises(r.ContractViolation):  # empty username
        r.build_request("  ")


def test_validate_game_expected_projection():
    stored = r.validate_game(_game(
        winner="white", moves="e4 e5", daysPerTurn=2,
        clocks=[300, 240], futureField={"x": 1}))
    assert stored["id"] == "abc123XY"
    assert stored["winner"] == "white"
    assert stored["moves"] == "e4 e5"
    assert "futureField" not in stored  # tolerated, never stored
    assert set(stored) <= (set(r.REQUIRED_FIELDS)
                           | set(r.OPTIONAL_FIELD_TYPES))


@pytest.mark.parametrize("mutation", [
    lambda g: g.pop("status"),                      # required missing
    lambda g: g.__setitem__("rated", "yes"),        # mistyped required
    lambda g: g.__setitem__("createdAt", True),     # bool not int64
    lambda g: g.__setitem__("variant", "garbage"),  # variant enum
    lambda g: g.__setitem__("speed", "hyperBullet"),  # speed enum
    lambda g: g.__setitem__("status", "resigned"),  # status enum
    lambda g: g["players"].pop("black"),            # players side missing
    lambda g: g.__setitem__("players", []),         # players not object
    lambda g: g["players"].__setitem__("white", {}),  # neither shape
    lambda g: g["players"]["white"].pop("rating"),  # partial user shape
    lambda g: g["players"]["white"]
              .__setitem__("aiLevel", 3),           # both shapes (oneOf)
    lambda g: g.__setitem__("opening", {}),         # required members absent
    lambda g: g.__setitem__("opening", {"eco": "C50", "name": "x"}),
    lambda g: g.__setitem__("clock", {}),
    lambda g: g.__setitem__("clock", {"initial": 300, "increment": 2}),
    lambda g: g.__setitem__("moves", ["e4"]),       # optional mistyped
    lambda g: g.__setitem__("daysPerTurn", "x"),
    lambda g: g.__setitem__("winner", "draw"),      # winner enum
    lambda g: g.__setitem__("clocks", {}),
    lambda g: g.__setitem__("clock", {"initial": "x"}),
])
def test_validate_game_adjacent_wrong(mutation):
    g = _game()
    mutation(g)
    with pytest.raises(r.ContractViolation):
        r.validate_game(g)


INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1


@pytest.mark.parametrize("field_name", ["createdAt", "lastMoveAt"])
@pytest.mark.parametrize("value,ok", [
    (INT64_MIN, True), (INT64_MAX, True),          # exact bounds accepted
    (INT64_MIN - 1, False), (INT64_MAX + 1, False),  # one past rejected
    (True, False), ("1758000000000", False), (1.5, False),
])
def test_int64_bounds(field_name, value, ok):
    g = _game(**{field_name: value})
    if ok:
        assert r.validate_game(g)["id"] == "abc123XY"
    else:
        with pytest.raises(r.ContractViolation):
            r.validate_game(g)


def test_ordinary_integer_unbounded():
    """Plain contract integers (daysPerTurn, clock members) stay
    intentionally unbounded - only integer-int64 is bounded."""
    g = _game(daysPerTurn=2 ** 70)
    assert r.validate_game(g)["daysPerTurn"] == 2 ** 70


def test_parse_stream_fail_closed_per_record():
    lines = [
        json.dumps(_game(id="g1")),
        "not json at all",
        json.dumps(_game(id="g2", variant="garbage")),
        json.dumps(_game(id="g3")),
    ]
    res = r.parse_stream(lines)
    assert [g["id"] for g in res.games] == ["g1", "g3"]  # never aborts
    assert len(res.record_errors) == 2
    assert all(e.error.code == "malformed_request" for e in res.record_errors)
    assert [e.line_no for e in res.record_errors] == [2, 3]
    assert all(e.error.retryable is False for e in res.record_errors)


ENVELOPE = '{"error": "not found"}'


def test_map_http_error_expected():
    e404 = r.map_http_error(404, ENVELOPE)
    assert e404.code == "source_unavailable" and e404.retryable is False
    e429 = r.map_http_error(429, ENVELOPE)
    assert e429.code == "rate_limited" and e429.retryable is True
    assert r.RETRY_AFTER_429_SECONDS >= 60
    e503 = r.map_http_error(503, ENVELOPE)
    assert e503.code == "source_unavailable" and e503.retryable is True
    with pytest.raises(r.ContractViolation):  # unmapped status
        r.map_http_error(418, ENVELOPE)


BAD_BODIES = [
    "",                              # empty
    "not json",                      # malformed JSON
    '["error"]',                     # wrong container
    '{"message": "x"}',              # missing error member
    '{"error": 7}',                  # non-string error
    '{"error": true}',               # bool error
    '{"error": "x", "extra": 1}',    # extra member
    '{}',                            # empty object
]


@pytest.mark.parametrize("status", [404, 429, 503])
@pytest.mark.parametrize("body", BAD_BODIES)
def test_map_http_error_malformed_envelope(status, body):
    """Every status family validates the body against the pinned
    envelope shape; a malformed envelope is a malformed_response,
    never silently mapped to the status class."""
    e = r.map_http_error(status, body)
    assert e.code == "malformed_request"
    assert e.retryable is False
