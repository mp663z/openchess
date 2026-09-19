"""T0042: variant conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0041 contract,
and must itself be contract-consistent and strictly shaped (unknown
keys, typo'd expectations and duplicate names are violations, never
inert data). Boundary identity is the exact five-field canonical tuple
including the variant id; every canonical dimension is covered exactly
once; every malformed case is discriminating (its error matches ONLY
its declared failure class) and mutation-checked; rollback cases are
executable against the contract registry."""

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
    check_variant_id,
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

TOP_KEYS = {
    "schema",
    "contract",
    "contract_schema_version",
    "notes",
    "happy",
    "boundary",
    "malformed",
    "rollback",
}
HAPPY_KEYS = {"name", "variant", "fen", "expect_identity"}
BOUNDARY_REQUIRED = {"name", "expect", "changed_fields"}
BOUNDARY_OPTIONAL = {"note", "pair", "deferred"}
MALFORMED_REQUIRED = {"name", "fen", "expect_failure"}
MALFORMED_OPTIONAL = {"note"}
EXPECT_ENUM = {"identical", "different"}

# Stage markers are pairwise disjoint across classes: each class's
# markers appear ONLY in errors raised at that class's stage, so an
# error can match exactly one class.
STAGE_MARKERS = {
    "wrong_field_count": ("FEN must have 6 fields",),
    "bad_board": (
        "8 ranks",
        "piece char",
        "empty-square digits",
        "adjacent digits",
        "8 squares",
    ),
    "bad_side": ("side must be w or b",),
    "bad_castling": ("castling field malformed",),
    "bad_en_passant": ("en-passant field malformed",),
    "bad_counters": ("counters must be canonical",),
    "illegal_position": (
        "king",
        "at most 8 can exist",
        "pieces - at most 16",
        "pawns on the back rank",
        "kings may not be adjacent",
        "in check",
        "inconsistent",
        "LEGAL",
        "en-passant rank",
        "en-passant square",
        "en-passant origin",
    ),
}


def _strict(case: dict, required: set[str], optional: set[str], where: str) -> None:
    assert type(case) is dict
    for key in case:
        assert type(key) is str, f"{where}: non-string key {key!r}"
    unknown = set(case) - required - optional
    assert not unknown, f"{where} case {case.get('name')!r}: unknown keys {sorted(unknown)}"
    missing = required - set(case)
    assert not missing, f"{where} case {case.get('name')!r}: missing keys {sorted(missing)}"


def _identity(record: dict) -> dict:
    """The exact five-field canonical identity tuple, in declared order."""
    board, side, castling, ep, _half, _full = record["fen"].split(" ")
    return dict(zip(CANONICAL_FIELDS, (record["variant"], board, side, castling, ep), strict=True))


def _contract_error(fen: str, name: str) -> str:
    try:
        _check_fen(fen, name, "orthodox")
    except ContractError as exc:
        return str(exc)
    raise AssertionError(f"{name}: FEN accepted")


def _assert_failure_stage(fen: str, name: str, expect: str) -> None:
    msg = _contract_error(fen, name)
    assert any(m in msg for m in STAGE_MARKERS[expect]), f"{name}: no {expect} marker in {msg!r}"
    for other, markers in STAGE_MARKERS.items():
        if other == expect:
            continue
        hit = [m for m in markers if m in msg]
        assert not hit, f"{name}: {msg!r} also matches {other} markers {hit}"


def _observed_fail_closed_code(vid: str, known: set[str]) -> str:
    """Run the registry gate and return the observed structured error
    code (the prefix before the first colon)."""
    try:
        check_variant_id(vid, known)
    except ContractError as exc:
        msg = str(exc)
        assert ":" in msg, f"unstructured error, no code prefix: {msg!r}"
        return msg.split(":", 1)[0]
    raise AssertionError(f"variant id {vid!r} accepted")


def _assert_rollback_codes(case: dict, observed: str) -> None:
    """The observed code, both fixture fields, and the contract-pinned
    code must ALL agree. The registry_rule pins malformed_request for
    unknown variant ids (request validation before variant resolution);
    unknown_variant is in the closed enum but is NOT this path's code."""
    assert case["expect_failure"] == case["expect_error_code"], (
        f"fixture fields disagree: {case['expect_failure']} != {case['expect_error_code']}"
    )
    assert observed == case["expect_failure"], (
        f"observed code {observed!r} != declared expect_failure {case['expect_failure']!r}"
    )
    assert observed == "malformed_request", (
        f"registry_rule pins malformed_request for unknown variant ids, observed {observed!r}"
    )


def _assert_additive_tolerance(base_identity: dict, extra_fields: dict) -> None:
    assert type(extra_fields) is dict and extra_fields, "additive case must carry extra fields"
    collision = set(extra_fields) & set(CANONICAL_FIELDS)
    assert not collision, f"additive fields collide with canonical fields {sorted(collision)}"
    augmented = dict(base_identity)
    augmented.update(extra_fields)
    projection = {f: augmented[f] for f in CANONICAL_FIELDS}
    assert projection == base_identity, "additive fields changed the canonical projection"


def test_fixture_shape():
    assert set(CASES) == TOP_KEYS
    assert CASES["schema"] == "variant-fixture-1"
    assert CASES["contract"] == "data/contracts/variant.yaml"
    assert CASES["contract_schema_version"] == CONTRACT_DOC["schema_version"]
    sections = ("happy", "boundary", "malformed", "rollback")
    for section in sections:
        assert type(CASES[section]) is list and CASES[section]
    all_names = [c["name"] for s in sections for c in CASES[s]]
    assert len(all_names) == len(set(all_names)), "case names must be globally unique"
    for case in HAPPY:
        _strict(case, HAPPY_KEYS, set(), "happy")
        assert type(case["name"]) is str and case["name"].strip()
        assert type(case["variant"]) is str
        assert type(case["fen"]) is str
        assert type(case["expect_identity"]) is dict
        assert list(case["expect_identity"]) == CANONICAL_FIELDS
    for case in BOUNDARY:
        _strict(case, BOUNDARY_REQUIRED, BOUNDARY_OPTIONAL, "boundary")
        assert case["expect"] in EXPECT_ENUM, case["name"]
        assert type(case["changed_fields"]) is list
        assert len(set(case["changed_fields"])) == len(case["changed_fields"])
        for field in case["changed_fields"]:
            assert field in CANONICAL_FIELDS, case["name"]
        if "note" in case:
            assert type(case["note"]) is str
        if "deferred" in case:
            assert "pair" not in case, f"{case['name']}: deferred cases carry no executable pair"
            assert set(case["deferred"]) == {"reason", "reverify", "hook"}
            assert all(type(v) is str and v.strip() for v in case["deferred"].values())
        else:
            pair = case["pair"]
            assert type(pair) is list and len(pair) == 2
            for record in pair:
                assert set(record) == {"variant", "fen"}, case["name"]
                assert type(record["variant"]) is str
                assert type(record["fen"]) is str
    for case in MALFORMED:
        _strict(case, MALFORMED_REQUIRED, MALFORMED_OPTIONAL, "malformed")
        assert type(case["fen"]) is str
    kinds: dict[str, int] = {}
    for case in ROLLBACK:
        kind = case.get("kind")
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "unknown-variant":
            _strict(
                case,
                {"name", "kind", "input", "expect_failure", "expect_error_code"},
                {"note"},
                "rollback",
            )
            assert set(case["input"]) == {"variant", "fen"}
            assert type(case["input"]["variant"]) is str
            assert type(case["input"]["fen"]) is str
        elif kind == "additive-fields":
            _strict(
                case,
                {"name", "kind", "base_case", "extra_fields", "expect"},
                {"note"},
                "rollback",
            )
            assert case["expect"] == "identity_unchanged"
            assert type(case["base_case"]) is str
            assert type(case["extra_fields"]) is dict
        else:
            raise AssertionError(f"rollback case {case.get('name')!r}: unknown kind {kind!r}")
    assert kinds.get("unknown-variant"), "no executable unknown-variant rollback case"
    assert kinds.get("additive-fields"), "no executable additive-fields rollback case"


def test_happy_cases_are_contract_legal_and_identity_exact():
    for case in HAPPY:
        assert case["variant"] in REGISTRY, case["name"]
        identity = case["expect_identity"]
        assert identity["variant"] == case["variant"]
        # the FEN must survive the contract's own full legality checks
        _check_fen(case["fen"], case["name"], "orthodox")
        assert identity == _identity({"variant": case["variant"], "fen": case["fen"]})


def test_every_registry_variant_has_a_happy_case():
    covered = {c["variant"] for c in HAPPY}
    assert covered >= REGISTRY


def test_boundary_pairs_discriminate_exactly_the_declared_fields():
    for case in BOUNDARY:
        if "deferred" in case:
            continue
        records = case["pair"]
        for record in records:
            assert record["variant"] in REGISTRY, case["name"]
            _check_fen(record["fen"], case["name"], "orthodox")
        ident_a, ident_b = (_identity(r) for r in records)
        diffs = {f for f in CANONICAL_FIELDS if ident_a[f] != ident_b[f]}
        if case["expect"] == "identical":
            assert diffs == set(), f"{case['name']}: identities differ in {sorted(diffs)}"
            assert case["changed_fields"] == [], case["name"]
            fen_a, fen_b = (r["fen"] for r in records)
            assert fen_a != fen_b, f"{case['name']}: identical raw FENs prove nothing"
            assert fen_a.split(" ")[:4] == fen_b.split(" ")[:4], (
                f"{case['name']}: only counters may differ"
            )
        else:
            assert case["changed_fields"], case["name"]
            assert diffs == set(case["changed_fields"]), (
                f"{case['name']}: tuple delta {sorted(diffs)} != declared {case['changed_fields']}"
            )


def test_boundary_coverage_each_canonical_dimension_once():
    diff_fields = []
    for case in BOUNDARY:
        if "deferred" not in case and case["expect"] == "different":
            diff_fields.extend(case["changed_fields"])
    fen_dims = ["board", "side_to_move", "castling_rights", "en_passant"]
    assert sorted(diff_fields) == sorted(fen_dims), (
        f"each FEN-derivable canonical dimension exactly once: {diff_fields}"
    )
    variant_cases = [c for c in BOUNDARY if c["changed_fields"] == ["variant"]]
    assert len(variant_cases) == 1, "the variant dimension must be covered exactly once"
    if len(REGISTRY) == 1:
        assert "deferred" in variant_cases[0], (
            "single-variant registry: the variant case must be an explicit deferred marker"
        )
    else:
        assert "deferred" not in variant_cases[0], (
            "registry has >=2 variants: the variant case must be executable"
        )


def test_deferred_cases_are_explicit_non_executable_markers():
    for case in BOUNDARY:
        if "deferred" not in case:
            continue
        assert case["changed_fields"] == ["variant"], case["name"]
        assert case["expect"] == "different", case["name"]
        assert len(REGISTRY) == 1, (
            f"{case['name']}: registry has {len(REGISTRY)} variants - "
            "the deferred marker must become an executable pair now"
        )
        node: object = CONTRACT_DOC
        for segment in case["deferred"]["hook"].split("."):
            assert type(node) is dict and segment in node, (
                f"{case['name']}: deferred hook {case['deferred']['hook']!r} "
                f"does not resolve in the contract (failed at {segment!r})"
            )
            node = node[segment]


def test_every_failure_class_has_a_malformed_case():
    covered = {c["expect_failure"] for c in MALFORMED}
    assert set(FAILURE_CLASSES) <= covered, f"uncovered: {set(FAILURE_CLASSES) - covered}"


def test_malformed_cases_fail_at_exactly_the_declared_stage():
    for case in MALFORMED:
        assert case["expect_failure"] in FAILURE_CLASSES, case["name"]
        _assert_failure_stage(case["fen"], case["name"], case["expect_failure"])


def test_malformed_stage_assertion_is_discriminating_under_mutation():
    """Every misdeclared failure class must be caught: for each case and
    each WRONG class, the stage assertion itself must fail."""
    for case in MALFORMED:
        for wrong in set(FAILURE_CLASSES) - {case["expect_failure"]}:
            with pytest.raises(AssertionError):
                _assert_failure_stage(case["fen"], case["name"], wrong)


def test_rollback_unknown_variant_fails_closed_executable():
    cases = [c for c in ROLLBACK if c["kind"] == "unknown-variant"]
    assert cases, "no unknown-variant rollback case"
    for case in cases:
        vid = case["input"]["variant"]
        assert vid not in REGISTRY, f"{case['name']}: {vid!r} is declared - vacuous"
        # the FEN payload is fully legal, so the failure is attributable
        # to the variant id alone
        _check_fen(case["input"]["fen"], case["name"], "orthodox")
        observed = _observed_fail_closed_code(vid, REGISTRY)
        assert observed in ERROR_ENUM, f"{case['name']}: {observed!r} outside the closed enum"
        _assert_rollback_codes(case, observed)
    # every declared variant passes the same gate
    for known in REGISTRY:
        check_variant_id(known, REGISTRY)


def test_rollback_unknown_variant_gate_is_discriminating_under_mutation():
    """If the unknown id were declared, the fail-closed assertion must fail."""
    for known in REGISTRY:
        with pytest.raises(AssertionError):
            _observed_fail_closed_code(known, REGISTRY)


def test_rollback_error_code_fields_are_load_bearing_under_mutation():
    """expect_failure / expect_error_code are asserted, not ornamental:
    every wrong-but-declared sibling code, in either field, must fail."""
    for case in [c for c in ROLLBACK if c["kind"] == "unknown-variant"]:
        observed = _observed_fail_closed_code(case["input"]["variant"], REGISTRY)
        _assert_rollback_codes(case, observed)
        for wrong in ERROR_ENUM - {"malformed_request"}:
            for field in ("expect_failure", "expect_error_code"):
                mutated = dict(case, **{field: wrong})
                with pytest.raises(AssertionError):
                    _assert_rollback_codes(mutated, observed)
        with pytest.raises(AssertionError):
            _assert_rollback_codes(dict(case, expect_error_code="garbage_code"), observed)


def test_rollback_additive_fields_tolerated_executable():
    cases = [c for c in ROLLBACK if c["kind"] == "additive-fields"]
    assert cases, "no additive-fields rollback case"
    for case in cases:
        base = next((h for h in HAPPY if h["name"] == case["base_case"]), None)
        assert base is not None, f"{case['name']}: base_case {case['base_case']!r} not in happy"
        _assert_additive_tolerance(base["expect_identity"], case["extra_fields"])


def test_rollback_additive_tolerance_is_discriminating_under_mutation():
    """Canonical-field collision or value clobbering must be caught."""
    for case in [c for c in ROLLBACK if c["kind"] == "additive-fields"]:
        base = next(h for h in HAPPY if h["name"] == case["base_case"])
        for field in CANONICAL_FIELDS:
            with pytest.raises(AssertionError):
                _assert_additive_tolerance(base["expect_identity"], {field: "clobbered"})
        with pytest.raises(AssertionError):
            _assert_additive_tolerance(base["expect_identity"], {})
