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
    "canonical_fields_reordered": lambda d: d["contract"]["identity"].__setitem__(
        "canonical_fields", ["board", "variant", "side_to_move", "castling_rights", "en_passant"]
    ),
    "canonical_field_dropped": lambda d: d["contract"]["identity"]["canonical_fields"].remove(
        "en_passant"
    ),
    "identity_single_hash_allowed": lambda d: d["contract"]["identity"].__setitem__(
        "rule", "Positions are identified by hash."
    ),
    "hash_rule_no_fallback": lambda d: d["contract"]["identity"].__setitem__(
        "hash_rule", "Hashes identify positions."
    ),
    "unknown_variant_coerced": lambda d: d["contract"]["variants"].__setitem__(
        "registry_rule", "Unknown variant ids coerce to standard."
    ),
    "standard_missing": lambda d: d["contract"]["variants"].__setitem__(
        "entries", [e for e in d["contract"]["variants"]["entries"] if e["id"] != "standard"]
    ),
    "duplicate_variant_id": lambda d: d["contract"]["variants"]["entries"].append(
        copy.deepcopy(d["contract"]["variants"]["entries"][0])
    ),
    "wrong_standard_start_fen": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"
    ),
    "start_fen_five_fields": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0"
    ),
    "start_fen_bad_rank": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "rnbqkbnr/ppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    ),
    "start_fen_bad_piece": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1".replace("q", "x", 1)
    ),
    "failure_class_dropped": lambda d: d["contract"]["fen"]["failure_classes"].remove(
        "illegal_position"
    ),
    "fen_rule_no_roundtrip": lambda d: d["contract"]["fen"].__setitem__(
        "rule", "FEN is parsed somehow."
    ),
    "enum_missing_unknown_variant": lambda d: d["contract"]["errors"]["closed_enum"].remove(
        "unknown_variant"
    ),
    "enum_duplicate_code": lambda d: d["contract"]["errors"]["closed_enum"].append("internal"),
    "base_path_unversioned": lambda d: d["contract"]["versioning"].__setitem__(
        "base_path", "/variant"
    ),
    "privacy_opened": lambda d: d["contract"]["privacy"].__setitem__(
        "chess_content", "unrestricted"
    ),
    "schema_version_bumped": lambda d: d.__setitem__("schema_version", 2),
    "schema_version_bool": lambda d: d.__setitem__("schema_version", True),
    "status_invented": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "status", "deprecated"
    ),
    "castling_invented": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "castling", "freestyle"
    ),
    # verifier probes on 63f1f9e - each previously passed
    "errors_shape_removed": lambda d: d["contract"]["errors"].pop("shape"),
    "errors_shape_retryable_type_string": lambda d: d["contract"]["errors"]["shape"]["error"][
        "fields"
    ]["retryable"].__setitem__("type", "string"),
    "errors_shape_extra_field": lambda d: d["contract"]["errors"]["shape"]["error"][
        "fields"
    ].__setitem__("hint", {"type": "string", "required": False}),
    "fen_grammar_anything": lambda d: d["contract"]["fen"].__setitem__("grammar", "anything"),
    "illegal_position_rule_anything": lambda d: d["contract"]["fen"].__setitem__(
        "illegal_position_rule", "anything"
    ),
    "illegal_position_rule_marker_dropped": lambda d: d["contract"]["fen"].__setitem__(
        "illegal_position_rule",
        "Rejected with illegal_position when both kings in check, "
        "missing king, or castling rights inconsistent with pieces.",
    ),
    "privacy_rule_removed": lambda d: d["contract"]["privacy"].pop("rule"),
    "privacy_rule_no_markers": lambda d: d["contract"]["privacy"].__setitem__(
        "rule", "Be nice with data."
    ),
    "enum_int_entry": lambda d: d["contract"]["errors"]["closed_enum"].append(5),
    "enum_bad_format": lambda d: d["contract"]["errors"]["closed_enum"].append("NotSnake"),
    "unknown_top_key": lambda d: d.__setitem__("surprise", 1),
    "unknown_contract_key": lambda d: d["contract"].__setitem__("typo_field", "x"),
    "unknown_versioning_key": lambda d: d["contract"]["versioning"].__setitem__("deprecates", "v0"),
    "unknown_identity_key": lambda d: d["contract"]["identity"].__setitem__("tiebreak", "hash"),
    "unknown_variants_key": lambda d: d["contract"]["variants"].__setitem__("default", "standard"),
    "unknown_entry_key": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "engine", "stockfish"
    ),
    "unknown_fen_key": lambda d: d["contract"]["fen"].__setitem__("normalization", "lenient"),
    "unknown_errors_key": lambda d: d["contract"]["errors"].__setitem__("http_codes", {}),
    "unknown_privacy_key": lambda d: d["contract"]["privacy"].__setitem__("telemetry", "allowed"),
    "start_fen_kingless": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQ1BNR w KQkq - 0 1"
    ),
    "start_fen_two_black_kings": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "rnbqkbkr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    ),
    "start_fen_pawn_back_rank": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "rnbqkbnP/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    ),
    "start_fen_adjacent_kings": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "8/8/8/8/8/8/4k3/4K3 w - - 0 1"
    ),
    "start_fen_waiting_side_in_check": lambda d: d["contract"]["variants"]["entries"][
        0
    ].__setitem__("start_fen", "4k3/4R3/8/8/8/8/8/4K3 w - - 0 1"),
    "start_fen_castling_without_rook": lambda d: d["contract"]["variants"]["entries"][
        0
    ].__setitem__("start_fen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN1 w KQkq - 0 1"),
    "added_variant_kingless_start": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "kingless",
            "name": "Kingless",
            "start_fen": "8/8/8/8/8/8/8/8 w - - 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_mutation_is_rejected(name):
    with pytest.raises(ContractError):
        lint(_mutate(MUTATIONS[name]))


def test_non_dict_document_is_rejected():
    with pytest.raises(ContractError):
        lint(["not", "a", "mapping"])
