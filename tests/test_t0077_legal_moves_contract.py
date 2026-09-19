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
    _bad(lambda d: d["movement"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["knight"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["pawn"]["double"].__setitem__("bogus", 1))
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("bogus", 1))
    _bad(lambda d: d["attack"].__setitem__("bogus", 1))
    _bad(lambda d: d["legality"].__setitem__("bogus", 1))
    _bad(lambda d: d["terminal_status"].__setitem__("bogus", 1))
    _bad(lambda d: d["terminal_status"]["checkmate"].__setitem__("bogus", 1))
    _bad(lambda d: d["linkage"].__setitem__("bogus", 1))
    _bad(lambda d: d["errors"].__setitem__("bogus", 1))
    _bad(lambda d: d["versioning"].__setitem__("bogus", 1))


def test_schema_version_exact_int():
    for bad in (1.0, "1", True, 2, None):
        _bad(lambda d, b=bad: d.__setitem__("schema_version", b), "top")


def test_contract_id_exact():
    _bad(lambda d: d.__setitem__("id", "chess-legal-moves-v2"))
    _bad(lambda d: d.__setitem__("id", "chess-legalmove"))


def test_move_model_exact():
    _bad(lambda d: d["move_model"].__setitem__("fields", ["from_square", "to_square"]))
    _bad(lambda d: d["move_model"].__setitem__(
        "fields", ["to_square", "from_square", "promotion"]))  # reversal
    _bad(lambda d: d["move_model"].__setitem__("promotion_values", ["q", "r", "b", "k"]))
    _bad(lambda d: d["move_model"].__setitem__(
        "promotion_values", ["n", "b", "r", "q"]))  # reversal
    _bad(lambda d: d["move_model"].__setitem__("promotion_values", "qrbn"))  # string
    _bad(lambda d: d["move_model"].__setitem__("promotion_required", "optional"))
    _bad(lambda d: d["move_model"].__setitem__("promotion_forbidden", "captures-only"))
    _bad(lambda d: d["move_model"].__delitem__("promotion_required"))


def test_knight_deltas_exact_and_ordered():
    _bad(lambda d: d["movement"]["knight"].__setitem__("deltas", [[1, 2]]))  # shrink
    _bad(lambda d: d["movement"]["knight"].__setitem__(
        "deltas", [[-2, -1], [-2, 1], [-1, -2], [-1, 2], [2, -1], [2, 1],
                   [1, -2], [1, 2]]))  # full reversal
    _bad(lambda d: d["movement"]["knight"].__setitem__(
        "deltas", [[1, 2], [2, 1], [-1, 2], [-2, 1], [1, -2], [2, -1],
                   [-1, -2], [-3, 0]]))  # one-defect contradiction
    _bad(lambda d: d["movement"]["knight"].__setitem__("jumps", False))
    _bad(lambda d: d["movement"]["knight"].__setitem__("jumps", "true"))  # string not bool
    _bad(lambda d: d["movement"]["knight"].__setitem__("jumps", 1))  # int not bool


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
    _bad(lambda d: d["movement"]["queen"].__setitem__("directions", ROOK_DIRS))  # diagonals dropped
    _bad(lambda d: d["movement"]["queen"].__setitem__("slides", 0))  # int not bool


def test_pawn_rules_exact():
    _bad(lambda d: d["movement"]["pawn"].__setitem__("forward_empty", "any-square"))
    _bad(lambda d: d["movement"]["pawn"]["double"].__setitem__(
        "requires", ["on-start-rank"]))  # both-squares-empty dropped
    _bad(lambda d: d["movement"]["pawn"]["double"].__setitem__(
        "requires", ["both-squares-empty", "on-start-rank"]))  # reversal
    _bad(lambda d: d["movement"]["pawn"]["double"]["ranks"].__setitem__("white", "3"))
    _bad(lambda d: d["movement"]["pawn"]["double"]["ranks"].__setitem__("black", 7))  # int
    _bad(lambda d: d["movement"]["pawn"]["double"]["ranks"].__setitem__("black", "2"))  # swap
    _bad(lambda d: d["movement"]["pawn"].__setitem__("capture", "any-diagonal"))
    _bad(lambda d: d["movement"]["pawn"].__setitem__("never_backward", False))  # reversal
    _bad(lambda d: d["movement"]["pawn"].__setitem__("never_backward", 1))  # int not bool


def test_occupancy_exact():
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("own_piece_square", "reachable"))
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("enemy_piece_square", "blocked"))
    _bad(lambda d: d["movement"]["occupancy"].__setitem__("sliding_block", "jump-over"))
    _bad(lambda d: d["movement"]["occupancy"].__delitem__("sliding_block"))


def test_attack_relation_exact():
    _bad(lambda d: d["attack"].__setitem__("definition", "legal-capture-to-square"))
    _bad(lambda d: d["attack"].__setitem__("attacker_king_safety", "respected"))  # reversal
    _bad(lambda d: d["attack"].__setitem__("king_attacks", "no-squares"))
    _bad(lambda d: d["attack"].__setitem__("pawn_attacks", "forward-square"))
    _bad(lambda d: d["attack"].__delitem__("attacker_king_safety"))


def test_legality_filter_exact():
    _bad(lambda d: d["legality"].__setitem__("filter", "pseudo-legal-is-legal"))
    _bad(lambda d: d["legality"].__setitem__("in_check_rule", "any-move-legal"))
    _bad(lambda d: d["legality"].__delitem__("filter"))


def test_terminal_status_exact():
    _bad(lambda d: d["terminal_status"].__setitem__("check", "any-king-attacked"))
    _bad(lambda d: d["terminal_status"]["checkmate"].__setitem__(
        "requires", ["check"]))  # zero-legal-moves dropped
    _bad(lambda d: d["terminal_status"]["checkmate"].__setitem__(
        "requires", ["zero-legal-moves", "check"]))  # reversal
    _bad(lambda d: d["terminal_status"]["stalemate"].__setitem__(
        "requires", ["check", "zero-legal-moves"]))  # contradiction
    _bad(lambda d: d["terminal_status"].__delitem__("stalemate"))


def test_linkage_exact():
    _bad(lambda d: d["linkage"].__setitem__("castling", "restated-here"))
    _bad(lambda d: d["linkage"].__setitem__("turn", "turn-ignored"))
    _bad(lambda d: d["linkage"].__delitem__("en_passant"))


def test_failure_classes_and_mapping_exact():
    _bad(lambda d: d.__setitem__("failure_classes", list(reversed(FC_COPY))))
    _bad(lambda d: d.__setitem__("failure_classes", FC_COPY[:-1]))
    _bad(lambda d: d["failure_mapping"].__delitem__("leaves_king_attacked"))
    _bad(lambda d: d["failure_mapping"]["no_piece"].__setitem__("error", "malformed_request"))
    _bad(lambda d: d["failure_mapping"]["malformed_move"].__setitem__("error", "illegal_move"))
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
    _bad(lambda d: d["versioning"].__setitem__("base_path", "/legal-moves/v2"))
    _bad(lambda d: d["versioning"].__setitem__("client_pin", "MINOR"))
    _bad(lambda d: d["versioning"].__setitem__("minor_policy", "anything-goes"))
    _bad(lambda d: d["versioning"].__setitem__("minor_additions", ["breaking-changes"]))
    _bad(lambda d: d["versioning"].__setitem__("downgrade_policy", "never"))
    _bad(lambda d: d["versioning"].__setitem__("major_bump", "silent"))


def test_documentation_fields_nonempty():
    for section in ("move_model", "movement", "attack", "legality",
                    "terminal_status", "linkage", "versioning"):
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

    bad_castling = copy.deepcopy(docs["castling"])
    bad_castling["contract"]["rights"]["irrevocable"] = False
    d = dict(docs, castling=bad_castling)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), _write_linked_root(tmp_path / "c", d))

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
        lambda d: d["contract"].__delitem__("movement"),
        lambda d: d["contract"]["movement"].__delitem__("knight"),
        lambda d: d["contract"]["movement"].__delitem__("pawn"),
        lambda d: d["contract"]["movement"]["pawn"].__delitem__("double"),
        lambda d: d["contract"].__delitem__("attack"),
        lambda d: d["contract"].__delitem__("legality"),
        lambda d: d["contract"].__delitem__("terminal_status"),
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
