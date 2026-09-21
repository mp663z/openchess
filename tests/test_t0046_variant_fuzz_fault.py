"""T0046: deterministic variant fuzz and injected-fault battery."""
from __future__ import annotations

import copy
import random
from dataclasses import replace

import pytest

from tests.test_t0045_variant_property import HAPPY
from tools import variant_runtime as rt

SEED = 20260921
CASES = 600
FIELDS = ("variant", "board", "side_to_move", "castling_rights", "en_passant")


def _error(call):
    with pytest.raises(rt.VariantError) as caught:
        call()
    error = caught.value
    assert error.code in rt._ERROR_ENUM
    assert type(error.retryable) is bool
    assert type(str(error)) is str and str(error)
    return error.code, error.failure_class


def _base():
    case = HAPPY[0]
    return rt.parse_position(case["variant"], case["fen"])


def test_seeded_field_fault_campaign_is_deterministic_and_fail_closed():
    rng = random.Random(SEED)
    position = _base()
    expected = rt.identity(position)
    counts = {name: 0 for name in FIELDS}
    digest = []
    bad_values = (None, True, 0, [], {}, "", "\N{SNOWMAN}")
    for _ in range(CASES):
        field = rng.choice(FIELDS)
        counts[field] += 1
        record = dict(expected)
        record[field] = copy.deepcopy(rng.choice(bad_values))
        digest.append((field, *_error(lambda r=record: rt.project_additive(r))))
        assert rt.identity(position) == expected
    assert counts == {
        "variant": 126,
        "board": 118,
        "side_to_move": 101,
        "castling_rights": 128,
        "en_passant": 127,
    }
    assert len(digest) == CASES


@pytest.mark.parametrize("field", FIELDS)
def test_forged_immutable_position_rejected_without_mutating_source(field):
    position = _base()
    before = rt.identity(position)
    forged = replace(position, **{field: "invalid"})
    _error(lambda: rt.identity(forged))
    assert rt.identity(position) == before


def test_forged_token_and_object_mutation_do_not_bypass_revalidation():
    position = _base()
    before = rt.identity(position)
    forged = copy.copy(position)
    object.__setattr__(forged, "side_to_move", "x")
    _error(lambda: rt.identity(forged))
    assert rt.identity(position) == before


FAULTS = {
    "identity_accepts_dict": lambda p: rt.identity(dict(rt.identity(p))),
    "projection_missing_field": lambda p: rt.project_additive(
        {k: v for k, v in rt.identity(p).items() if k != "board"}
    ),
    "projection_wrong_variant": lambda p: rt.project_additive(
        {**rt.identity(p), "variant": "unknown"}
    ),
    "parse_wrong_field_count": lambda _p: rt.parse_position("standard", "8/8 w - - 0"),
}


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_injected_faults_hit_their_rejection_path_and_leave_state_unchanged(name):
    position = _base()
    before = rt.identity(position)
    _error(lambda: FAULTS[name](position))
    assert rt.identity(position) == before
