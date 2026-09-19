"""T0053 behavior suite (the T0052 red suite turned green): every
T0051 turn fixture case executed against the turn runtime
(tools/turn_runtime). Collected by the default gate.

Malformed and rollback expectations pin the FULL error shape, not just
the failure class: the code comes from the contract's failure_mapping,
retryable is the exact bool False, and the message is a nonempty
string. The class->code expectation is derived from the linted
contract document, and the runtime's own mapping is checked AGAINST it,
so a runtime mutation cannot self-confirm."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "fixtures" / "turn" / "cases.json").read_text())
HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]


def _rt():
    from tools import turn_runtime

    return turn_runtime


@pytest.mark.parametrize("case", HAPPY, ids=[c["name"] for c in HAPPY])
def test_happy(case):
    rt = _rt()
    got = rt.apply_move(case["state"], case["move"])
    assert got == case["expect_state"]
    assert got["side_to_move"] != case["state"]["side_to_move"]


@pytest.mark.parametrize("case", BOUNDARY, ids=[c["name"] for c in BOUNDARY])
def test_boundary(case):
    rt = _rt()
    if case["kind"] == "threshold":
        got = rt.apply_move(case["state"], case["move"])
        assert got == case["expect_state"]
        status = rt.termination_for(got)
        assert status is not None, f"{case['name']}: no termination available"
        assert status["state"] == case["expect_termination_available"]
        assert status["automatic"] is case["expect_automatic"]
    elif case["kind"] == "transition":
        got = rt.apply_move(case["state"], case["move"])
        assert got == case["expect_state"]
    else:
        same = rt.identity(case["state"]) == rt.identity(case["other"])
        if case["expect_identity"] == "identical":
            assert same
        else:
            assert not same


def _assert_error_shape(rt, err, failure_class: str) -> None:
    """Full contract error shape: code from the CONTRACT's
    failure_mapping (not the runtime's), failure_class as declared,
    retryable the exact bool False, message a nonempty string."""
    import yaml

    doc = yaml.safe_load((ROOT / "data" / "contracts" / "turn.yaml").read_text())
    expect_code = doc["contract"]["failure_mapping"][failure_class]["error"]
    assert expect_code in doc["contract"]["errors"]["closed_enum"]
    assert err.code == expect_code
    assert err.failure_class == failure_class
    assert type(err.retryable) is bool and err.retryable is False
    assert type(err.args[0]) is str and err.args[0].strip()
    assert str(err) == err.args[0]


@pytest.mark.parametrize("case", MALFORMED, ids=[c["name"] for c in MALFORMED])
def test_malformed(case):
    rt = _rt()
    with pytest.raises(rt.TurnError) as excinfo:
        rt.apply_move(case["state"], case["move"], case.get("terminated"))
    _assert_error_shape(rt, excinfo.value, case["expect_failure"])


@pytest.mark.parametrize("case", ROLLBACK, ids=[c["name"] for c in ROLLBACK])
def test_rollback(case):
    rt = _rt()
    with pytest.raises(rt.TurnError) as excinfo:
        rt.apply_move(case["state"], case["move"], case.get("terminated"))
    _assert_error_shape(rt, excinfo.value, case["expect_failure"])
    assert case["state"] == case["expect_state_after"], (
        f"{case['name']}: rejected transition mutated the input state")


def test_turn_error_shape_enforced():
    rt = _rt()
    with pytest.raises(ValueError):
        rt.TurnError(code="malformed_request", message="m", retryable=1)
    with pytest.raises(ValueError):
        rt.TurnError(code="malformed_request", message="")
    with pytest.raises(ValueError):
        rt.TurnError(code="malformed_request", message=42)
    with pytest.raises(ValueError):
        rt.TurnError(code="nope", message="m")
    with pytest.raises(ValueError):
        rt.TurnError(code="malformed_request", message="m", failure_class="nope")
    err = rt.TurnError(code="internal", message="m", retryable=True)
    assert err.retryable is True and err.failure_class is None


def test_identity_and_termination_validate_first():
    rt = _rt()
    for bad in (
        {"halfmove_clock": 0, "fullmove_number": 1},
        {"side_to_move": "white", "halfmove_clock": 0, "fullmove_number": 1},
        {"side_to_move": "w", "halfmove_clock": True, "fullmove_number": 1},
        {"side_to_move": "w", "halfmove_clock": 0, "fullmove_number": 0},
        {"side_to_move": "w", "halfmove_clock": 0},
    ):
        with pytest.raises(rt.TurnError):
            rt.identity(bad)
        with pytest.raises(rt.TurnError):
            rt.termination_for(bad)
    assert rt.termination_for({"side_to_move": "w", "halfmove_clock": 99,
                               "fullmove_number": 1}) is None
    assert rt.identity({"side_to_move": "b", "halfmove_clock": 150,
                        "fullmove_number": 76}) == {"side_to_move": "b"}


def test_input_state_never_mutated_on_success():
    rt = _rt()
    case = HAPPY[0]
    before = dict(case["state"])
    rt.apply_move(case["state"], case["move"])
    assert case["state"] == before
