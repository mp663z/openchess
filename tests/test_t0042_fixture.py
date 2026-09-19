"""T0042: variant conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0041 contract,
and must itself be contract-consistent (no orphan variants, no invented
failure classes, identity keys exactly the canonical tuple, full
coverage of declared classes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools.variant_contract_lint import (
    CANONICAL_FIELDS,
    CONTRACT,
    FAILURE_CLASSES,
    ContractError,
    _check_fen,
)

FIXTURE = Path(__file__).parent / "fixtures" / "variant" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
CONTRACT_DOC = yaml.safe_load(CONTRACT.read_text())
REGISTRY = {e["id"] for e in CONTRACT_DOC["contract"]["variants"]["entries"]}
ERROR_ENUM = set(CONTRACT_DOC["contract"]["errors"]["closed_enum"])

HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]


def test_fixture_shape():
    assert CASES["schema"] == "variant-fixture-1"
    assert CASES["contract"] == "data/contracts/variant.yaml"
    assert CASES["contract_schema_version"] == CONTRACT_DOC["schema_version"]
    assert HAPPY and BOUNDARY and MALFORMED and ROLLBACK
    for section in (HAPPY, BOUNDARY, MALFORMED, ROLLBACK):
        names = [c["name"] for c in section]
        assert len(names) == len(set(names)), "duplicate case names"


def test_happy_cases_are_contract_legal_and_identity_exact():
    for case in HAPPY:
        assert case["variant"] in REGISTRY, case["name"]
        identity = case["expect_identity"]
        assert list(identity) == CANONICAL_FIELDS, case["name"]
        assert identity["variant"] == case["variant"]
        # the FEN must survive the contract's own full legality checks
        _check_fen(case["fen"], case["name"], "orthodox")
        board, side, castling, ep, _half, _full = case["fen"].split(" ")
        assert identity["board"] == board
        assert identity["side_to_move"] == side
        assert identity["castling_rights"] == castling
        assert identity["en_passant"] == ep


def test_every_registry_variant_has_a_happy_case():
    covered = {c["variant"] for c in HAPPY}
    assert covered >= REGISTRY


def test_boundary_pairs_are_well_formed_and_discriminating():
    for case in BOUNDARY:
        pair = case["pair"]
        assert len(pair) == 2
        for fen in pair:
            _check_fen(fen, case["name"], "orthodox")
        idents = []
        for fen in pair:
            board, side, castling, ep, _h, _f = fen.split(" ")
            idents.append((board, side, castling, ep))
        if case["expect"] == "identical":
            assert idents[0] == idents[1], case["name"]
        else:
            assert idents[0] != idents[1], case["name"]


def test_every_failure_class_has_a_malformed_case():
    covered = {c["expect_failure"] for c in MALFORMED}
    assert set(FAILURE_CLASSES) <= covered, f"uncovered: {set(FAILURE_CLASSES) - covered}"


def test_malformed_cases_actually_fail_the_contract_checks():
    for case in MALFORMED:
        assert case["expect_failure"] in FAILURE_CLASSES, case["name"]
        with pytest.raises(ContractError):
            _check_fen(case["fen"], case["name"], "orthodox")


def test_malformed_failure_class_matches_check_stage():
    """Each malformed case must fail at the stage its class names."""
    stage_markers = {
        "wrong_field_count": "6 fields",
        "bad_board": ("rank", "piece char", "digits", "noncanonical"),
        "bad_side": "side",
        "bad_castling": "castling field malformed",
        "bad_en_passant": "en-passant",
        "bad_counters": "counters",
        "illegal_position": (
            "king",
            "pawns",
            "pieces",
            "adjacent",
            "in check",
            "inconsistent",
            "LEGAL",
        ),
    }
    for case in MALFORMED:
        try:
            _check_fen(case["fen"], case["name"], "orthodox")
        except ContractError as exc:
            markers = stage_markers[case["expect_failure"]]
            if isinstance(markers, str):
                markers = (markers,)
            assert any(m in str(exc) for m in markers), f"{case['name']}: {exc}"
        else:
            raise AssertionError(f"{case['name']}: accepted")


def test_rollback_cases_use_declared_error_codes():
    for case in ROLLBACK:
        if "expect_error_code" in case:
            assert case["expect_error_code"] in ERROR_ENUM, case["name"]
    codes = {c.get("expect_error_code") for c in ROLLBACK} - {None}
    assert codes, "rollback section proves nothing"


def test_unknown_variant_is_not_in_registry():
    for case in ROLLBACK:
        if case["name"] == "unknown-variant-fails-closed":
            assert case["variant"] not in REGISTRY
