"""T0046: closed variant fuzz manifest and injected-fault battery."""
from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import replace

import pytest

from tests.test_t0045_variant_property import HAPPY
from tools import variant_runtime as rt

SEED = 20260921
FIELDS = ("variant", "board", "side_to_move", "castling_rights", "en_passant")
BAD_VALUES = (
    ("none", None),
    ("bool", True),
    ("int", 0),
    ("list", []),
    ("dict", {}),
    ("empty", ""),
    ("snowman", "\N{SNOWMAN}"),
)
FAILURE_FOR_FIELD = {
    "variant": None,
    "board": "bad_board",
    "side_to_move": "bad_side",
    "castling_rights": "bad_castling",
    "en_passant": "bad_en_passant",
}
MANIFEST_DIGEST = "0a74e7d3f95419abbb39305f53d426343259789a49f0eeded80a5815c1fb478b"


def _base():
    case = HAPPY[0]
    return rt.parse_position(case["variant"], case["fen"])


def _outcome(call):
    with pytest.raises(rt.VariantError) as caught:
        call()
    error = caught.value
    assert error.code in rt._ERROR_ENUM
    assert type(error.retryable) is bool
    assert type(str(error)) is str and str(error)
    return error.code, error.failure_class


def _expected(field, label):
    failure = FAILURE_FOR_FIELD[field] if label in {"empty", "snowman"} else None
    return "malformed_request", failure


def _manifest():
    scenarios = [
        {"field": field, "value_label": label, "bad_value": copy.deepcopy(value),
         "expected": _expected(field, label)}
        for field in FIELDS
        for label, value in BAD_VALUES
    ]
    random.Random(SEED).shuffle(scenarios)
    return scenarios


def _run_campaign(project):
    position = _base()
    canonical = rt.identity(position)
    # Positive controls use the same public surface as every negative.
    assert project(dict(canonical)) == canonical
    outcomes = []
    for scenario in _manifest():
        record = dict(canonical)
        record[scenario["field"]] = copy.deepcopy(scenario["bad_value"])
        got = _outcome(lambda r=record: project(r))
        assert got == scenario["expected"], (
            scenario["field"], scenario["value_label"], got)
        # Minimal repair changes only the selected field and must restore
        # the exact canonical projection through the same public surface.
        repaired = dict(record)
        repaired[scenario["field"]] = canonical[scenario["field"]]
        assert project(repaired) == canonical
        assert rt.identity(position) == canonical
        outcomes.append({
            "field": scenario["field"],
            "value_label": scenario["value_label"],
            "code": got[0],
            "failure_class": got[1],
        })
    assert project(dict(canonical)) == canonical
    payload = json.dumps(outcomes, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(payload.encode()).hexdigest() == MANIFEST_DIGEST
    assert len(outcomes) == len(FIELDS) * len(BAD_VALUES) == 35
    return canonical


def test_closed_field_fault_manifest_is_discriminating_and_stable():
    _run_campaign(rt.project_additive)


@pytest.mark.parametrize("field", FIELDS)
def test_forged_immutable_position_rejected_with_exact_failure_and_repair(field):
    position = _base()
    canonical = rt.identity(position)
    forged = replace(position, **{field: "invalid"})
    expected_failure = None if field == "variant" else FAILURE_FOR_FIELD[field]
    assert _outcome(lambda: rt.identity(forged)) == (
        "malformed_request", expected_failure)
    assert rt.identity(position) == canonical
    repaired = replace(forged, **{field: getattr(position, field)})
    assert rt.identity(repaired) == canonical


def test_forged_token_mutation_rejected_with_exact_failure_and_repair():
    position = _base()
    canonical = rt.identity(position)
    forged = copy.copy(position)
    object.__setattr__(forged, "side_to_move", "x")
    assert _outcome(lambda: rt.identity(forged)) == (
        "malformed_request", "bad_side")
    assert rt.identity(position) == canonical
    object.__setattr__(forged, "side_to_move", position.side_to_move)
    assert rt.identity(forged) == canonical


FAULTS = {
    "identity_accepts_dict": (
        lambda p: rt.identity(dict(rt.identity(p))),
        ("malformed_request", None),
    ),
    "projection_missing_field": (
        lambda p: rt.project_additive(
            {k: v for k, v in rt.identity(p).items() if k != "board"}
        ),
        ("malformed_request", None),
    ),
    "projection_wrong_variant": (
        lambda p: rt.project_additive({**rt.identity(p), "variant": "unknown"}),
        ("malformed_request", None),
    ),
    "parse_wrong_field_count": (
        lambda _p: rt.parse_position("standard", "8/8 w - - 0"),
        ("malformed_request", "wrong_field_count"),
    ),
}


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_named_faults_have_exact_outcomes_positive_controls_and_rollback(name):
    position = _base()
    canonical = rt.identity(position)
    call, expected = FAULTS[name]
    assert _outcome(lambda: call(position)) == expected
    assert rt.identity(position) == canonical
    assert rt.project_additive(dict(canonical)) == canonical


def test_campaign_kills_reject_all_wrong_mapping_skip_and_accept_mutants():
    canonical = rt.identity(_base())

    def reject_all(_record):
        raise rt.VariantError(code="malformed_request", message="reject all")

    def wrong_code(record):
        try:
            return rt.project_additive(record)
        except rt.VariantError as error:
            raise rt.VariantError(code="unknown_variant", message="wrong code") from error

    def wrong_failure_class(record):
        try:
            return rt.project_additive(record)
        except rt.VariantError as error:
            raise rt.VariantError(
                code=error.code,
                failure_class="bad_board",
                message="wrong failure class",
            ) from error

    def skipped_validation(record):
        return {field: record[field] for field in FIELDS}

    def accept_all(_record):
        return dict(canonical)

    for mutant in (reject_all, wrong_code, wrong_failure_class,
                   skipped_validation, accept_all):
        with pytest.raises((AssertionError, pytest.fail.Exception, rt.VariantError)):
            _run_campaign(mutant)
