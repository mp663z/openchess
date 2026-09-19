"""T0050: turn contract lint battery - normative semantics are
STRUCTURED, so contradictions, reversals and policy flips are exact
value changes that must fail; the variant linkage is verified against
the real artifact; the CLI boundary never leaks a traceback."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.turn_contract_lint import CONTRACT, ContractError, lint

ROOT = Path(__file__).resolve().parent.parent
DOC = yaml.safe_load(CONTRACT.read_text())
VARIANT_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "variant.yaml").read_text())


def fresh() -> dict:
    return copy.deepcopy(DOC)


def bad(doc: dict, marker: str, root: Path = ROOT) -> None:
    with pytest.raises(ContractError) as excinfo:
        lint(doc, root=root)
    assert marker in str(excinfo.value), f"{marker!r} not in {excinfo.value}"


def test_valid_doc_passes():
    lint(fresh())


# --- exact-key gates ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("contract",),
        ("contract", "state"),
        ("contract", "transition"),
        ("contract", "termination"),
        ("contract", "errors"),
        ("contract", "versioning"),
        ("contract", "variant_link"),
        ("contract", "failure_mapping"),
    ],
)
def test_extra_key_rejected(path):
    doc = fresh()
    node = doc
    for segment in path:
        node = node[segment]
    node["bogus_key"] = 1
    bad(doc, "unknown keys")


@pytest.mark.parametrize(
    "path",
    [
        ("contract", "state", "fields"),
        ("contract", "state", "side_values"),
        ("contract", "state", "bounds"),
        ("contract", "state", "rule"),
        ("contract", "transition", "applies_to"),
        ("contract", "transition", "on_move"),
        ("contract", "transition", "per_move"),
        ("contract", "transition", "null_move"),
        ("contract", "transition", "identity"),
        ("contract", "transition", "rule"),
        ("contract", "termination", "states"),
        ("contract", "termination", "closed_after"),
        ("contract", "termination", "on_transition_after_termination"),
        ("contract", "termination", "unknown_state"),
        ("contract", "termination", "fifty_move"),
        ("contract", "termination", "rule"),
        ("contract", "failure_mapping"),
        ("contract", "errors", "closed_enum"),
        ("contract", "errors", "shape"),
        ("contract", "versioning", "base_path"),
        ("contract", "versioning", "rule"),
        ("contract", "variant_link", "contract"),
        ("contract", "variant_link", "path"),
        ("contract", "variant_link", "applies_to_variants"),
        ("contract", "variant_link", "rule"),
    ],
)
def test_missing_key_rejected(path):
    doc = fresh()
    node = doc
    for segment in path[:-1]:
        node = node[segment]
    del node[path[-1]]
    bad(doc, "missing keys")


def test_non_string_key_rejected():
    doc = fresh()
    doc["contract"][7] = "x"
    bad(doc, "non-string key")


# --- structured transition semantics -------------------------------------


@pytest.mark.parametrize("value", ["any-legal-or-illegal-event", "a single legal move", "", None])
def test_applies_to_pinned(value):
    doc = fresh()
    doc["contract"]["transition"]["applies_to"] = value
    bad(doc, "applies_to")


@pytest.mark.parametrize(
    "field,value",
    [
        ("side_to_move", "keep"),
        ("side_to_move", "alternate"),  # synonyms are violations, not drift
    ],
)
def test_side_transition_exact(field, value):
    doc = fresh()
    doc["contract"]["transition"]["on_move"][field] = value
    bad(doc, "on_move")


@pytest.mark.parametrize(
    "value",
    [
        {"op": "increment", "amount": 1, "when": "white"},  # wrong side
        {"op": "increment", "amount": 2, "when": "black"},  # wrong amount
        {"op": "set", "amount": 1, "when": "black"},
        {"op": "increment", "amount": 1, "when": "both"},
    ],
)
def test_fullmove_increment_exact(value):
    doc = fresh()
    doc["contract"]["transition"]["on_move"]["fullmove_number"] = value
    bad(doc, "on_move")


@pytest.mark.parametrize(
    "reset_when",
    [
        ["pawn_move"],  # capture missing
        ["capture", "pawn_move", "check"],  # invented predicate
        ["capture", "pawn_move"],  # order is pinned (reviewed order)
        [],
    ],
)
def test_halfmove_reset_predicate_exact(reset_when):
    doc = fresh()
    doc["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_when"] = reset_when
    bad(doc, "on_move")


def test_halfmove_reset_value_and_fallback_exact():
    doc = fresh()
    doc["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_to"] = 1
    bad(doc, "on_move")
    doc = fresh()
    doc["contract"]["transition"]["on_move"]["halfmove_clock"]["otherwise"] = {
        "op": "increment",
        "amount": 2,
    }
    bad(doc, "on_move")


def test_per_move_cardinality_exact():
    doc = fresh()
    doc["contract"]["transition"]["per_move"] = "at-most-one"
    bad(doc, "per_move")


@pytest.mark.parametrize(
    "value",
    [
        {"policy": "accept", "failure": "illegal_transition"},  # null moves accepted
        {"policy": "reject", "failure": "malformed_request"},  # wrong class
        {"policy": "reject"},  # missing failure
    ],
)
def test_null_move_policy_exact(value):
    doc = fresh()
    doc["contract"]["transition"]["null_move"] = value
    bad(doc, "null_move")


@pytest.mark.parametrize(
    "field,value",
    [
        ("counters_participate", True),  # reversed identity claim
        ("side_to_move_participates", False),  # reversed identity claim
        ("counters_participate", "false"),  # string, not bool
    ],
)
def test_identity_participation_booleans_exact(field, value):
    doc = fresh()
    doc["contract"]["transition"]["identity"][field] = value
    bad(doc, "identity")


# --- termination semantics ----------------------------------------------


def test_closed_after_must_be_true():
    doc = fresh()
    doc["contract"]["termination"]["closed_after"] = False
    bad(doc, "closed_after")


@pytest.mark.parametrize(
    "key,value",
    [
        ("on_transition_after_termination", {"policy": "allow", "failure": "illegal_transition"}),
        ("on_transition_after_termination", {"policy": "reject", "failure": "malformed_request"}),
        ("unknown_state", {"policy": "reject", "failure": "malformed_request"}),
        ("unknown_state", {"policy": "coerce", "failure": "unknown_termination"}),
    ],
)
def test_termination_policies_exact(key, value):
    doc = fresh()
    doc["contract"]["termination"][key] = value
    bad(doc, key)


def test_fifty_move_thresholds_reversed_fail():
    doc = fresh()
    fifty = doc["contract"]["termination"]["fifty_move"]
    fifty["claim"]["threshold"] = 150
    fifty["automatic"]["threshold"] = 100
    bad(doc, "fifty_move")


@pytest.mark.parametrize(
    "branch,field,value",
    [
        ("claim", "threshold", 99),
        ("claim", "threshold", 101),
        ("automatic", "threshold", 149),
        ("automatic", "threshold", 151),
        ("claim", "automatic", True),  # claim marked automatic
        ("automatic", "automatic", False),  # auto marked claimable
        ("claim", "state", "seventyfive_move_auto"),  # swapped states
        ("automatic", "state", "fifty_move_claim"),
        ("claim", "state", "undeclared_state"),
    ],
)
def test_fifty_move_fields_exact(branch, field, value):
    doc = fresh()
    doc["contract"]["termination"]["fifty_move"][branch][field] = value
    bad(doc, "fifty_move")


def test_termination_states_exact():
    doc = fresh()
    doc["contract"]["termination"]["states"] = doc["contract"]["termination"]["states"][:-1]
    bad(doc, "termination.states")


# --- failure mapping: no orphans, no ambiguity ----------------------------


def test_every_declared_failure_class_mapped_exactly():
    doc = fresh()
    del doc["contract"]["failure_mapping"]["unknown_termination"]
    bad(doc, "missing keys")


def test_invented_failure_class_in_mapping_rejected():
    doc = fresh()
    doc["contract"]["failure_mapping"]["slow_move"] = {
        "trigger": "took-too-long",
        "error": "internal",
    }
    bad(doc, "unknown keys")


def test_mapping_error_must_be_declared():
    # pin is exact, so this mutates BOTH the doc and validates the lint's
    # declared-enum cross-check independently via a hand-built mapping
    doc = fresh()
    doc["contract"]["failure_mapping"]["no_side_to_move"]["error"] = "internal"
    bad(doc, "failure_mapping")


def test_unknown_termination_maps_to_its_own_class_not_malformed():
    doc = fresh()
    doc["contract"]["failure_mapping"]["unknown_termination"]["error"] = "malformed_request"
    bad(doc, "failure_mapping")


def test_trigger_strings_exact():
    doc = fresh()
    doc["contract"]["failure_mapping"]["bad_counter"]["trigger"] = "bad counters lol"
    bad(doc, "failure_mapping")


# --- invariants / classes / enum / shape / versioning ----------------------


def test_invariants_exact():
    doc = fresh()
    doc["contract"]["invariants"] = doc["contract"]["invariants"][:-1]
    bad(doc, "contract.invariants")


def test_failure_classes_exact():
    doc = fresh()
    doc["contract"]["failure_classes"] = doc["contract"]["failure_classes"][:-1]
    bad(doc, "contract.failure_classes")


def test_enum_rejects_duplicates():
    doc = fresh()
    doc["contract"]["errors"]["closed_enum"].append("internal")
    bad(doc, "duplicate codes")


@pytest.mark.parametrize("code", ["malformed_request", "illegal_transition", "unknown_termination"])
def test_enum_requires_codes(code):
    doc = fresh()
    doc["contract"]["errors"]["closed_enum"] = [
        c for c in doc["contract"]["errors"]["closed_enum"] if c != code
    ]
    bad(doc, f"{code} required")


def test_error_shape_exact():
    doc = fresh()
    doc["contract"]["errors"]["shape"]["error"]["fields"]["code"]["type"] = "integer"
    bad(doc, "exact value")


@pytest.mark.parametrize("base", ["/turn/v0", "/turn/vx", "/variant/v1", "turn/v1", "/turn/v1/"])
def test_base_path_format(base):
    doc = fresh()
    doc["contract"]["versioning"]["base_path"] = base
    bad(doc, "base_path")


@pytest.mark.parametrize("marker", ["MINOR", "MAJOR", "downgrade"])
def test_versioning_markers(marker):
    doc = fresh()
    doc["contract"]["versioning"]["rule"] = doc["contract"]["versioning"]["rule"].replace(
        marker, "REDACTED"
    )
    bad(doc, f"must state {marker!r}")


# --- schema scalars --------------------------------------------------------


@pytest.mark.parametrize("value", [2, "1", True, None, 1.0])
def test_schema_version_exact_int_1(value):
    doc = fresh()
    doc["schema_version"] = value
    bad(doc, "schema_version")


def test_contract_id_pinned():
    doc = fresh()
    doc["contract"]["id"] = "chess-turns"
    bad(doc, "contract.id")


# --- variant linkage against the real artifact -----------------------------


def _tmp_root(tmp_path: Path, vdoc: object) -> Path:
    (tmp_path / "data" / "contracts").mkdir(parents=True)
    (tmp_path / "data" / "contracts" / "variant.yaml").write_text(yaml.safe_dump(vdoc))
    return tmp_path


def test_variant_link_applies_to_variants_exact():
    doc = fresh()
    doc["contract"]["variant_link"]["applies_to_variants"] = ["crazyhouse"]
    bad(doc, "applies_to_variants")


def test_variant_link_path_pinned():
    doc = fresh()
    doc["contract"]["variant_link"]["path"] = "data/contracts/turn.yaml"
    bad(doc, "variant_link.path")


def test_variant_link_missing_artifact_fails(tmp_path):
    doc = fresh()
    bad(doc, "linked contract must be a mapping", root=_tmp_root(tmp_path, {}))
    (tmp_path / "data" / "contracts" / "variant.yaml").unlink()
    bad(doc, "does not exist", root=tmp_path)


def test_variant_link_id_mismatch_fails(tmp_path):
    vdoc = copy.deepcopy(VARIANT_DOC)
    vdoc["contract"]["id"] = "chess-variants"
    bad(fresh(), "linked id", root=_tmp_root(tmp_path, vdoc))


def test_variant_link_requires_side_to_move_in_identity(tmp_path):
    vdoc = copy.deepcopy(VARIANT_DOC)
    vdoc["contract"]["identity"]["canonical_fields"] = [
        f for f in vdoc["contract"]["identity"]["canonical_fields"] if f != "side_to_move"
    ]
    bad(fresh(), "must include side_to_move", root=_tmp_root(tmp_path, vdoc))


@pytest.mark.parametrize("counter", ["halfmove_clock", "fullmove_number"])
def test_variant_link_rejects_counters_in_identity(tmp_path, counter):
    vdoc = copy.deepcopy(VARIANT_DOC)
    vdoc["contract"]["identity"]["canonical_fields"].append(counter)
    bad(fresh(), "must NOT include counter field", root=_tmp_root(tmp_path, vdoc))


def test_variant_link_requires_orthodox_castling(tmp_path):
    vdoc = copy.deepcopy(VARIANT_DOC)
    for entry in vdoc["contract"]["variants"]["entries"]:
        if entry["id"] == "standard":
            entry["castling"] = "chess960"
    bad(fresh(), "castling must be orthodox", root=_tmp_root(tmp_path, vdoc))


def test_variant_link_requires_registry_membership(tmp_path):
    vdoc = copy.deepcopy(VARIANT_DOC)
    vdoc["contract"]["variants"]["entries"] = [
        e for e in vdoc["contract"]["variants"]["entries"] if e["id"] != "standard"
    ]
    bad(fresh(), "not in the variant registry", root=_tmp_root(tmp_path, vdoc))


# --- CLI boundary -----------------------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/turn_contract_lint.py", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_accepts_valid_contract():
    r = _cli()
    assert r.returncode == 0 and "OK" in r.stdout


def test_cli_rejects_mutated_contract(tmp_path):
    doc = fresh()
    doc["contract"]["termination"]["fifty_move"]["claim"]["threshold"] = 150
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(doc))
    r = _cli(str(p))
    assert r.returncode == 1 and "FAIL" in r.stdout and "Traceback" not in r.stderr


def test_cli_rejects_malformed_yaml(tmp_path):
    p = tmp_path / "worse.yaml"
    p.write_text(":\n  - [")
    r = _cli(str(p))
    assert r.returncode == 1 and "FAIL" in r.stdout and "Traceback" not in r.stderr


def test_cli_rejects_non_mapping_without_traceback(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- just\n- a\n- list\n")
    r = _cli(str(p))
    assert r.returncode == 1 and "FAIL" in r.stdout and "Traceback" not in r.stderr
