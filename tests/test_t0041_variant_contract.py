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

MUTATIONS_V3 = {
    # verifier probes on a6da578 - each previously passed
    "ep_impossible_no_pawns": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/8/8/8/8/8/8/K7 w - e3 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "ep_wrong_side_to_move": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen",
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e3 0 2",
    ),
    "ep_no_double_stepped_pawn": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/8/8/8/8/8/8/K7 b - e3 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "ep_no_capturing_pawn": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/8/8/8/4P3/8/8/K7 b - e3 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "ep_origin_not_empty": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/8/8/8/3pP3/8/4P3/K7 b - e3 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "chess960_with_castling_rights": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "f960",
            "name": "F960",
            "start_fen": "bbqnrkrn/pppppppp/8/8/8/8/PPPPPPPP/BBQNRKRN w KQkq - 0 1",
            "castling": "chess960",
            "status": "experimental",
        }
    ),
    "base_path_alpha_major": lambda d: d["contract"]["versioning"].__setitem__(
        "base_path", "/variant/vXYZ"
    ),
    "base_path_zero_major": lambda d: d["contract"]["versioning"].__setitem__(
        "base_path", "/variant/v0"
    ),
}

MUTATIONS.update(MUTATIONS_V3)

MUTATIONS_V4 = {
    # verifier probes on a094a1e - each previously passed
    "ep_capture_pinned_black": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "3k4/8/8/8/3pP3/8/8/3RK3 b - e3 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "ep_capture_pinned_white": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "3r3k/8/8/3Pp3/8/8/8/3K4 w - e6 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "ep_capture_pinned_b_file": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "1k6/8/8/8/Pp6/8/8/KR6 b - a3 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "nine_white_pawns": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/8/8/8/8/P7/PPPPPPPP/K7 w - - 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "nine_black_pawns": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/1p6/pppppppp/8/8/8/8/K7 w - - 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "seventeen_black_pieces": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "rnbqkbnr/pppppppp/n7/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
}

MUTATIONS.update(MUTATIONS_V4)

MUTATIONS_V5 = {
    # verifier probes on 8187e49 - each previously crashed or passed
    "ep_missing_black_king": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "8/8/8/8/3pP3/8/8/K7 b - e3 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "ep_missing_white_king": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/8/8/3pP3/8/8/8/8 w - d6 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "ep_two_white_kings": lambda d: d["contract"]["variants"]["entries"].append(
        {
            "id": "foo",
            "name": "Foo",
            "start_fen": "7k/8/8/3pP3/8/8/8/KK6 w - d6 0 1",
            "castling": "orthodox",
            "status": "experimental",
        }
    ),
    "board_digit_zero": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "07k/8/8/8/8/8/8/K7 w - - 0 1"
    ),
    "board_digit_nine": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "9k/8/8/8/8/8/8/K7 w - - 0 1"
    ),
    "board_adjacent_digits": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen", "11k5/8/8/8/8/8/8/K7 w - - 0 1"
    ),
    "counters_leading_zeros": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 00 01",
    ),
}

MUTATIONS.update(MUTATIONS_V5)

MUTATIONS_V6 = {
    # verifier probes on 44503a3
    "counter_fullmove_huge": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 " + "9" * 5000,
    ),
    "counter_halfmove_huge": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(
        "start_fen",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - " + "9" * 5000 + " 1",
    ),
    "mixed_type_top_key": lambda d: d.__setitem__(5, "x"),
    "mixed_type_contract_key": lambda d: d["contract"].__setitem__(7, "x"),
    "mixed_type_entry_key": lambda d: d["contract"]["variants"]["entries"][0].__setitem__(9, "x"),
}

MUTATIONS.update(MUTATIONS_V6)

MUTATIONS_V7 = {
    # verifier probes on 6691a2a - nested error-shape levels
    "mixed_key_errors_shape": lambda d: d["contract"]["errors"]["shape"].__setitem__(5, "x"),
    "mixed_key_errors_shape_error": lambda d: d["contract"]["errors"]["shape"]["error"].__setitem__(
        5, "x"
    ),
    "mixed_key_errors_shape_fields": lambda d: d["contract"]["errors"]["shape"]["error"][
        "fields"
    ].__setitem__(5, "x"),
}

MUTATIONS.update(MUTATIONS_V7)


def _doc_with_start(fen, castling="orthodox"):
    doc = copy.deepcopy(DOC)
    doc["contract"]["variants"]["entries"].append(
        {
            "id": "probe",
            "name": "Probe",
            "start_fen": fen,
            "castling": castling,
            "status": "experimental",
        }
    )
    return doc


def test_valid_en_passant_start_passes():
    # white double-stepped e2-e4; black pawn d4 can capture - reachable
    lint(_doc_with_start("7k/8/8/8/3pP3/8/8/K7 b - e3 0 1"))
    # black double-stepped d7-d5; white pawn e5 can capture
    lint(_doc_with_start("K7/8/8/3pP3/8/8/8/7k w - d6 0 1"))


def test_promotion_aware_material_passes():
    # two queens (promotion) with few pieces is fine: pawn count and
    # total-piece supply are the hard limits
    lint(_doc_with_start("7k/8/8/8/8/8/8/KQQ5 w - - 0 1"))


def test_ep_capture_unpinned_passes():
    # adjacent capturer whose capture is legal (no pin) still passes
    lint(_doc_with_start("3k4/8/8/8/3pP3/8/8/4K3 b - e3 0 1"))


def test_chess960_without_rights_passes():
    lint(
        _doc_with_start(
            "bbqnrkrn/pppppppp/8/8/8/8/PPPPPPPP/BBQNRKRN w - - 0 1",
            castling="chess960",
        )
    )


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_mutation_is_rejected(name):
    with pytest.raises(ContractError):
        lint(_mutate(MUTATIONS[name]))


def test_non_dict_document_is_rejected():
    with pytest.raises(ContractError):
        lint(["not", "a", "mapping"])


CLI_MATRIX = [
    "8/8/8/8/3pP3/8/8/K7 b - e3 0 1",  # missing black king
    "7k/8/8/3pP3/8/8/8/8 w - d6 0 1",  # missing white king
    "07k/8/8/8/8/8/8/K7 w - - 0 1",  # digit 0
    "7k/8/8/8/8/8/8/K7 w - - 0 " + "9" * 5000,  # oversized fullmove
    "7k/8/8/8/8/8/8/K7 w - - " + "9" * 5000 + " 1",  # oversized halfmove
]


@pytest.mark.parametrize("fen", CLI_MATRIX)
def test_cli_malformed_matrix(tmp_path, capsys, fen):
    from tools.variant_contract_lint import main

    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(_doc_with_start(fen)))
    assert main(["prog", str(path)]) == 1
    out = capsys.readouterr()
    assert out.out.startswith("FAIL variant contract lint:")
    assert "Traceback" not in out.err


@pytest.mark.parametrize(
    "level", ["top", "contract", "entry", "shape", "shape_error", "shape_fields"]
)
def test_cli_mixed_type_keys(tmp_path, capsys, level):
    from tools.variant_contract_lint import main

    doc = copy.deepcopy(DOC)
    if level == "top":
        doc[5] = "x"
    elif level == "contract":
        doc["contract"][5] = "x"
    elif level == "entry":
        doc["contract"]["variants"]["entries"][0][5] = "x"
    elif level == "shape":
        doc["contract"]["errors"]["shape"][5] = "x"
    elif level == "shape_error":
        doc["contract"]["errors"]["shape"]["error"][5] = "x"
    else:
        doc["contract"]["errors"]["shape"]["error"]["fields"][5] = "x"
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(doc))
    assert main(["prog", str(path)]) == 1
    out = capsys.readouterr()
    assert out.out.startswith("FAIL variant contract lint:")
    assert "non-string key" in out.out  # classified, never "internal error"
    assert "internal error" not in out.out
    assert "Traceback" not in out.err


def test_cli_unhashable_yaml_key(tmp_path, capsys):
    from tools.variant_contract_lint import main

    path = tmp_path / "contract.yaml"
    path.write_text("?\n- 1\n- 2\n: value\n")
    assert main(["prog", str(path)]) == 1
    out = capsys.readouterr()
    assert out.out.startswith("FAIL variant contract lint:")
    assert "Traceback" not in out.err


def test_cli_rejects_malformed_without_traceback(tmp_path, capsys):
    """Every malformed contract must exit 1 with a classified FAIL, not
    an uncaught exception."""
    from tools.variant_contract_lint import main

    for fen in (
        "8/8/8/8/3pP3/8/8/K7 b - e3 0 1",  # missing black king (StopIteration probe)
        "7k/8/8/3pP3/8/8/8/8 w - d6 0 1",  # missing white king
        "07k/8/8/8/8/8/8/K7 w - - 0 1",  # digit 0
    ):
        doc = _doc_with_start(fen)
        path = tmp_path / "contract.yaml"
        path.write_text(yaml.safe_dump(doc))
        assert main(["prog", str(path)]) == 1
        out = capsys.readouterr()
        assert out.out.startswith("FAIL variant contract lint:")
        assert "Traceback" not in out.err


# --- recursive strict equality: bool/int conflation matrix (hardening) ---


def _set_path(doc: dict, path: tuple, value: object) -> None:
    node = doc
    for segment in path[:-1]:
        node = node[segment]
    node[path[-1]] = value


SHAPE_LEAVES = [
    ("contract", "errors", "shape", "error", "fields", field, leaf)
    for field in ("code", "message", "retryable")
    for leaf in ("type", "required")
]


@pytest.mark.parametrize(
    "path,value",
    [
        *((p, 1) for p in SHAPE_LEAVES if p[-1] == "required"),
        *((p, 0) for p in SHAPE_LEAVES if p[-1] == "required"),
        *((p, True) for p in SHAPE_LEAVES if p[-1] == "type"),
        *((p, 1) for p in SHAPE_LEAVES if p[-1] == "type"),
        (("contract", "schema_version"), True),
    ],
)
def test_bool_int_conflation_rejected_at_shape_leaves(path, value):
    """Ordinary == accepts 1 for True and 0 for False; the exact error
    shape (and schema_version) must not."""
    doc = _mutate(lambda d: _set_path(d, path, value))
    with pytest.raises(ContractError):
        lint(doc)
