"""T0068: en-passant contract battery - the lint must PROVE the normative
content of data/contracts/en_passant.yaml by exact structured comparison.
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

from tools.en_passant_contract_lint import CONTRACT, lint
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
    assert str(ei.value).startswith("en-passant contract:"), str(ei.value)
    return str(ei.value)


def test_real_contract_lints_clean():
    lint(copy.deepcopy(DOC))


def test_unknown_keys_rejected_at_every_level():
    _bad(lambda d: d.__setitem__("bogus", 1), "top")
    _bad(lambda d: d["target"].__setitem__("bogus", 1))
    _bad(lambda d: d["target"]["grammar"].__setitem__("bogus", 1))
    _bad(lambda d: d["target"]["set_on"]["white"].__setitem__("bogus", 1))
    _bad(lambda d: d["target"]["lifetime"].__setitem__("bogus", 1))
    _bad(lambda d: d["target"]["storage_vs_identity"].__setitem__("bogus", 1))
    _bad(lambda d: d["capture"].__setitem__("bogus", 1))
    _bad(lambda d: d["capture"]["mover"]["white"].__setitem__("bogus", 1))
    _bad(lambda d: d["capture"]["mechanics"].__setitem__("bogus", 1))
    _bad(lambda d: d["identity"].__setitem__("bogus", 1))


def test_schema_version_exact_int():
    for bad in (1.0, "1", True, 2, None):
        _bad(lambda d, b=bad: d.__setitem__("schema_version", b), "top")


def test_contract_id_exact():
    _bad(lambda d: d.__setitem__("id", "chess-enpassant"))
    _bad(lambda d: d.__setitem__("id", "chess-en-passant-v2"))


def test_grammar_exact_and_ordered():
    _bad(lambda d: d["target"]["grammar"].__setitem__("files", list("hgfedcba")))  # reversal
    _bad(lambda d: d["target"]["grammar"].__setitem__("files", list("abcdefg")))  # shrink
    _bad(lambda d: d["target"]["grammar"].__setitem__("files", "abcdefgh"))  # string not list
    _bad(lambda d: d["target"]["grammar"].__setitem__("ranks", ["3", "4"]))  # contradiction
    _bad(lambda d: d["target"]["grammar"].__setitem__("ranks", ["6", "3"]))  # reversal
    _bad(lambda d: d["target"]["grammar"].__setitem__("ranks", [3, 6]))  # int not string
    _bad(lambda d: d["target"]["grammar"].__setitem__("none_sentinel", ""))
    _bad(lambda d: d["target"]["grammar"].__setitem__("none_sentinel", "none"))
    _bad(lambda d: d["target"]["grammar"].__setitem__("empty", "allowed"))
    _bad(lambda d: d["target"]["grammar"].__delitem__("empty"))


def test_set_on_exact():
    _bad(lambda d: d["target"]["set_on"].__setitem__("event", "any-pawn-advance"))
    _bad(lambda d: d["target"]["set_on"]["white"].__setitem__("from_rank", "3"))
    _bad(lambda d: d["target"]["lifetime"].__setitem__("duration", "until-used"))
    _bad(lambda d: d["target"]["set_on"]["black"].__setitem__("target_rank", "3"))  # color swap
    _bad(lambda d: d["target"]["set_on"].__delitem__("black"))
    _bad(lambda d: d["target"]["set_on"]["black"].__delitem__("to_rank"))


def test_lifetime_exact():
    _bad(lambda d: d["target"]["lifetime"].__setitem__("duration", "until-used"))  # contradiction
    _bad(lambda d: d["target"]["lifetime"].__setitem__("available_to", "either-side"))
    _bad(lambda d: d["target"]["storage_vs_identity"].__setitem__(
        "storage", "recorded-when-a-capture-exists"))
    _bad(lambda d: d["target"]["lifetime"].__delitem__("cleared_by"))


def test_storage_vs_identity_exact():
    _bad(lambda d: d["target"]["storage_vs_identity"].__setitem__(
        "storage", "recorded-when-a-capture-exists"))  # contradiction
    _bad(lambda d: d["target"]["storage_vs_identity"].__setitem__(
        "identity_value", "always-the-target"))  # reversal
    _bad(lambda d: d["target"]["storage_vs_identity"].__delitem__("identity_value"))


def test_mover_exact():
    _bad(lambda d: d["capture"]["mover"].__setitem__("piece", "any-piece"))
    _bad(lambda d: d["capture"]["mover"]["white"].__setitem__("mover_rank", "4"))
    _bad(lambda d: d["capture"]["mover"]["white"].__setitem__("target_rank", "3"))  # color swap
    _bad(lambda d: d["capture"]["mover"]["black"].__setitem__("captured_rank", "5"))
    _bad(lambda d: d["capture"]["mover"].__setitem__("file_relation", "same-file"))
    _bad(lambda d: d["capture"]["mover"]["black"].__delitem__("captured_rank"))


def test_mechanics_exact():
    _bad(lambda d: d["capture"]["mechanics"].__setitem__("destination",
        "the-captured-square"))  # contradiction
    _bad(lambda d: d["capture"]["mechanics"]["captured_square"].__setitem__("file", "mover-file"))
    _bad(lambda d: d["capture"]["mechanics"]["captured_square"].__setitem__(
        "rank", "target-rank"))  # captured on destination
    _bad(lambda d: d["capture"]["mechanics"].__delitem__("captured_square"))


def test_preconditions_exact_and_ordered():
    _bad(lambda d: d["capture"]["preconditions"].pop())  # dropped the double-removal pin
    _bad(lambda d: d["capture"]["preconditions"].__setitem__(
        2, "no enemy pawn on the captured square"))  # contradiction
    _bad(lambda d: d["capture"]["preconditions"].reverse())  # reorder
    _bad(lambda d: d["capture"]["preconditions"].append("target square empty"))  # nonsense addition


def test_turn_linkage_exact():
    _bad(lambda d: d["turn_linkage"].__setitem__("halfmove_clock",
        "increment"))  # reversal: ep ALWAYS resets
    _bad(lambda d: d["turn_linkage"].__setitem__("transitions", "at-most-one"))
    _bad(lambda d: d["turn_linkage"].__setitem__("fullmove_number", "always"))


def test_identity_exact():
    _bad(lambda d: d["identity"].__setitem__("participates",
        "conditionally"))  # contradiction: field always participates
    _bad(lambda d: d["identity"].__setitem__("canonical_field", "ep_target"))
    _bad(lambda d: d["identity"].__setitem__("value_rule", "always-the-stored-target"))  # reversal
    _bad(lambda d: d["identity"].__delitem__("value_rule"))


def test_failure_classes_and_mapping_exact():
    _bad(lambda d: d["failure_classes"].reverse())
    _bad(lambda d: d["failure_classes"].remove("pinned_capture"))
    _bad(lambda d: d["failure_classes"].append("orphan_class"))

    def undeclared_error(d):
        d["failure_mapping"]["pinned_capture"]["error"] = "not_in_enum"
    _bad(undeclared_error)

    def wrong_error(d):
        d["failure_mapping"]["target_inconsistent"]["error"] = "malformed_request"
    _bad(wrong_error)

    def trigger_changed(d):
        d["failure_mapping"]["pinned_capture"]["trigger"] = "king-in-check-before"
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
    # every compatibility semantic is STRUCTURED and exactly pinned
    _bad(lambda d: d.__setitem__("versioning", {"base_path": "/en-passant/v2"}))
    _bad(lambda d: d["versioning"].__setitem__("base_path", "/en-passant/v2"))
    _bad(lambda d: d["versioning"].__setitem__("client_pin", "MINOR"))  # contradiction
    _bad(lambda d: d["versioning"].__setitem__("client_pin", "major"))  # case
    _bad(lambda d: d["versioning"].__setitem__("minor_policy", "subtractive-allowed"))
    # contradiction
    _bad(lambda d: d["versioning"]["minor_additions"].append("new-required-fields"))
    _bad(lambda d: d["versioning"]["minor_additions"].pop())
    # string not list
    _bad(lambda d: d["versioning"].__setitem__("minor_additions", "new-optional-fields"))
    _bad(lambda d: d["versioning"].__setitem__("downgrade_policy", "any-version-anywhere"))
    _bad(lambda d: d["versioning"].__setitem__("downgrade_policy", "no-downgrade"))  # reversal
    _bad(lambda d: d["versioning"].__setitem__("major_bump", "same-base-path"))  # contradiction
    _bad(lambda d: d["versioning"].__delitem__("client_pin"))
    _bad(lambda d: d["versioning"].__delitem__("minor_policy"))
    _bad(lambda d: d["versioning"].__delitem__("downgrade_policy"))
    _bad(lambda d: d["versioning"].__delitem__("major_bump"))
    _bad(lambda d: d["versioning"].__setitem__("bogus", 1))
    _bad(lambda d: d["versioning"].__delitem__("rule"))  # documentation required
    _bad(lambda d: d["versioning"].__setitem__("rule", ""))


def test_documentation_fields_nonempty():
    _bad(lambda d: d["target"].__setitem__("rule", ""))
    _bad(lambda d: d["target"]["grammar"].__setitem__("rule", "   "))
    _bad(lambda d: d["capture"].__setitem__("rule", 7))
    _bad(lambda d: d["turn_linkage"].__setitem__("rule", ""))


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
    bad_variant["contract"]["identity"]["canonical_fields"] = [
        f for f in bad_variant["contract"]["identity"]["canonical_fields"]
        if f != "en_passant"
    ]
    root = _write_linked_root(tmp_path / "a", bad_variant, turn)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)

    bad_variant2 = copy.deepcopy(variant)
    bad_variant2["contract"]["fen"]["failure_classes"] = [
        f for f in bad_variant2["contract"]["fen"]["failure_classes"]
        if f != "bad_en_passant"
    ]
    root = _write_linked_root(tmp_path / "b", bad_variant2, turn)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)

    bad_turn = copy.deepcopy(turn)
    bad_turn["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_when"] = [
        "pawn_move"  # capture dropped: ep is both, the reset must be total
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


def test_missing_and_wrong_container_family():
    """The whole malformed-SHAPE family converts to ContractError with
    the advertised prefix - no raw KeyError/AttributeError escapes."""
    cases = [
        lambda d: d["contract"].__delitem__("target"),
        lambda d: d["contract"]["target"].__delitem__("grammar"),
        lambda d: d["contract"]["target"].__delitem__("set_on"),
        lambda d: d["contract"]["target"].__delitem__("lifetime"),
        lambda d: d["contract"]["target"].__delitem__("storage_vs_identity"),
        lambda d: d["contract"].__delitem__("capture"),
        lambda d: d["contract"]["capture"].__delitem__("mover"),
        lambda d: d["contract"]["capture"].__delitem__("preconditions"),
        lambda d: d["contract"]["capture"].__setitem__("mover", []),
        lambda d: d["contract"]["capture"]["mechanics"].__setitem__("captured_square", None),
        lambda d: d["contract"].__delitem__("turn_linkage"),
        lambda d: d["contract"].__delitem__("identity"),
        lambda d: d["contract"].__delitem__("errors"),
        lambda d: d["contract"].__delitem__("links"),
        lambda d: d["contract"].__delitem__("failure_mapping"),
        lambda d: d["contract"].__delitem__("versioning"),
        lambda d: d.__setitem__("contract", []),
        lambda d: d["contract"].__setitem__("target", "a3"),
    ]
    for fn in cases:
        doc = copy.deepcopy(DOC)
        fn(doc)
        with pytest.raises(ContractError) as ei:
            lint(doc)
        assert str(ei.value).startswith("en-passant contract:"), str(ei.value)


def test_cli_boundary_clean_and_failing():
    ok = subprocess.run([sys.executable, "tools/en_passant_contract_lint.py"],
                        cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0 and "OK en-passant contract lint" in ok.stdout
    bad_doc = copy.deepcopy(DOC)
    bad_doc["contract"]["turn_linkage"]["halfmove_clock"] = "increment"
    bad_path = ROOT / "data" / "contracts" ".tmp_bad_en_passant.yaml"
    try:
        bad_path.write_text(yaml.safe_dump(bad_doc))
        fail = subprocess.run(
            [sys.executable, "tools/en_passant_contract_lint.py", str(bad_path)],
            cwd=ROOT, capture_output=True, text=True)
        assert fail.returncode == 1
        assert "en-passant contract:" in fail.stderr
        assert "Traceback" not in fail.stderr
    finally:
        bad_path.unlink(missing_ok=True)


def test_set_on_target_file_structured():
    """The same-file relation is structured, not prose (v3): the
    target's file is the advancing pawn's file, exactly pinned."""
    _bad(lambda d: d["target"]["set_on"].__setitem__("target_file", "adjacent-file"))
    _bad(lambda d: d["target"]["set_on"].__setitem__("target_file", "any-file"))
    _bad(lambda d: d["target"]["set_on"].__delitem__("target_file"))
    _bad(lambda d: d["target"]["set_on"].__setitem__("target_file", 1))
