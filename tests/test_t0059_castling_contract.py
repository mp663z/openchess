"""T0059: castling contract battery - the lint must PROVE the normative
content of data/contracts/castling.yaml by exact structured comparison.
Every rule family gets contradiction AND reversal mutations; every
typed leaf gets the bool/int conflation treatment; linkage mutations
run against mutated COPIES of the linked artifacts in a temp root,
never against the real ones. A mutation that passes is a hole."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.castling_contract_lint import CONTRACT, lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load(CONTRACT.read_text())


def _mut(fn, where: str):
    doc = copy.deepcopy(DOC)
    fn(doc["contract"] if where.startswith("contract") else doc)
    return doc


def _bad(fn, where: str = "contract"):
    doc = _mut(fn, where)
    with pytest.raises(ContractError) as ei:
        lint(doc)
    assert str(ei.value).startswith("castling contract:"), str(ei.value)
    return str(ei.value)


def test_real_contract_lints_clean():
    lint(copy.deepcopy(DOC))


def test_unknown_keys_rejected_at_every_level():
    _bad(lambda d: d.__setitem__("bogus", 1), "top")
    _bad(lambda d: d["rights"].__setitem__("bogus", 1))
    _bad(lambda d: d["loss"].__setitem__("bogus", 1))
    _bad(lambda d: d["move"].__setitem__("bogus", 1))
    _bad(lambda d: d["move"]["per_side_paths"]["K"].__setitem__("bogus", 1))
    _bad(lambda d: d["identity"].__setitem__("bogus", 1))


def test_schema_version_exact_int():
    for bad in (1.0, "1", True, 2, None):
        _bad(lambda d, b=bad: d.__setitem__("schema_version", b), "top")


def test_contract_id_exact():
    _bad(lambda d: d.__setitem__("id", "chess-castle"))
    _bad(lambda d: d.__setitem__("id", "chess-castling-v2"))


def test_rights_values_exact_and_ordered():
    _bad(lambda d: d["rights"].__setitem__("values", ["Q", "K", "k", "q"]))  # reversal
    _bad(lambda d: d["rights"].__setitem__("values", ["K", "Q", "k"]))  # shrink
    _bad(lambda d: d["rights"].__setitem__("values", ["K", "Q", "k", "q", "X"]))
    _bad(lambda d: d["rights"].__setitem__("values", ["K", "K", "k", "q"]))  # duplicate
    _bad(lambda d: d["rights"].__setitem__("values", "KQkq"))  # string not list


def test_irrevocable_exact_bool():
    _bad(lambda d: d["rights"].__setitem__("irrevocable", False))  # reversal
    _bad(lambda d: d["rights"].__setitem__("irrevocable", 1))  # bool/int conflation
    _bad(lambda d: d["rights"].__setitem__("irrevocable", "true"))


def test_home_squares_exact():
    def swap(d):
        d["rights"]["home_squares"]["K"]["rook"] = "a1"  # contradiction
    _bad(swap)

    def wrong_king(d):
        d["rights"]["home_squares"]["k"]["king"] = "e1"  # color swap
    _bad(wrong_king)
    _bad(lambda d: d["rights"]["home_squares"].__delitem__("q"))


def test_loss_mapping_exact():
    _bad(lambda d: d["loss"].__setitem__("on_king_move", ["K"]))  # shrink
    _bad(lambda d: d["loss"].__setitem__("on_king_move", ["k", "q"]))  # color reversal
    _bad(lambda d: d["loss"]["on_rook_move_from"].__setitem__("h1", "Q"))
    _bad(lambda d: d["loss"]["on_rook_capture_on"].__delitem__("a8"))
    _bad(lambda d: d["loss"].__delitem__("on_king_move_black"))


def test_move_paths_exact():
    def bad_king_to(d):
        d["move"]["per_side_paths"]["K"]["king_to"] = "f1"  # one square, not two
    _bad(bad_king_to)

    def missing_b1(d):
        d["move"]["per_side_paths"]["Q"]["empty_required"] = ["d1", "c1"]  # b1 dropped
    _bad(missing_b1)

    def transit_swap(d):
        d["move"]["per_side_paths"]["k"]["king_transit"] = ["g8"]
    _bad(transit_swap)
    _bad(lambda d: d["move"].__setitem__("king_deltas", "one-or-two-squares"))


def test_preconditions_exact_and_ordered():
    _bad(lambda d: d["move"]["preconditions"].pop())  # dropped through-check pin
    _bad(lambda d: d["move"]["preconditions"].__setitem__(
        3, "king in check on king_from"))  # contradiction
    _bad(lambda d: d["move"]["preconditions"].reverse())  # reorder
    _bad(lambda d: d["move"]["preconditions"].append("rook not attacked"))


def test_turn_linkage_exact():
    _bad(lambda d: d["turn_linkage"].__setitem__("halfmove_clock", "reset"))  # reversal
    _bad(lambda d: d["turn_linkage"].__setitem__("transitions", "at-most-one"))
    _bad(lambda d: d["turn_linkage"].__setitem__("fullmove_number", "always"))


def test_identity_participation_exact_bool():
    _bad(lambda d: d["identity"].__setitem__("rights_participate", False))  # reversal
    _bad(lambda d: d["identity"].__setitem__("rights_participate", 1))


def test_failure_classes_and_mapping_exact():
    _bad(lambda d: d["failure_classes"].reverse())
    _bad(lambda d: d["failure_classes"].remove("through_check"))
    _bad(lambda d: d["failure_classes"].append("orphan_class"))

    def undeclared_error(d):
        d["failure_mapping"]["path_blocked"]["error"] = "not_in_enum"
    _bad(undeclared_error)

    def wrong_error(d):
        d["failure_mapping"]["rights_inconsistent"]["error"] = "malformed_request"
    _bad(wrong_error)

    def trigger_changed(d):
        d["failure_mapping"]["through_check"]["trigger"] = "anything-attacked"
    _bad(trigger_changed)


def test_error_enum_and_shape_exact():
    _bad(lambda d: d["errors"]["closed_enum"].remove("illegal_move"))
    _bad(lambda d: d["errors"]["closed_enum"].append("unknown_variant"))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["retryable"]
         .__setitem__("type", "string"))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["code"]
         .__setitem__("required", False))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["code"]
         .__setitem__("required", 1))  # bool/int conflation


def test_versioning_exact():
    _bad(lambda d: d.__setitem__("versioning", {"base_path": "/castling/v2"}))


def test_documentation_fields_nonempty():
    _bad(lambda d: d["rights"].__setitem__("rule", ""))
    _bad(lambda d: d["move"].__setitem__("rule", "   "))
    _bad(lambda d: d["loss"].__setitem__("rule", 7))


def _write_linked_root(tmp_path: Path, variant_doc: dict, turn_doc: dict) -> Path:
    root = tmp_path / "repo"
    (root / "data" / "contracts").mkdir(parents=True)
    (root / "data" / "contracts" / "variant.yaml").write_text(yaml.safe_dump(variant_doc))
    (root / "data" / "contracts" / "turn.yaml").write_text(yaml.safe_dump(turn_doc))
    return root


def test_linkage_mutations_rejected(tmp_path):
    variant = yaml.safe_load((ROOT / "data" / "contracts" / "variant.yaml").read_text())
    turn = yaml.safe_load((ROOT / "data" / "contracts" / "turn.yaml").read_text())

    bad_variant = copy.deepcopy(variant)
    bad_variant["contract"]["variants"]["entries"][0]["castling"] = "chess960"
    root = _write_linked_root(tmp_path / "a", bad_variant, turn)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)

    bad_variant2 = copy.deepcopy(variant)
    bad_variant2["contract"]["identity"]["canonical_fields"] = [
        f for f in bad_variant2["contract"]["identity"]["canonical_fields"]
        if f != "castling_rights"
    ]
    root = _write_linked_root(tmp_path / "b", bad_variant2, turn)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)

    bad_turn = copy.deepcopy(turn)
    bad_turn["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_when"] = [
        "pawn_move", "capture", "castling"
    ]
    root = _write_linked_root(tmp_path / "c", variant, bad_turn)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)

    malformed_variant = {"schema_version": 1, "contract": {"id": "chess-variant"}}
    root = _write_linked_root(tmp_path / "d", malformed_variant, turn)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)


def test_links_exact_paths():
    _bad(lambda d: d["links"].__setitem__("variant_contract", "data/contracts/other.yaml"))
    _bad(lambda d: d["links"].__delitem__("turn_contract"))


def test_rights_grammar_structured_exact():
    """The grammar is normative STRUCTURE, not prose: every field gets
    contradiction mutations, and a prose-only canonical_form edit that
    contradicts the grammar is caught by the grammar pins staying put."""
    _bad(lambda d: d["rights"]["grammar"].__setitem__("none_sentinel", ""))
    _bad(lambda d: d["rights"]["grammar"].__setitem__("none_sentinel", "none"))
    _bad(lambda d: d["rights"]["grammar"].__setitem__("ordering", "kqKQ"))  # reversal
    _bad(lambda d: d["rights"]["grammar"].__setitem__("duplicates", "allowed"))
    _bad(lambda d: d["rights"]["grammar"].__setitem__("empty", "allowed"))
    _bad(lambda d: d["rights"]["grammar"].__setitem__("membership", "any-string"))
    _bad(lambda d: d["rights"]["grammar"].__delitem__("empty"))
    _bad(lambda d: d["rights"]["grammar"].__setitem__("bogus", 1))
    # contradictory prose alone must not matter - but grammar must exist;
    # delete the prose and the lint must FAIL (documentation required)
    _bad(lambda d: d["rights"].__delitem__("canonical_form"))


def test_missing_and_wrong_container_family():
    """The whole malformed-SHAPE family converts to ContractError with
    the advertised prefix - no raw KeyError/AttributeError escapes."""
    cases = [
        lambda d: d["contract"].__delitem__("rights"),
        lambda d: d["contract"]["rights"].__delitem__("values"),
        lambda d: d["contract"]["rights"].__delitem__("grammar"),
        lambda d: d["contract"].__delitem__("loss"),
        lambda d: d["contract"].__delitem__("move"),
        lambda d: d["contract"]["move"].__setitem__("per_side_paths", []),
        lambda d: d["contract"]["move"].__setitem__("per_side_paths", None),
        lambda d: d["contract"]["move"]["per_side_paths"].__setitem__("K", []),
        lambda d: d["contract"].__delitem__("turn_linkage"),
        lambda d: d["contract"].__delitem__("identity"),
        lambda d: d["contract"].__delitem__("errors"),
        lambda d: d["contract"].__delitem__("links"),
        lambda d: d["contract"].__delitem__("failure_mapping"),
        lambda d: d["contract"].__delitem__("versioning"),
        lambda d: d.__setitem__("contract", []),
        lambda d: d["contract"].__setitem__("rights", "KQkq"),
    ]
    for fn in cases:
        doc = copy.deepcopy(DOC)
        fn(doc)
        with pytest.raises(ContractError) as ei:
            lint(doc)
        assert str(ei.value).startswith("castling contract:"), str(ei.value)


def test_cli_boundary_clean_and_failing():
    ok = subprocess.run([sys.executable, "tools/castling_contract_lint.py"],
                        cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0 and "OK castling contract lint" in ok.stdout
    bad_doc = copy.deepcopy(DOC)
    bad_doc["contract"]["rights"]["irrevocable"] = False
    bad_path = ROOT / "data" / "contracts" / ".tmp_bad_castling.yaml"
    bad_path.write_text(yaml.safe_dump(bad_doc))
    try:
        fail = subprocess.run(
            [sys.executable, "tools/castling_contract_lint.py", str(bad_path)],
            cwd=ROOT, capture_output=True, text=True)
        assert fail.returncode == 1
        assert "castling contract:" in fail.stderr
        assert "Traceback" not in fail.stderr
    finally:
        bad_path.unlink()
