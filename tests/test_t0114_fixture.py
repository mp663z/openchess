"""T0114: position-digest conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the T0113
position-digest contract. The cases execute against the contract-
derived reference in tests.test_t0113_position_digest_contract (itself
fully derived from data/contracts/position_digest.yaml plus the linked
variant, en-passant and FEN contracts) - nothing is re-implemented
here. Pinned digests in the fixture were computed from that reference
at authoring time, so any contract or derivation drift breaks this
battery. Every malformed case is discriminating (repairing ONLY its
declared defect makes the case valid) and rollback cases prove the
pure-function surface: a rejection leaves no trace.

DESIGN CAUTION: the reference interpreter is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work must
execute these same cases against a separately implemented runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0086_fen_contract import FenError, parse_fen  # noqa: E402
from tests.test_t0113_position_digest_contract import (  # noqa: E402
    DigestError,
    digest_fen,
    parse_digest,
)

FIXTURE = Path(__file__).parent / "fixtures" / "position_digest" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
DOC = yaml.safe_load(
    (ROOT / "data" / "contracts" / "position_digest.yaml").read_text())
C = DOC["contract"]
FAILURE_CLASSES = list(C["failures"]["classes"])
FAILURE_MAPPING = dict(C["failures"]["mapping"])
DIGEST_RE = C["digest"]["format"]["regex"]
DIGEST_PREFIX = C["digest"]["format"]["prefix"]
VARIANT_IDS = [e["id"] for e in yaml.safe_load(
    (ROOT / "data" / "contracts" / "variant.yaml")
    .read_text())["contract"]["variants"]["entries"]]

TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes",
            "happy", "boundary", "malformed", "rollback"}
KIND_KEYS = {
    "digest": {"name", "kind", "input", "expect_digest"},
    "digest-parse": {"name", "kind", "input_text", "expect_text"},
    "digest-equivalence": {"name", "kind", "inputs", "expect"},
    "rollback-digest-parse": {"name", "kind", "reject_text",
                              "then_text", "expect_text"},
    "rollback-digest": {"name", "kind", "reject_input", "then_input",
                        "expect_digest"},
}
HAPPY_KINDS = {"digest", "digest-parse"}
BOUNDARY_KINDS = {"digest", "digest-equivalence"}
MALFORMED_KINDS = {"digest", "digest-parse"}
MALFORMED_REQUIRED = {"name", "kind", "input", "defect",
                      "expect_failure"}
MALFORMED_PARSE_REQUIRED = {"name", "kind", "input_text", "defect",
                            "expect_failure"}
FEN_SUBCLASSES = {"malformed_fen", "impossible_position"}


def _case(name):
    for section in ("happy", "boundary", "malformed", "rollback"):
        for case in CASES[section]:
            if case["name"] == name:
                return case
    raise AssertionError(f"case {name} not found")


def test_fixture_structure():
    assert set(CASES.keys()) == TOP_KEYS
    assert CASES["schema"] == 1
    assert CASES["contract"] == C["id"]
    assert CASES["contract_schema_version"] == DOC["schema_version"]
    assert isinstance(CASES["notes"], str) and CASES["notes"].strip()
    names = []
    for section in ("happy", "boundary", "malformed", "rollback"):
        assert CASES[section], f"{section} must not be empty"
        for case in CASES[section]:
            names.append(case["name"])
            kind = case["kind"]
            assert kind in KIND_KEYS, case["name"]
            if section == "malformed":
                required = (MALFORMED_PARSE_REQUIRED
                            if kind == "digest-parse"
                            else MALFORMED_REQUIRED)
            else:
                required = KIND_KEYS[kind]
            assert required <= set(case.keys()), (
                f"{case['name']}: missing {required - set(case.keys())}")
            extra = set(case.keys()) - required
            assert extra <= {"expect_subclass"}, (
                f"{case['name']}: unexpected keys {extra}")
    assert len(names) == len(set(names)), "case names must be unique"
    for case in CASES["happy"]:
        assert case["kind"] in HAPPY_KINDS, case["name"]
    for case in CASES["boundary"]:
        assert case["kind"] in BOUNDARY_KINDS, case["name"]
        if case["kind"] == "digest-equivalence":
            assert len(case["inputs"]) == 2, case["name"]
            assert case["expect"] in ("equal", "different"), case["name"]
    for case in CASES["malformed"]:
        assert case["kind"] in MALFORMED_KINDS, case["name"]
        defect = case["defect"]
        assert type(defect) is str and defect.strip(), case["name"]
        assert case["expect_failure"] in FAILURE_CLASSES, case["name"]
        if "expect_subclass" in case:
            assert case["expect_failure"] == "malformed_position"
            assert case["expect_subclass"] in FEN_SUBCLASSES
    for case in CASES["happy"] + CASES["boundary"]:
        if case["kind"] == "digest":
            assert set(case["input"].keys()) == {"variant", "fen"}
            assert case["expect_digest"].startswith(DIGEST_PREFIX)


def _digest(case_input):
    return digest_fen(case_input["variant"], case_input["fen"])


def test_happy():
    for case in CASES["happy"]:
        if case["kind"] == "digest":
            assert _digest(case["input"]) == case["expect_digest"], (
                case["name"])
        else:  # digest-parse
            assert parse_digest(case["input_text"]) == case[
                "expect_text"], case["name"]


def test_boundary():
    for case in CASES["boundary"]:
        if case["kind"] == "digest":
            assert _digest(case["input"]) == case["expect_digest"], (
                case["name"])
        else:  # digest-equivalence
            a, b = (digest_fen("standard", f) for f in case["inputs"])
            if case["expect"] == "equal":
                assert a == b, case["name"]
            else:
                assert a != b, case["name"]


def test_malformed():
    for case in CASES["malformed"]:
        with pytest.raises(DigestError) as exc:
            if case["kind"] == "digest-parse":
                parse_digest(case["input_text"])
            else:
                _digest(case["input"])
        assert exc.value.failure_class == case["expect_failure"], (
            f"{case['name']}: {exc.value.failure_class} != "
            f"{case['expect_failure']}")
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]], case["name"]
        if "expect_subclass" in case:
            # the digest surface collapses position failures into
            # malformed_position; the pinned FEN subclass must be the
            # actual reason underneath.
            with pytest.raises(FenError) as fen_exc:
                parse_fen(
                    yaml.safe_load(
                        (ROOT / "data" / "contracts" / "fen.yaml")
                        .read_text())["contract"],
                    case["input"]["fen"])
            assert fen_exc.value.failure_class == case[
                "expect_subclass"], case["name"]


def test_malformed_discriminating():
    """Every malformed case names exactly one defect: repairing ONLY
    that defect makes the case valid, so no second defect hides."""
    for case in CASES["malformed"]:
        name = case["name"]
        if name == "digest-uppercase-hex":
            repaired = case["input_text"].lower()
            assert repaired != case["input_text"]
            assert parse_digest(repaired) == repaired
            continue
        if name == "digest-short-hex":
            repaired = case["input_text"] + "e"
            assert parse_digest(repaired) == repaired
            continue
        if name == "digest-unknown-prefix":
            repaired = DIGEST_PREFIX + case["input_text"].split(":", 1)[1]
            assert parse_digest(repaired) == repaired
            continue
        if name == "digest-trailing-space":
            repaired = case["input_text"].strip()
            assert parse_digest(repaired) == repaired
            continue
        if name == "position-kingless":
            # repair: give the position its missing black king.
            repaired = dict(case["input"])
            repaired["fen"] = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
            assert _digest(repaired) == _case(
                "kings-only-minimal")["expect_digest"]
            continue
        if name == "position-grammar-bad-fen":
            repaired = dict(case["input"])
            repaired["fen"] = (
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
                " w KQkq - 0 1")
            assert _digest(repaired) == _case("startpos")[
                "expect_digest"]
            continue
        if name == "variant-unknown":
            assert case["input"]["variant"] not in VARIANT_IDS
            repaired = dict(case["input"])
            repaired["variant"] = "standard"
            assert _digest(repaired) == _case("startpos")[
                "expect_digest"]
            continue
        raise AssertionError(f"no repair rule for {name}")


def test_rollback():
    for case in CASES["rollback"]:
        if case["kind"] == "rollback-digest-parse":
            with pytest.raises(DigestError):
                parse_digest(case["reject_text"])
            # the rejected call leaves no trace: the next valid parse
            # behaves exactly as if the rejection never happened.
            assert parse_digest(case["then_text"]) == case[
                "expect_text"], case["name"]
        else:  # rollback-digest
            with pytest.raises(DigestError):
                _digest(case["reject_input"])
            assert _digest(case["then_input"]) == case[
                "expect_digest"], case["name"]
