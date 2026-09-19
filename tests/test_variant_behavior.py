"""T0044 behavior suite (the T0043 red suite turned green): every
T0042 fixture case executed against the variant runtime
(tools/variant_runtime). Collected by the default gate.

Malformed expectations pin the FULL error shape, not just the failure
class: grammatical FEN failures map to malformed_request, semantic
(illegal position) failures to illegal_position, retryable is the exact
bool False, and the message is a nonempty string. Unknown-variant
rollback pins failure_class None + retryable False. These expectations
are pinned HERE, not read from the runtime, so a runtime mutation
cannot self-confirm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "fixtures" / "variant" / "cases.json").read_text())
HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]

DEFERRED = [c for c in BOUNDARY if "deferred" in c]
ACTIVE_BOUNDARY = [c for c in BOUNDARY if "deferred" not in c]
UNKNOWN_VARIANT = [c for c in ROLLBACK if c["kind"] == "unknown-variant"]
ADDITIVE = [c for c in ROLLBACK if c["kind"] == "additive-fields"]

# Pinned class -> code mapping (contract errors.closed_enum +
# registry_rule: grammatical failures are request-shape errors; a
# nonsensical-but-grammatical position is its own code).
CODE_FOR_CLASS = {"illegal_position": "illegal_position"}
DEFAULT_CODE = "malformed_request"


def _rt():
    from tools import variant_runtime

    return variant_runtime


def _expect_code(failure_class: str) -> str:
    return CODE_FOR_CLASS.get(failure_class, DEFAULT_CODE)


def _assert_error_shape(err, code: str, failure_class) -> None:
    assert err.code == code
    assert err.failure_class == failure_class
    assert type(err.retryable) is bool and err.retryable is False
    assert type(err.args[0]) is str and err.args[0].strip()
    assert str(err) == err.args[0]


@pytest.mark.parametrize("case", HAPPY, ids=[c["name"] for c in HAPPY])
def test_happy(case):
    rt = _rt()
    position = rt.parse_position(case["variant"], case["fen"])
    assert type(position) is rt.Position
    assert rt.identity(position) == case["expect_identity"]


@pytest.mark.parametrize("case", ACTIVE_BOUNDARY, ids=[c["name"] for c in ACTIVE_BOUNDARY])
def test_boundary(case):
    rt = _rt()
    ident_a, ident_b = (
        rt.identity(rt.parse_position(r["variant"], r["fen"])) for r in case["pair"]
    )
    if case["expect"] == "identical":
        assert ident_a == ident_b
    else:
        diffs = {f for f in ident_a if ident_a[f] != ident_b[f]}
        assert diffs == set(case["changed_fields"])


@pytest.mark.parametrize("case", DEFERRED, ids=[c["name"] for c in DEFERRED])
def test_boundary_deferred(case):
    pytest.skip(f"deferred ({case['deferred']['hook']}): {case['deferred']['reason']}")


@pytest.mark.parametrize("case", MALFORMED, ids=[c["name"] for c in MALFORMED])
def test_malformed(case):
    rt = _rt()
    with pytest.raises(rt.VariantError) as excinfo:
        rt.parse_position("standard", case["fen"])
    _assert_error_shape(excinfo.value, _expect_code(case["expect_failure"]),
                        case["expect_failure"])


@pytest.mark.parametrize("case", UNKNOWN_VARIANT, ids=[c["name"] for c in UNKNOWN_VARIANT])
def test_rollback_unknown_variant(case):
    rt = _rt()
    with pytest.raises(rt.VariantError) as excinfo:
        rt.parse_position(case["input"]["variant"], case["input"]["fen"])
    _assert_error_shape(excinfo.value, case["expect_error_code"], None)


@pytest.mark.parametrize("case", ADDITIVE, ids=[c["name"] for c in ADDITIVE])
def test_rollback_additive_fields(case):
    rt = _rt()
    base = next(h for h in HAPPY if h["name"] == case["base_case"])
    augmented = dict(base["expect_identity"])
    augmented.update(case["extra_fields"])
    assert rt.project_additive(augmented) == base["expect_identity"]


def test_identity_requires_position():
    rt = _rt()
    record = rt.identity(rt.parse_position("standard", HAPPY[0]["fen"]))
    with pytest.raises(rt.VariantError) as excinfo:
        rt.identity(record)  # a bare dict is not a validated Position
    _assert_error_shape(excinfo.value, "malformed_request", None)
    with pytest.raises(rt.VariantError):
        rt.Position(**record)  # direct construction without the token fails


def test_project_additive_validates():
    rt = _rt()
    base = dict(rt.identity(rt.parse_position("standard", HAPPY[0]["fen"])))
    for bad in (
        {**base, "variant": "crazyhouse"},
        {**base, "board": "x"},
        {**base, "side_to_move": "x"},
        {**base, "castling_rights": "XYZ"},
        {**base, "en_passant": "e4"},
        {**base, "variant": {}},
        {**base, "board": []},
        {**base, "side_to_move": 1},
        {**base, "castling_rights": None},
        {**base, "en_passant": False},
    ):
        with pytest.raises(rt.VariantError):
            rt.project_additive(bad)


def test_variant_error_shape_enforced():
    rt = _rt()
    with pytest.raises(ValueError):
        rt.VariantError(code="malformed_request", message="m", retryable=1)
    with pytest.raises(ValueError):
        rt.VariantError(code="malformed_request", message="")
    with pytest.raises(ValueError):
        rt.VariantError(code="malformed_request", message=42)
    with pytest.raises(ValueError):
        rt.VariantError(code="nope", message="m")
    with pytest.raises(ValueError):
        rt.VariantError(code="malformed_request", message="m", failure_class="nope")
    err = rt.VariantError(code="internal", message="m", retryable=True)
    assert err.retryable is True and err.failure_class is None
