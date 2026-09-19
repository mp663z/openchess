"""T0041: variant + position-identity contract lint - adversarial
mutations, each rejected for its own reason."""

from __future__ import annotations

import copy

import pytest
import yaml

from tools.variant_contract_lint import CONTRACT, ContractError, lint

DOC = yaml.safe_load(CONTRACT.read_text())


def test_real_contract_is_clean():
    lint(copy.deepcopy(DOC))


def _mutate(fn):
    doc = copy.deepcopy(DOC)
    fn(doc)
    return doc


MUTATIONS = {
    "canonical_fields_reordered": lambda d: d["contract"]["identity"]
        .__setitem__("canonical_fields", ["board", "variant",
                     "side_to_move", "castling_rights", "en_passant"]),
    "canonical_field_dropped": lambda d: d["contract"]["identity"]
        ["canonical_fields"].remove("en_passant"),
    "identity_single_hash_allowed": lambda d: d["contract"]["identity"]
        .__setitem__("rule", "Positions are identified by hash."),
    "hash_rule_no_fallback": lambda d: d["contract"]["identity"]
        .__setitem__("hash_rule", "Hashes identify positions."),
    "unknown_variant_coerced": lambda d: d["contract"]["variants"]
        .__setitem__("registry_rule",
                     "Unknown variant ids coerce to standard."),
    "standard_missing": lambda d: d["contract"]["variants"]
        .__setitem__("entries", [e for e in d["contract"]["variants"]
                     ["entries"] if e["id"] != "standard"]),
    "duplicate_variant_id": lambda d: d["contract"]["variants"]
        ["entries"].append(copy.deepcopy(d["contract"]["variants"]
                                         ["entries"][0])),
    "wrong_standard_start_fen": lambda d: d["contract"]["variants"]
        ["entries"][0].__setitem__(
        "start_fen",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"),
    "start_fen_five_fields": lambda d: d["contract"]["variants"]
        ["entries"][0].__setitem__(
        "start_fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0"),
    "start_fen_bad_rank": lambda d: d["contract"]["variants"]
        ["entries"][0].__setitem__(
        "start_fen",
        "rnbqkbnr/ppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
    "start_fen_bad_piece": lambda d: d["contract"]["variants"]
        ["entries"][0].__setitem__(
        "start_fen",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        .replace("q", "x", 1)),
    "failure_class_dropped": lambda d: d["contract"]["fen"]
        ["failure_classes"].remove("illegal_position"),
    "fen_rule_no_roundtrip": lambda d: d["contract"]["fen"].__setitem__(
        "rule", "FEN is parsed somehow."),
    "enum_missing_unknown_variant": lambda d: d["contract"]["errors"]
        ["closed_enum"].remove("unknown_variant"),
    "enum_duplicate_code": lambda d: d["contract"]["errors"]
        ["closed_enum"].append("internal"),
    "base_path_unversioned": lambda d: d["contract"]["versioning"]
        .__setitem__("base_path", "/variant"),
    "privacy_opened": lambda d: d["contract"]["privacy"].__setitem__(
        "chess_content", "unrestricted"),
    "schema_version_bumped": lambda d: d.__setitem__("schema_version", 2),
    "schema_version_bool": lambda d: d.__setitem__("schema_version",
                                                   True),
    "status_invented": lambda d: d["contract"]["variants"]["entries"]
        [0].__setitem__("status", "deprecated"),
    "castling_invented": lambda d: d["contract"]["variants"]["entries"]
        [0].__setitem__("castling", "freestyle"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_mutation_is_rejected(name):
    with pytest.raises(ContractError):
        lint(_mutate(MUTATIONS[name]))


def test_non_dict_document_is_rejected():
    with pytest.raises(ContractError):
        lint(["not", "a", "mapping"])
