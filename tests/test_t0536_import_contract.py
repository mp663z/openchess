"""T0536: import capability contract battery - the lint must PROVE the
normative content of data/contracts/import.yaml by exact structured
comparison. Every rule family gets contradiction AND reversal
mutations; the scenario registry gets per-entry field mutations (the
capability acceptance: every scenario maps to visible output, state,
telemetry and errors); linkage mutations run against mutated COPIES of
the linked artifacts in a temp root, never against the real ones.
A mutation that passes is a hole."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.import_contract_lint import CONTRACT, lint
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
    assert str(ei.value).startswith("import contract:"), str(ei.value)
    return str(ei.value)


def test_real_contract_lints_clean():
    lint(copy.deepcopy(DOC))


def test_unknown_keys_rejected_at_every_level():
    _bad(lambda d: d.__setitem__("bogus", 1), "top")
    _bad(lambda d: d["sources"].__setitem__("bogus", 1))
    _bad(lambda d: d["sources"]["entries"][0].__setitem__("bogus", 1))
    _bad(lambda d: d["record"].__setitem__("bogus", 1))
    _bad(lambda d: d["idempotency"].__setitem__("bogus", 1))
    _bad(lambda d: d["atomicity"].__setitem__("bogus", 1))
    _bad(lambda d: d["scenarios"].__setitem__("bogus", 1))
    _bad(lambda d: d["scenarios"]["entries"][0].__setitem__("bogus", 1))


def test_schema_version_exact_int():
    for bad in (1.0, "1", True, 2, None):
        _bad(lambda d, b=bad: d.__setitem__("schema_version", b), "top")


def test_contract_id_exact():
    _bad(lambda d: d.__setitem__("id", "import"))
    _bad(lambda d: d.__setitem__("id", "import-capability-v2"))


def test_sources_registry_exact():
    _bad(lambda d: d["sources"]["entries"].pop())  # dropped source
    _bad(lambda d: d["sources"]["entries"][0].__setitem__("rights_class", "cc0"))  # escalation
    # lichess downgraded
    _bad(lambda d: d["sources"]["entries"][4].__setitem__("rights_class", "user-own"))
    # chesscom upgraded: rights laundering
    _bad(lambda d: d["sources"]["entries"][5].__setitem__("rights_class", "cc0"))
    _bad(lambda d: d["sources"]["entries"][4].__setitem__("kind", "user-file"))
    _bad(lambda d: d["sources"]["entries"][0].__setitem__("kind", "scrape"))  # undeclared kind
    _bad(lambda d: d["sources"]["entries"].append(
        {"id": "random-website", "kind": "public-api", "rights_class": "cc0"}))  # new source
    _bad(lambda d: d["sources"]["entries"][0].__setitem__("id", "pgn"))
    _bad(lambda d: d["sources"]["rights_classes"].remove("cc0"))
    _bad(lambda d: d["sources"]["rights_classes"].append("public-domain"))


def test_record_shape_exact():
    # hash dropped: dedup goes heuristic
    _bad(lambda d: d["record"]["fields"].remove("content_sha256"))
    _bad(lambda d: d["record"]["fields"].remove("source_id"))
    _bad(lambda d: d["record"]["fields"].reverse())
    _bad(lambda d: d["record"]["fields"].append("engine_score"))
    # provenance without rights
    _bad(lambda d: d["record"]["provenance_fields"].remove("rights_class"))
    _bad(lambda d: d["record"]["provenance_fields"].remove("retrieved_at"))
    _bad(lambda d: d["record"]["fields"].__setitem__(0, "id"))


def test_idempotency_exact():
    _bad(lambda d: d["idempotency"].__setitem__("dedup_key", "content_sha256"))
    # contradiction
    _bad(lambda d: d["idempotency"]["duplicate"].__setitem__("policy", "replace-silently"))
    _bad(lambda d: d["idempotency"]["duplicate"].__setitem__("outcome", "reimported"))
    _bad(lambda d: d["idempotency"]["updated"].__setitem__("policy", "reject"))
    _bad(lambda d: d["idempotency"]["updated"].__delitem__("precondition"))  # unguarded replace
    _bad(lambda d: d["idempotency"].__delitem__("duplicate"))


def test_atomicity_exact():
    _bad(lambda d: d["atomicity"].__setitem__("commit_boundary", "per-file"))  # coarsening
    _bad(lambda d: d["atomicity"].__setitem__("partial_game", "visible-with-flag"))  # contradiction
    _bad(lambda d: d["atomicity"].__setitem__("cancel", "rollback-everything"))
    _bad(lambda d: d["atomicity"].__setitem__("crash", "manual-cleanup-required"))
    _bad(lambda d: d["atomicity"].__delitem__("crash"))


def test_scenarios_registry_exact():
    _bad(lambda d: d["scenarios"]["entries"].pop())  # scenario unmapped
    _bad(lambda d: d["scenarios"]["entries"][0].__setitem__("chain_task", "T9999"))
    # illegal import must refuse
    _bad(lambda d: d["scenarios"]["entries"][5].__setitem__("visible_output", "import-summary"))
    # bad tags stored as games
    _bad(lambda d: d["scenarios"]["entries"][3].__setitem__("persisted_state", ["game-records"]))
    # duplicate must say already-imported
    _bad(lambda d: d["scenarios"]["entries"][6].__setitem__("visible_output", "import-summary"))
    # unknown-rights must refuse loudly
    _bad(lambda d: d["scenarios"]["entries"][12].__setitem__("error_codes", []))
    # wrong code
    _bad(lambda d: d["scenarios"]["entries"][12].__setitem__("error_codes", ["malformed_request"]))
    # unknown-rights names NO source
    _bad(lambda d: d["scenarios"]["entries"][12].__setitem__("sources", ["pgn-file"]))
    # undeclared source
    _bad(lambda d: d["scenarios"]["entries"][0].__setitem__("sources", ["unknown-source"]))
    _bad(lambda d: d["scenarios"]["entries"][0]["telemetry"].clear())  # empty telemetry
    _bad(lambda d: d["scenarios"]["entries"][0]["persisted_state"].clear())  # empty state
    _bad(lambda d: d["scenarios"]["entries"][0].__setitem__("visible_output", ""))  # empty output
    # illegal must be illegal_move
    _bad(lambda d: d["scenarios"]["entries"][5].__setitem__("error_codes", ["malformed_request"]))
    _bad(lambda d: d["scenarios"]["entries"][0]["error_codes"].append("not_in_enum"))


def test_failure_classes_and_mapping_exact():
    _bad(lambda d: d["failure_classes"].reverse())
    _bad(lambda d: d["failure_classes"].remove("partial_visibility"))
    _bad(lambda d: d["failure_classes"].append("orphan_class"))

    def undeclared_error(d):
        d["failure_mapping"]["malformed_input"]["error"] = "not_in_enum"
    _bad(undeclared_error)

    def wrong_error(d):
        # rights refusal laundered
        d["failure_mapping"]["unknown_rights"]["error"] = "malformed_request"
    _bad(wrong_error)

    def partial_not_internal(d):
        # visibility bug is internal
        d["failure_mapping"]["partial_visibility"]["error"] = "malformed_request"
    _bad(partial_not_internal)

    def trigger_changed(d):
        d["failure_mapping"]["illegal_movetext"]["trigger"] = "anything"
    _bad(trigger_changed)


def test_error_enum_and_shape_exact():
    _bad(lambda d: d["errors"]["closed_enum"].remove("unknown_rights"))
    _bad(lambda d: d["errors"]["closed_enum"].append("unknown_variant"))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["retryable"]
         .__setitem__("type", "string"))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["code"]
         .__setitem__("required", False))
    _bad(lambda d: d["errors"]["shape"]["error"]["fields"]["code"]
         .__setitem__("required", 1))  # bool/int conflation


def test_versioning_exact():
    # every compatibility semantic is STRUCTURED and exactly pinned
    _bad(lambda d: d.__setitem__("versioning", {"base_path": "/import/v2"}))
    _bad(lambda d: d["versioning"].__setitem__("base_path", "/import/v2"))
    _bad(lambda d: d["versioning"].__setitem__("client_pin", "MINOR"))  # contradiction
    _bad(lambda d: d["versioning"].__setitem__("minor_policy", "subtractive-allowed"))
    _bad(lambda d: d["versioning"]["minor_additions"].append("breaking-changes"))
    _bad(lambda d: d["versioning"]["minor_additions"].remove("new-sources"))
    _bad(lambda d: d["versioning"].__setitem__("minor_additions", "new-sources"))
    _bad(lambda d: d["versioning"].__setitem__("downgrade_policy", "no-downgrade"))  # reversal
    _bad(lambda d: d["versioning"].__setitem__("major_bump", "same-base-path"))  # contradiction
    _bad(lambda d: d["versioning"].__delitem__("client_pin"))
    _bad(lambda d: d["versioning"].__delitem__("minor_additions"))
    _bad(lambda d: d["versioning"].__delitem__("downgrade_policy"))
    _bad(lambda d: d["versioning"].__delitem__("major_bump"))
    _bad(lambda d: d["versioning"].__setitem__("bogus", 1))
    _bad(lambda d: d["versioning"].__delitem__("rule"))
    _bad(lambda d: d["versioning"].__setitem__("rule", ""))


def test_documentation_fields_nonempty():
    _bad(lambda d: d["sources"].__setitem__("registry_rule", ""))
    _bad(lambda d: d["record"].__setitem__("identity_rule", 7))
    _bad(lambda d: d["idempotency"].__setitem__("rule", ""))
    _bad(lambda d: d["atomicity"].__setitem__("rule", ""))
    _bad(lambda d: d["scenarios"].__setitem__("registry_rule", ""))


def _write_linked_root(tmp_path: Path, variant_doc: dict,
                       policy_doc: object = None) -> Path:
    root = tmp_path / "repo"
    (root / "data" / "contracts").mkdir(parents=True)
    (root / "data" / "contracts" / "variant.yaml").write_text(
        yaml.safe_dump(variant_doc, sort_keys=False))
    if policy_doc is None:
        policy_doc = yaml.safe_load(
            (ROOT / "data" / "contracts" / "rights_policy.yaml").read_text())
    (root / "data" / "contracts" / "rights_policy.yaml").write_text(
        yaml.safe_dump(policy_doc))
    return root


def test_linkage_mutations_rejected(tmp_path):
    variant = yaml.safe_load((ROOT / "data" / "contracts" / "variant.yaml").read_text())

    bad_variant = copy.deepcopy(variant)
    bad_variant["contract"]["identity"]["canonical_fields"] = ["variant"]
    root = _write_linked_root(tmp_path / "a", bad_variant)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)

    malformed_variant = {"schema_version": 1, "contract": {"id": "chess-variant"}}
    root = _write_linked_root(tmp_path / "b", malformed_variant)
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)

    root = _write_linked_root(tmp_path / "c", variant,
                              {"rights_policy": "no structure here"})
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), root)


def test_links_exact_paths():
    _bad(lambda d: d["links"].__setitem__("rights_policy", "docs/other.md"))
    _bad(lambda d: d["links"].__delitem__("variant_contract"))


def test_missing_and_wrong_container_family():
    """The whole malformed-SHAPE family converts to ContractError with
    the advertised prefix - no raw KeyError/AttributeError escapes."""
    cases = [
        lambda d: d["contract"].__delitem__("sources"),
        lambda d: d["contract"]["sources"].__delitem__("entries"),
        lambda d: d["contract"]["sources"].__delitem__("rights_classes"),
        lambda d: d["contract"]["sources"].__setitem__("entries", {}),
        lambda d: d["contract"].__delitem__("record"),
        lambda d: d["contract"]["record"].__delitem__("fields"),
        lambda d: d["contract"].__delitem__("idempotency"),
        lambda d: d["contract"].__delitem__("atomicity"),
        lambda d: d["contract"].__delitem__("scenarios"),
        lambda d: d["contract"]["scenarios"].__setitem__("entries", {}),
        lambda d: d["contract"]["scenarios"].__delitem__("entries"),
        lambda d: d["contract"].__delitem__("errors"),
        lambda d: d["contract"].__delitem__("links"),
        lambda d: d["contract"].__delitem__("failure_mapping"),
        lambda d: d["contract"].__delitem__("versioning"),
        lambda d: d.__setitem__("contract", []),
        lambda d: d["contract"].__setitem__("sources", "pgn"),
    ]
    for fn in cases:
        doc = copy.deepcopy(DOC)
        fn(doc)
        with pytest.raises(ContractError) as ei:
            lint(doc)
        assert str(ei.value).startswith("import contract:"), str(ei.value)


def test_cli_boundary_clean_and_failing():
    ok = subprocess.run([sys.executable, "tools/import_contract_lint.py"],
                        cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0 and "OK import contract lint" in ok.stdout
    bad_doc = copy.deepcopy(DOC)
    bad_doc["contract"]["sources"]["entries"][5]["rights_class"] = "cc0"
    bad_path = ROOT / "data" / "contracts" / ".tmp_bad_import.yaml"
    try:
        bad_path.write_text(yaml.safe_dump(bad_doc))
        fail = subprocess.run(
            [sys.executable, "tools/import_contract_lint.py", str(bad_path)],
            cwd=ROOT, capture_output=True, text=True)
        assert fail.returncode == 1
        assert "import contract:" in fail.stderr
        assert "Traceback" not in fail.stderr
    finally:
        bad_path.unlink(missing_ok=True)


def test_rights_semantics_structured_in_linked_policy(tmp_path):
    """The operative rights meaning lives in the linked structured
    policy and is exactly compared (v2): value changes, missing keys,
    fail-closed reversal, and phrase-mimicking fakes all fail."""
    variant = yaml.safe_load((ROOT / "data" / "contracts" / "variant.yaml").read_text())
    real_policy = yaml.safe_load(
        (ROOT / "data" / "contracts" / "rights_policy.yaml").read_text())

    def lint_with(policy_obj, label):
        root = _write_linked_root(tmp_path / label, variant, policy_obj)
        return root

    lint(copy.deepcopy(DOC), lint_with(copy.deepcopy(real_policy), "ok"))

    def policy_bad(fn, label):
        pol = copy.deepcopy(real_policy)
        fn(pol["rights_policy"])
        with pytest.raises(ContractError):
            lint(copy.deepcopy(DOC), lint_with(pol, label))

    # laundering reversal: third-party storage permitted
    policy_bad(lambda p: p["classes"]["user-own-only"]
               .__setitem__("third_party_storage", "permitted"), "a")
    # redistribution opened on a user-own class
    policy_bad(lambda p: p["classes"]["user-own"]
               .__setitem__("redistribution", "permitted"), "b")
    # provenance dropped
    policy_bad(lambda p: p["classes"]["cc0"]
               .__setitem__("provenance_required", False), "c")
    # fail-closed reversed
    policy_bad(lambda p: p.__setitem__("fail_closed", False), "d")
    # unknown-class effect rerouted
    policy_bad(lambda p: p["unknown_class"].__setitem__("error", "internal"), "e")
    # a whole class removed
    policy_bad(lambda p: p["classes"].__delitem__("user-own-only"), "f")
    # unknown key smuggled into a class
    policy_bad(lambda p: p["classes"]["cc0"].__setitem__("exceptions", "any"), "g")
    # prose mimic: a fake doc carrying the old phrase but no structure
    with pytest.raises(ContractError):
        lint(copy.deepcopy(DOC), lint_with(
            {"rights_policy": "Fail closed. Chess.com public data is CC0."}, "h"))


def test_rights_policy_closed_document(tmp_path):
    """Override-laundering family (v3): the linked policy is a closed
    contract at document, policy, class, and unknown_class levels."""
    variant = yaml.safe_load((ROOT / "data" / "contracts" / "variant.yaml").read_text())
    real_policy = yaml.safe_load(
        (ROOT / "data" / "contracts" / "rights_policy.yaml").read_text())

    def doc_bad(fn, label):
        pol = copy.deepcopy(real_policy)
        fn(pol)
        root = _write_linked_root(tmp_path / label, variant, pol)
        with pytest.raises(ContractError):
            lint(copy.deepcopy(DOC), root)

    # unknown key at document level
    doc_bad(lambda p: p.__setitem__("override", {"fail_closed": False}), "a")
    # unknown key at policy level (verifier probe)
    doc_bad(lambda p: p["rights_policy"].__setitem__(
        "exceptions", {"chesscom-public": "redistribute-all"}), "b")
    doc_bad(lambda p: p["rights_policy"].__setitem__("fallback", "allow"), "c")
    # schema_version type: boolean true is not integer 1
    doc_bad(lambda p: p.__setitem__("schema_version", True), "d")
    doc_bad(lambda p: p.__setitem__("schema_version", "1"), "e")
    doc_bad(lambda p: p.__delitem__("schema_version"), "f")
    # rule required, nonempty
    doc_bad(lambda p: p["rights_policy"].__delitem__("rule"), "g")
    doc_bad(lambda p: p["rights_policy"].__setitem__("rule", ""), "h")
    # unknown key inside unknown_class
    doc_bad(lambda p: p["rights_policy"]["unknown_class"]
            .__setitem__("except", "verified-sources"), "i")
    # unknown key inside a class
    doc_bad(lambda p: p["rights_policy"]["classes"]["cc0"]
            .__setitem__("exceptions", "any"), "j")
    # missing fail_closed
    doc_bad(lambda p: p["rights_policy"].__delitem__("fail_closed"), "k")
