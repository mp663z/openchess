"""T0115: position-digest red battery with closed mutation witnesses."""
from __future__ import annotations

import copy

import pytest

from tests.test_t0113_position_digest_contract import (
    DigestError,
    digest_fen,
    parse_digest,
)
from tests.test_t0114_fixture import CASES, FAILURE_MAPPING, _apply_repair

MUTATION_MANIFEST = {
    case["name"]: (case["kind"], case["expect_failure"])
    for case in CASES["malformed"]
}
EXPECTED_MANIFEST = {
    "digest-uppercase-hex": ("digest-parse", "malformed_digest"),
    "digest-short-hex": ("digest-parse", "malformed_digest"),
    "digest-unknown-prefix": ("digest-parse", "malformed_digest"),
    "digest-trailing-space": ("digest-parse", "malformed_digest"),
    "position-kingless": ("digest", "malformed_position"),
    "position-grammar-bad-fen": ("digest", "malformed_position"),
    "variant-unknown": ("digest", "unknown_variant"),
}


def _execute(case, value=None):
    if case["kind"] == "digest-parse":
        return parse_digest(case["input_text"] if value is None else value)
    data = case["input"] if value is None else value
    return digest_fen(data["variant"], data["fen"])


def test_mutation_manifest_is_closed_and_exhaustive():
    assert MUTATION_MANIFEST == EXPECTED_MANIFEST


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda c: c["name"])
def test_each_permissive_repair_mutant_is_killed_by_exact_oracle(case):
    repaired = _apply_repair(case)
    accepted = _execute(case, repaired)
    assert type(accepted) is str and accepted

    with pytest.raises(DigestError) as caught:
        _execute(case)
    error = caught.value
    assert error.failure_class == case["expect_failure"]
    assert error.code == FAILURE_MAPPING[case["expect_failure"]]


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda c: c["name"])
def test_rejection_has_a_minimal_accepted_neighbor_and_no_state(case):
    before = copy.deepcopy(case)
    with pytest.raises(DigestError):
        _execute(case)
    assert case == before
    repaired = _apply_repair(case)
    assert _execute(case, repaired) == _execute(case, copy.deepcopy(repaired))


def test_reject_all_and_accept_all_mutants_are_killed():
    valid = CASES["happy"][0]
    expected = valid["expect_digest"]

    def reject_all(_variant, _fen):
        raise DigestError("malformed_position", FAILURE_MAPPING["malformed_position"])

    def accept_all(_variant, _fen):
        return expected

    with pytest.raises(DigestError):
        reject_all(valid["input"]["variant"], valid["input"]["fen"])
    assert digest_fen(valid["input"]["variant"], valid["input"]["fen"]) == expected

    malformed = next(c for c in CASES["malformed"] if c["kind"] == "digest")
    assert accept_all(malformed["input"]["variant"], malformed["input"]["fen"]) == expected
    with pytest.raises(DigestError):
        _execute(malformed)


def test_rollback_paths_are_nonvacuous_and_repeatable():
    for case in CASES["rollback"]:
        if case["kind"] == "rollback-digest-parse":
            for _ in range(2):
                with pytest.raises(DigestError):
                    parse_digest(case["reject_text"])
            assert parse_digest(case["then_text"]) == case["expect_text"]
        else:
            for _ in range(2):
                with pytest.raises(DigestError):
                    digest_fen(
                        case["reject_input"]["variant"],
                        case["reject_input"]["fen"],
                    )
            assert digest_fen(
                case["then_input"]["variant"],
                case["then_input"]["fen"],
            ) == case["expect_digest"]
