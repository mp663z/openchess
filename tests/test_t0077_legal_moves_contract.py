"""T0077: legal-moves contract battery - the lint must PROVE the normative
content of data/contracts/legal_moves.yaml by exact structured comparison.
Every rule family gets contradiction AND reversal mutations; every typed
leaf gets the bool/int conflation treatment; linkage mutations run
against mutated COPIES of the linked artifacts in a temp root, never
against the real ones. A mutation that passes is a hole."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.legal_moves_contract_lint import CONTRACT, lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load(CONTRACT.read_text())

FC_COPY = ["malformed_move", "no_piece", "not_players_piece",
           "unreachable_target", "promotion_missing", "promotion_forbidden",
           "leaves_king_attacked", "wrong_variant"]
ROOK_DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]]


def _mut(fn, where: str):
    doc = copy.deepcopy(DOC)
    fn(doc["contract"] if where.startswith("contract") else doc)
    return doc


def _bad(fn, where: str = "contract"):
    doc = _mut(fn, where)
    with pytest.raises(ContractError) as ei:
        lint(doc)
    assert str(ei.value).startswith("legal-moves contract:"), str(ei.value)
    return str(ei.value)


def test_real_contract_lints_clean():
    lint(copy.deepcopy(DOC))


def test_unknown_keys_rejected_at_every_level():
    _bad(lambda d: d.__setitem__("bogus", 1), "top")
    _bad(lambda d: d.__setitem__("bogus", 1))
    _bad(lambda d: d["move_model"].__setitem__("bogus", 1))
    _bad(lambda d: d["move_model"]["shape"].__setitem__("bogus", 1))
    _bad(lambda d: d["move_model"]["shape"]["types"].__setitem__("bogus", 1))
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__("bogus", 1))
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["knight"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"]["forward"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"]["forward"]["white"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"]["double"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"]["capture_deltas"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"]["promotion_ranks"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("bogus", 1))
    _bad(lambda d: d["attack"].__setitem__("bogus", 1))
    _bad(lambda d: d["attack"]["target_occupancy"].__setitem__("bogus", 1))
    _bad(lambda d: d["legality"].__setitem__("bogus", 1))
    _bad(lambda d: d["move_set_terminal_status"].__setitem__("bogus", 1))
    _bad(lambda d: d["move_set_terminal_status"]["checkmate"].__setitem__("bogus", 1))
    _bad(lambda d: d["move_set_terminal_status"]["other_outcomes"].__setitem__("bogus", 1))
    _bad(lambda d: d["linkage"].__setitem__("bogus", 1))
    _bad(lambda d: d["errors"].__setitem__("bogus", 1))
    _bad(lambda d: d["versioning"].__setitem__("bogus", 1))


def test_schema_version_exact_int():
    for bad in (1.0, "1", True, 2, None):
        _bad(lambda d, b=bad: d.__setitem__("schema_version", b), "top")


def test_contract_id_exact():
    _bad(lambda d: d.__setitem__("id", "chess-legal-moves-v2"))
    _bad(lambda d: d.__setitem__("id", "chess-legalmove"))


def test_move_shape_exact():
    _bad(lambda d: d["move_model"]["shape"].__setitem__("required", ["from_square"]))
    _bad(lambda d: d["move_model"]["shape"].__setitem__(
        "required", ["to_square", "from_square"]))  # reversal
    _bad(lambda d: d["move_model"]["shape"].__setitem__(
        "required", ["from_square", "to_square", "promotion"]))  # promotion not required
    _bad(lambda d: d["move_model"]["shape"].__setitem__("optional", []))
    _bad(lambda d: d["move_model"]["shape"].__setitem__("closed_keys", False))  # reversal
    _bad(lambda d: d["move_model"]["shape"].__setitem__("closed_keys", "true"))  # string
    _bad(lambda d: d["move_model"]["shape"].__setitem__("closed_keys", 1))  # int
    _bad(lambda d: d["move_model"]["shape"].__setitem__("from_to_distinct", False))  # reversal
    _bad(lambda d: d["move_model"]["shape"].__setitem__("from_to_distinct", 0))  # int
    _bad(lambda d: d["move_model"]["shape"]["types"]["from_square"].__setitem__("kind", "integer"))
    _bad(lambda d: d["move_model"]["shape"]["types"]["to_square"].__delitem__("grammar"))
    _bad(lambda d: d["move_model"]["shape"]["types"]["promotion"].__setitem__(
        "enum", ["q", "r", "b", "k"]))  # king promotable
    _bad(lambda d: d["move_model"]["shape"]["types"]["promotion"].__setitem__(
        "enum", ["n", "b", "r", "q"]))  # reversal
    _bad(lambda d: d["move_model"]["shape"]["types"]["promotion"].__setitem__(
        "presence", "always-present-null"))  # the verifier's null-promotion hole
    _bad(lambda d: d["move_model"]["shape"]["types"].__setitem__("capture", "bool"))  # extra flag


def test_square_grammar_exact():
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__("files", list("hgfedcba")))
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__("files", list("abcdefg")))
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__("files", "abcdefgh"))
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__(
        "ranks", ["1", "2", "3", "4", "5", "6", "7", "8", "9"]))  # off-board rank
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__(
        "ranks", [1, 2, 3, 4, 5, 6, 7, 8]))  # int not string
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__(
        "ranks", ["8", "7", "6", "5", "4", "3", "2", "1"]))  # reversal
    _bad(lambda d: d["move_model"]["square_grammar"].__setitem__("form", "rank-then-file"))


def test_promotion_expansion_exact():
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__("expands_to", 3))
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__("expands_to", "4"))
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__(
        "values", ["q", "r", "b", "n", "k"]))
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__(
        "values", ["n", "b", "r", "q"]))  # reversal
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__(
        "applies_to", ["quiet"]))  # capture expansion dropped
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__(
        "applies_to", ["capture", "quiet"]))  # reversal
    _bad(lambda d: d["move_model"]["promotion_expansion"].__setitem__(
        "unpromoted_last_rank_move", "allowed"))  # the verifier's unpromoted hole
    _bad(lambda d: d["move_model"].__setitem__("promotion_required", "optional"))
    _bad(lambda d: d["move_model"].__setitem__("promotion_forbidden", "captures-only"))
    _bad(lambda d: d["move_model"].__delitem__("promotion_expansion"))


def test_knight_deltas_exact_and_ordered():
    _bad(lambda d: d["movement"]["knight"].__setitem__("deltas", [[1, 2]]))
    _bad(lambda d: d["movement"]["knight"].__setitem__(
        "deltas", [[-2, -1], [-2, 1], [-1, -2], [-1, 2], [2, -1], [2, 1],
                   [1, -2], [1, 2]]))  # full reversal
    _bad(lambda d: d["movement"]["knight"].__setitem__(
        "deltas", [[1, 2], [2, 1], [-1, 2], [-2, 1], [1, -2], [2, -1],
                   [-1, -2], [-3, 0]]))  # one-defect contradiction
    _bad(lambda d: d["movement"]["knight"].__setitem__("jumps", False))
    _bad(lambda d: d["movement"]["knight"].__setitem__("jumps", "true"))
    _bad(lambda d: d["movement"]["knight"].__setitem__("jumps", 1))


def test_king_deltas_exact():
    _bad(lambda d: d["movement"]["king"].__setitem__("deltas", [[1, 0], [-1, 0]]))
    _bad(lambda d: d["movement"]["king"].__setitem__("jumps", True))  # reversal
    _bad(lambda d: d["movement"]["king"].__delitem__("deltas"))


def test_slider_directions_exact_and_ordered():
    _bad(lambda d: d["movement"]["rook"].__setitem__(
        "directions", [[0, -1], [0, 1], [-1, 0], [1, 0]]))  # reversal
    _bad(lambda d: d["movement"]["rook"].__setitem__("directions", [[1, 0]]))
    _bad(lambda d: d["movement"]["rook"].__setitem__("slides", False))
    _bad(lambda d: d["movement"]["bishop"].__setitem__("directions", ROOK_DIRS))
    _bad(lambda d: d["movement"]["bishop"].__setitem__("slides", "yes"))
    _bad(lambda d: d["movement"]["queen"].__setitem__("directions", ROOK_DIRS))
    _bad(lambda d: d["movement"]["queen"].__setitem__("slides", 0))


def test_pawn_geometry_exact():
    # forward vectors per side (finding 2)
    _bad(lambda d: d["movement"]["pawn"]["forward"]["white"].__setitem__("rank_delta", -1))
    _bad(lambda d: d["movement"]["pawn"]["forward"]["black"].__setitem__("rank_delta", 1))
    _bad(lambda d: d["movement"]["pawn"]["forward"]["white"].__setitem__("file_delta", 1))
    _bad(lambda d: d["movement"]["pawn"]["forward"]["black"].__delitem__("rank_delta"))
    _bad(lambda d: d["movement"]["pawn"]["forward"]["white"].__setitem__(
        "rank_delta", 1.0))  # float not int
    _bad(lambda d: d["movement"]["pawn"].__delitem__("forward"))
    # capture deltas per side (finding 2)
    _bad(lambda d: d["movement"]["pawn"]["capture_deltas"].__setitem__(
        "white", [[-1, 1], [1, 1]]))  # reversal
    _bad(lambda d: d["movement"]["pawn"]["capture_deltas"].__setitem__(
        "white", [[1, -1], [-1, -1]]))  # backward captures
    _bad(lambda d: d["movement"]["pawn"]["capture_deltas"].__setitem__(
        "black", [[1, 1], [-1, 1]]))  # swapped with white
    _bad(lambda d: d["movement"]["pawn"]["capture_deltas"].__delitem__("black"))
    # start ranks and promotion ranks by side (finding 2)
    _bad(lambda d: d["movement"]["pawn"]["double"]["ranks"].__setitem__("white", "3"))
    _bad(lambda d: d["movement"]["pawn"]["double"]["ranks"].__setitem__("black", 7))  # int
    _bad(lambda d: d["movement"]["pawn"]["promotion_ranks"].__setitem__("white", "7"))
    _bad(lambda d: d["movement"]["pawn"]["promotion_ranks"].__setitem__("black", "2"))
    _bad(lambda d: d["movement"]["pawn"]["promotion_ranks"].__setitem__("black", 1))  # int
    _bad(lambda d: d["movement"]["pawn"].__delitem__("promotion_ranks"))
    # remaining pawn rules
    _bad(lambda d: d["movement"]["pawn"].__setitem__("forward_empty", "any-square"))
    _bad(lambda d: d["movement"]["pawn"]["double"].__setitem__(
        "requires", ["on-start-rank"]))
    _bad(lambda d: d["movement"]["pawn"]["double"].__setitem__(
        "requires", ["both-squares-empty", "on-start-rank"]))  # reversal
    _bad(lambda d: d["movement"]["pawn"].__setitem__("capture", "any-diagonal"))
    _bad(lambda d: d["movement"]["pawn"].__setitem__("never_backward", False))
    _bad(lambda d: d["movement"]["pawn"].__setitem__("never_backward", 1))


def test_occupancy_exact():
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("own_piece_square", "reachable"))
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("enemy_piece_square", "blocked"))
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("sliding_block", "jump-over"))
    _bad(lambda d: d["movement"]["occupancy"].__delitem__("sliding_block"))
    # opponent-king-capture prohibition (T0080 v2 finding)
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("enemy_king_square", "capture-only"))
    _bad(lambda d: d["movement"]["occupancy"].__delitem__("enemy_king_square"))


def test_attack_relation_exact():
    _bad(lambda d: d["attack"].__setitem__("definition", "legal-capture-to-square"))
    _bad(lambda d: d["attack"].__setitem__("attacker_king_safety", "respected"))  # reversal
    _bad(lambda d: d["attack"].__delitem__("attacker_king_safety"))
    # structured target occupancy for empty/enemy/own targets (finding 3)
    _bad(lambda d: d["attack"]["target_occupancy"].__setitem__("empty", "not-attacked"))
    _bad(lambda d: d["attack"]["target_occupancy"].__setitem__("enemy_occupied", "not-attacked"))
    _bad(lambda d: d["attack"]["target_occupancy"].__setitem__("own_occupied", "not-attacked"))
    _bad(lambda d: d["attack"]["target_occupancy"].__delitem__("own_occupied"))
    _bad(lambda d: d["attack"].__delitem__("target_occupancy"))
    _bad(lambda d: d["attack"].__setitem__("king_attacks", "no-squares"))
    _bad(lambda d: d["attack"].__setitem__("pawn_attacks", "forward-square"))
    # castling transit bound to THIS relation (finding 3)
    _bad(lambda d: d["attack"].__setitem__("castling_transit", "castling-decides-its-own"))
    _bad(lambda d: d["attack"].__delitem__("castling_transit"))


def test_legality_filter_exact():
    _bad(lambda d: d["legality"].__setitem__("filter", "pseudo-legal-is-legal"))
    _bad(lambda d: d["legality"].__setitem__("in_check_rule", "any-move-legal"))
    _bad(lambda d: d["legality"].__delitem__("filter"))
    _bad(lambda d: d["legality"].__setitem__("opponent_king_capture", "allowed"))
    _bad(lambda d: d["legality"].__delitem__("opponent_king_capture"))


def test_move_set_terminal_status_exact():
    # section renamed and scoped to move-set classification (finding 4)
    _bad(lambda d: d.__setitem__("terminal_status", d.pop("move_set_terminal_status")))
    _bad(lambda d: d["move_set_terminal_status"].__setitem__(
        "yields", ["checkmate", "stalemate", "check"]))  # reversal
    _bad(lambda d: d["move_set_terminal_status"].__setitem__("yields", ["checkmate", "stalemate"]))
    _bad(lambda d: d["move_set_terminal_status"].__setitem__(
        "yields", ["check", "checkmate", "stalemate", "resignation"]))  # scope creep
    _bad(lambda d: d["move_set_terminal_status"].__setitem__("check", "any-king-attacked"))
    _bad(lambda d: d["move_set_terminal_status"]["checkmate"].__setitem__(
        "requires", ["check"]))
    _bad(lambda d: d["move_set_terminal_status"]["checkmate"].__setitem__(
        "requires", ["zero-legal-moves", "check"]))  # reversal
    _bad(lambda d: d["move_set_terminal_status"]["stalemate"].__setitem__(
        "requires", ["check", "zero-legal-moves"]))  # contradiction
    _bad(lambda d: d["move_set_terminal_status"].__setitem__("scope", "all-terminal-outcomes"))
    _bad(lambda d: d["move_set_terminal_status"]["other_outcomes"].__setitem__(
        "owner", "this-contract"))
    _bad(lambda d: d["move_set_terminal_status"]["other_outcomes"].__setitem__(
        "states", ["resignation", "timeout"]))  # shrink
    _bad(lambda d: d["move_set_terminal_status"]["other_outcomes"].__setitem__(
        "states", list(reversed(["resignation", "timeout", "draw_agreement",
                                 "fifty_move_claim", "seventyfive_move_auto",
                                 "fivefold_auto", "threefold_claim",
                                 "insufficient_material", "variant_specific"]))))
    _bad(lambda d: d["move_set_terminal_status"].__delitem__("other_outcomes"))


def test_linkage_exact():
    _bad(lambda d: d["linkage"].__setitem__("castling", "restated-here"))
    _bad(lambda d: d["linkage"].__setitem__(
        "turn", "turn-contract-owns-clocks-and-advance"))  # pre-rename scope
    _bad(lambda d: d["linkage"].__delitem__("en_passant"))


def test_failure_classes_and_mapping_exact():
    _bad(lambda d: d.__setitem__("failure_classes", list(reversed(FC_COPY))))
    _bad(lambda d: d.__setitem__("failure_classes", FC_COPY[:-1]))
    _bad(lambda d: d["failure_mapping"].__delitem__("leaves_king_attacked"))
    _bad(lambda d: d["failure_mapping"]["no_piece"].__setitem__("error", "malformed_request"))
    _bad(lambda d: d["failure_mapping"]["malformed_move"].__setitem__("error", "illegal_move"))
    _bad(lambda d: d["failure_mapping"]["malformed_move"].__setitem__(
        "trigger", "move-not-the-declared-field-shape"))  # label, not schema reference
    _bad(lambda d: d["failure_mapping"]["promotion_missing"].__setitem__(
        "trigger", "pawn-reaches-last-rank-without-promotion-piece"))  # stale label
    _bad(lambda d: d["failure_mapping"]["promotion_forbidden"].__setitem__("trigger", "anything"))


def test_error_enum_and_shape_exact():
    _bad(lambda d: d["errors"].__setitem__("closed_enum", ["illegal_move", "internal"]))
    _bad(lambda d: d["errors"].__setitem__(
        "closed_enum", ["internal", "illegal_move", "malformed_request"]))  # reversal
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["code"].__setitem__("required", False))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["retryable"].__setitem__(
        "type", "string"))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"].__delitem__("message"))


def test_versioning_exact():
    """Versioning is reconciled with the CLOSED move object: MINOR carries
    zero schema additions, every structured section is a closed location,
    and any new move member is a MAJOR bump. The false 'conformance never
    rejects extras' rationale is gone; every claim is structured and
    exactly compared."""
    _bad(lambda d: d["versioning"].__setitem__("base_path", "/legal-moves/v2"))
    _bad(lambda d: d["versioning"].__setitem__("client_pin", "MINOR"))
    _bad(lambda d: d["versioning"].__setitem__(
        "minor_means", "additive-optional-fields"))  # contradiction
    _bad(lambda d: d["versioning"].__setitem__("minor_means", ""))
    # allowing move fields in MINOR must fail (verifier mutation)
    _bad(lambda d: d["versioning"].__setitem__("minor_additions", ["new-optional-fields"]))
    _bad(lambda d: d["versioning"].__setitem__("minor_additions", ["move_model.shape.promotion"]))
    _bad(lambda d: d["versioning"].__setitem__("extensible_locations", ["move_model.shape"]))
    _bad(lambda d: d["versioning"].__setitem__("extensible_locations", ["movement.pawn"]))
    # closed_locations must cover the whole structured surface
    _bad(lambda d: d["versioning"].__setitem__(
        "closed_locations", [loc for loc in d["versioning"]["closed_locations"]
                             if loc != "move_model.shape"]))
    _bad(lambda d: d["versioning"].__setitem__(
        "closed_locations", list(reversed(d["versioning"]["closed_locations"]))))
    _bad(lambda d: d["versioning"].__setitem__("closed_locations", []))
    _bad(lambda d: d["versioning"].__setitem__("new_move_member", "minor-allowed"))
    _bad(lambda d: d["versioning"].__setitem__("downgrade_policy", "never"))
    # contradictory compatibility claim (the verifier's false rationale)
    _bad(lambda d: d["versioning"].__setitem__(
        "downgrade_rationale", "conformance-never-asserts-absence-of-extra-fields"))
    _bad(lambda d: d["versioning"].__setitem__("downgrade_rationale", "required-fields-are-minor"))
    _bad(lambda d: d["versioning"].__setitem__("major_bump", "silent"))
    for key in ("minor_means", "minor_additions", "extensible_locations",
                "closed_locations", "new_move_member", "downgrade_rationale"):
        _bad(lambda d, k=key: d["versioning"].__delitem__(k))


def test_documentation_fields_nonempty():
    for section in ("move_model", "movement", "attack", "legality",
                    "move_set_terminal_status", "linkage", "versioning"):
        _bad(lambda d, s=section: d[s].__setitem__("rule", ""))
        _bad(lambda d, s=section: d[s].__setitem__("rule", "   "))
        _bad(lambda d, s=section: d[s].__setitem__("rule", 3))
        _bad(lambda d, s=section: d[s].__delitem__("rule"))


def _write_linked_root(tmp_path: Path, docs: dict) -> Path:
    root = tmp_path / "repo"
    (root / "data" / "contracts").mkdir(parents=True)
    for name, doc in docs.items():
        (root / "data" / "contracts" / f"{name}.yaml").write_text(yaml.safe_dump(doc))
    return root


def _real_contracts() -> dict:
    return {name: yaml.safe_load((ROOT / "data" / "contracts" / f"{name}.yaml").read_text())
            for name in ("variant", "turn", "castling", "en_passant")}


def test_linkage_mutations_rejected(tmp_path):
    docs = _real_contracts()

    bad_variant = copy.deepcopy(docs["variant"])
    bad_variant["contract"]["identity"]["canonical_fields"] = [
        f for f in bad_variant["contract"]["identity"]["canonical_fields"]
        if f != "side_to_move"
    ]
    d = dict(docs, variant=bad_variant)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "a", d))

    bad_turn = copy.deepcopy(docs["turn"])
    bad_turn["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_when"] = [
        "pawn_move"  # capture dropped: legal moves include captures
    ]
    d = dict(docs, turn=bad_turn)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "b", d))

    # turn loses ownership of a non-move-set outcome (finding 4)
    bad_turn2 = copy.deepcopy(docs["turn"])
    bad_turn2["contract"]["termination"]["states"] = [
        s for s in bad_turn2["contract"]["termination"]["states"]
        if s != "insufficient_material"
    ]
    d = dict(docs, turn=bad_turn2)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "b2", d))

    # turn loses a move-set outcome it must accept (finding 4)
    bad_turn3 = copy.deepcopy(docs["turn"])
    bad_turn3["contract"]["termination"]["states"] = [
        s for s in bad_turn3["contract"]["termination"]["states"] if s != "checkmate"
    ]
    d = dict(docs, turn=bad_turn3)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "b3", d))

    bad_castling = copy.deepcopy(docs["castling"])
    bad_castling["contract"]["rights"]["irrevocable"] = False
    d = dict(docs, castling=bad_castling)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "c", d))

    # castling path without king_transit cannot apply the attack relation
    bad_castling2 = copy.deepcopy(docs["castling"])
    del bad_castling2["contract"]["move"]["per_side_paths"]["K"]["king_transit"]
    d = dict(docs, castling=bad_castling2)
    with pytest.raises((ContractError, KeyError)):
        # castling's own lint may reject the shape first; either way the
        # combination is refused, never silently accepted
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "c2", d))

    bad_ep = copy.deepcopy(docs["en_passant"])
    bad_ep["contract"]["failure_classes"] = [
        f for f in bad_ep["contract"]["failure_classes"] if f != "pinned_capture"
    ]
    bad_ep["contract"]["failure_mapping"] = {
        k: v for k, v in bad_ep["contract"]["failure_mapping"].items()
        if k != "pinned_capture"
    }
    d = dict(docs, en_passant=bad_ep)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "d", d))

    malformed_castling = {"schema_version": 1, "contract": {"id": "chess-castling"}}
    d = dict(docs, castling=malformed_castling)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "e", d))


def test_links_exact_paths():
    _bad(lambda d: d["links"].__setitem__("variant_contract", "data/contracts/other.yaml"))
    _bad(lambda d: d["links"].__delitem__("turn_contract"))
    _bad(lambda d: d["links"].__delitem__("castling_contract"))
    _bad(lambda d: d["links"].__delitem__("en_passant_contract"))


def test_missing_and_wrong_container_family():
    """The whole malformed-SHAPE family converts to ContractError with
    the advertised prefix - no raw KeyError/AttributeError escapes."""
    cases = [
        lambda d: d["contract"].__delitem__("move_model"),
        lambda d: d["contract"]["move_model"].__delitem__("shape"),
        lambda d: d["contract"]["move_model"].__delitem__("square_grammar"),
        lambda d: d["contract"]["move_model"].__delitem__("promotion_expansion"),
        lambda d: d["contract"].__delitem__("movement"),
        lambda d: d["contract"]["movement"].__delitem__("knight"),
        lambda d: d["contract"]["movement"].__delitem__("pawn"),
        lambda d: d["contract"]["movement"]["pawn"].__delitem__("double"),
        lambda d: d["contract"].__delitem__("attack"),
        lambda d: d["contract"]["attack"].__delitem__("target_occupancy"),
        lambda d: d["contract"].__delitem__("legality"),
        lambda d: d["contract"].__delitem__("move_set_terminal_status"),
        lambda d: d["contract"].__delitem__("linkage"),
        lambda d: d["contract"].__delitem__("errors"),
        lambda d: d["contract"].__delitem__("links"),
        lambda d: d["contract"].__delitem__("failure_mapping"),
        lambda d: d["contract"].__delitem__("failure_classes"),
        lambda d: d["contract"].__delitem__("versioning"),
        lambda d: d.__setitem__("contract", []),
        lambda d: d["contract"].__setitem__("movement", "e4"),
        lambda d: d["contract"]["movement"].__setitem__("knight", []),
        lambda d: d["contract"]["attack"].__setitem__("definition", None),
        lambda d: d["contract"]["move_model"].__setitem__("shape", "a1"),
    ]
    for fn in cases:
        doc = copy.deepcopy(DOC)
        fn(doc)
        with pytest.raises(ContractError) as ei:
            lint(doc)
        assert str(ei.value).startswith("legal-moves contract:"), str(ei.value)


def test_cli_boundary_clean_and_failing():
    ok = subprocess.run([sys.executable, "tools/legal_moves_contract_lint.py"],
                        cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0 and "OK legal-moves contract lint" in ok.stdout
    bad_doc = copy.deepcopy(DOC)
    bad_doc["contract"]["legality"]["filter"] = "pseudo-legal-is-legal"
    bad_path = ROOT / "data" / "contracts" / ".tmp_bad_legal_moves.yaml"
    try:
        bad_path.write_text(yaml.safe_dump(bad_doc))
        fail = subprocess.run(
            [sys.executable, "tools/legal_moves_contract_lint.py", str(bad_path)],
            cwd=ROOT, capture_output=True, text=True)
        assert fail.returncode == 1
        assert "legal-moves contract:" in fail.stderr
        assert "Traceback" not in fail.stderr
    finally:
        bad_path.unlink(missing_ok=True)
