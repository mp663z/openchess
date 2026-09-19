"""T0068: chess en-passant contract lint.

Enforces the normative CONTENT of data/contracts/en_passant.yaml by EXACT
STRUCTURED COMPARISON, never prose-marker presence: the target grammar
(none sentinel or file plus rank 3/6 only), the set-on event (exactly a
two-square pawn advance from the start rank, target = the skipped
square), the one-ply lifetime (opponent only, cleared by any move), the
storage-vs-identity split (stored on every advance; identity VALUE is
the target only when a legal capture exists), the capture mechanics
(capturing pawn to the target square, captured pawn removed from the
target file on the captured rank - NEVER the destination), the four
preconditions including resulting-position king safety (the double
removal pin), turn linkage (exactly one transition, halfmove RESET,
fullmove when black), identity framing (canonical field ALWAYS
participates, value conditional), the explicit failure_mapping (no
orphan classes, no undeclared error codes), the closed error enum +
exact shape, and versioning. Cross-artifact linkage is VERIFIED AGAINST
THE ACTUAL variant and turn contract artifacts (each linted first): the
variant contract pins en_passant as a canonical identity field and a
declared FEN failure class; the turn contract's halfmove reset set MUST
contain pawn_move and capture (en-passant is both).

    python tools/en_passant_contract_lint.py [path]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.turn_contract_lint import lint as _turn_lint  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402
from tools.variant_contract_lint import lint as _variant_lint  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "en_passant.yaml"

GRAMMAR = {
    "none_sentinel": "-",
    "files": ["a", "b", "c", "d", "e", "f", "g", "h"],
    "ranks": ["3", "6"],
    "empty": "forbidden",
}
SET_ON = {
    "event": "pawn-two-square-advance",
    "target_file": "advancing-pawn-file",
    "white": {"from_rank": "2", "to_rank": "4", "target_rank": "3"},
    "black": {"from_rank": "7", "to_rank": "5", "target_rank": "6"},
}
LIFETIME = {
    "duration": "exactly-one-ply",
    "available_to": "opponent-of-the-advancing-side",
    "cleared_by": "any-move",
}
STORAGE_VS_IDENTITY = {
    "storage": "recorded-on-every-two-square-advance",
    "identity_value": "target-when-legal-capture-else-none",
}
MOVER = {
    "piece": "pawn-of-side-to-move",
    "white": {"mover_rank": "5", "captured_rank": "5", "target_rank": "6"},
    "black": {"mover_rank": "4", "captured_rank": "4", "target_rank": "3"},
    "file_relation": "adjacent-to-target-file",
}
MECHANICS = {
    "destination": "the-target-square",
    "captured_square": {"file": "target-file", "rank": "captured-rank"},
}
PRECONDITIONS = [
    "target set on the immediately preceding move",
    "capturing pawn on its mover rank in a file adjacent to the target file",
    "an enemy pawn on the captured square",
    "the resulting position leaves the mover's own king unattacked",
]
TURN_LINKAGE = {"transitions": "exactly-one", "halfmove_clock": "reset",
                "fullmove_number": "increment-when-black"}
IDENTITY = {
    "canonical_field": "en_passant",
    "participates": "always",
    "value_rule": "target-when-at-least-one-legal-capture-else-none-sentinel",
}
FAILURE_CLASSES = ["target_malformed", "target_inconsistent",
                   "capture_precondition", "pinned_capture"]
FAILURE_MAPPING = {
    "target_malformed": {"trigger": "target-field-not-canonical",
                         "error": "malformed_request"},
    "target_inconsistent": {"trigger": "target-set-but-no-enemy-pawn-on-captured-square",
                            "error": "illegal_position"},
    "capture_precondition": {"trigger": "no-adjacent-pawn-or-target-stale",
                             "error": "illegal_move"},
    "pinned_capture": {"trigger": "resulting-position-leaves-own-king-attacked",
                       "error": "illegal_move"},
}
ERROR_ENUM = ["malformed_request", "illegal_position", "illegal_move", "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}
LINKS = {"variant_contract": "data/contracts/variant.yaml",
         "turn_contract": "data/contracts/turn.yaml"}
VERSIONING = {
    "base_path": "/en-passant/v1",
    "client_pin": "MAJOR",
    "minor_policy": "additive-only",
    "minor_additions": ["new-optional-fields"],
    "downgrade_policy": "any-earlier-minor-within-major-without-migration",
    "major_bump": "new-base-path-required",
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {"id", "target", "capture", "turn_linkage", "identity",
                    "failure_classes", "failure_mapping", "errors", "links",
                    "versioning"}
ALLOWED_TARGET = {"grammar", "set_on", "lifetime", "storage_vs_identity",
                  "rule"}
ALLOWED_CAPTURE = {"mover", "mechanics", "preconditions", "rule"}
ALLOWED_RANK_TRIPLE = {"from_rank", "to_rank", "target_rank"}
ALLOWED_MOVER_SIDE = {"mover_rank", "captured_rank", "target_rank"}


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(f"en-passant contract: {problem}")


def _keys(node: dict, allowed: set[str], where: str) -> None:
    _need(type(node) is dict, f"{where}: mapping required")
    unknown = set(node) - allowed
    _need(not unknown, f"{where}: unknown keys {sorted(unknown)}")


def _get(node: object, key: str, where: str) -> object:
    """Presence-checked access: a missing key is a contract violation,
    never a raw KeyError."""
    _need(type(node) is dict, f"{where}: mapping required")
    _need(key in node, f"{where}: missing key {key!r}")
    return node[key]


def _text(node: object, where: str) -> str:
    _need(type(node) is str and node.strip(), f"{where}: nonempty documentation string required")
    return node


def _strict_eq(actual: object, expected: object, where: str) -> None:
    _need(type(actual) is type(expected),
          f"{where}: type {type(actual).__name__} != {type(expected).__name__}")
    if isinstance(actual, dict):
        _need(set(actual) == set(expected),
              f"{where}: keys {sorted(actual)} != {sorted(expected)}")
        for k in actual:
            _strict_eq(actual[k], expected[k], f"{where}.{k}")
    elif isinstance(actual, list):
        _need(actual == expected, f"{where}: {actual!r} != {expected!r}")
    else:
        _need(actual == expected, f"{where}: {actual!r} != {expected!r}")


def _check_links(links: dict, root: Path) -> None:
    _strict_eq(links, LINKS, "contract.links")
    vdoc = yaml.safe_load((root / LINKS["variant_contract"]).read_text())
    _variant_lint(vdoc)  # lint BEFORE relying on the linked artifact
    canonical = vdoc["contract"]["identity"]["canonical_fields"]
    _need("en_passant" in canonical,
          "linked variant contract: en_passant not a canonical identity field")
    fen_failures = vdoc["contract"]["fen"]["failure_classes"]
    _need("bad_en_passant" in fen_failures,
          "linked variant contract: bad_en_passant not a declared FEN failure class")
    tdoc = yaml.safe_load((root / LINKS["turn_contract"]).read_text())
    _turn_lint(tdoc)
    reset_when = tdoc["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_when"]
    _need("pawn_move" in reset_when and "capture" in reset_when,
          "linked turn contract: en-passant is a pawn move AND a capture - "
          "both must reset the halfmove clock")
    errors = tdoc["contract"]["errors"]["closed_enum"]
    _need(type(errors) is list and errors, "linked turn contract: empty error enum")


def lint(doc: object, root: Path = ROOT) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _need(doc.get("schema_version") == 1 and type(doc.get("schema_version")) is int,
          "schema_version: exact int 1")
    c = _get(doc, "contract", "document")
    _keys(c, ALLOWED_CONTRACT, "contract")
    for required in ALLOWED_CONTRACT:
        _get(c, required, "contract")
    _need(c["id"] == "chess-en-passant", "contract.id must be chess-en-passant")

    target = _get(c, "target", "contract")
    _keys(target, ALLOWED_TARGET, "contract.target")
    grammar = _get(target, "grammar", "contract.target")
    _keys(grammar, {"none_sentinel", "files", "ranks", "empty", "rule"},
          "contract.target.grammar")
    _strict_eq({k: v for k, v in grammar.items() if k != "rule"},
               GRAMMAR, "contract.target.grammar.fields")
    _text(_get(grammar, "rule", "contract.target.grammar"),
          "contract.target.grammar.rule")
    set_on = _get(target, "set_on", "contract.target")
    _keys(set_on, {"event", "target_file", "white", "black", "rule"}, "contract.target.set_on")
    for side in ("white", "black"):
        _keys(_get(set_on, side, "contract.target.set_on"), ALLOWED_RANK_TRIPLE,
              f"contract.target.set_on.{side}")
    _strict_eq({k: v for k, v in set_on.items() if k != "rule"},
               SET_ON, "contract.target.set_on.fields")
    _text(_get(set_on, "rule", "contract.target.set_on"),
          "contract.target.set_on.rule")
    lifetime = _get(target, "lifetime", "contract.target")
    _keys(lifetime, {"duration", "available_to", "cleared_by", "rule"},
          "contract.target.lifetime")
    _strict_eq({k: v for k, v in lifetime.items() if k != "rule"},
               LIFETIME, "contract.target.lifetime.fields")
    _text(_get(lifetime, "rule", "contract.target.lifetime"),
          "contract.target.lifetime.rule")
    svi = _get(target, "storage_vs_identity", "contract.target")
    _keys(svi, {"storage", "identity_value", "rule"},
          "contract.target.storage_vs_identity")
    _strict_eq({k: v for k, v in svi.items() if k != "rule"},
               STORAGE_VS_IDENTITY, "contract.target.storage_vs_identity.fields")
    _text(_get(svi, "rule", "contract.target.storage_vs_identity"),
          "contract.target.storage_vs_identity.rule")
    _text(_get(target, "rule", "contract.target"), "contract.target.rule")

    capture = _get(c, "capture", "contract")
    _keys(capture, ALLOWED_CAPTURE, "contract.capture")
    mover = _get(capture, "mover", "contract.capture")
    _keys(mover, {"piece", "white", "black", "file_relation"},
          "contract.capture.mover")
    for side in ("white", "black"):
        _keys(_get(mover, side, "contract.capture.mover"), ALLOWED_MOVER_SIDE,
              f"contract.capture.mover.{side}")
    _strict_eq(mover, MOVER, "contract.capture.mover")
    mechanics = _get(capture, "mechanics", "contract.capture")
    _keys(mechanics, {"destination", "captured_square", "rule"},
          "contract.capture.mechanics")
    _strict_eq({k: v for k, v in mechanics.items() if k != "rule"},
               MECHANICS, "contract.capture.mechanics.fields")
    _text(_get(mechanics, "rule", "contract.capture.mechanics"),
          "contract.capture.mechanics.rule")
    _strict_eq(_get(capture, "preconditions", "contract.capture"),
               PRECONDITIONS, "contract.capture.preconditions")
    _text(_get(capture, "rule", "contract.capture"), "contract.capture.rule")

    # internal consistency: set-on ranks, mover ranks and grammar agree
    _need(SET_ON["white"]["target_rank"] == GRAMMAR["ranks"][0]
          and SET_ON["black"]["target_rank"] == GRAMMAR["ranks"][1],
          "set_on target ranks disagree with the grammar's declared ranks")
    _need(MOVER["white"]["target_rank"] == SET_ON["black"]["target_rank"],
          "white captures against BLACK's advance: target ranks disagree")
    _need(MOVER["black"]["target_rank"] == SET_ON["white"]["target_rank"],
          "black captures against WHITE's advance: target ranks disagree")
    for side in ("white", "black"):
        m = MOVER[side]
        _need(m["mover_rank"] == m["captured_rank"],
              f"{side}: mover and captured pawn stand on the same rank")
        _need(m["mover_rank"] != m["target_rank"],
              f"{side}: mover rank must differ from the target rank")

    linkage = _get(c, "turn_linkage", "contract")
    _keys(linkage, {"transitions", "halfmove_clock", "fullmove_number", "rule"},
          "contract.turn_linkage")
    _strict_eq({k: v for k, v in linkage.items() if k != "rule"},
               TURN_LINKAGE, "contract.turn_linkage.fields")
    _text(_get(linkage, "rule", "contract.turn_linkage"),
          "contract.turn_linkage.rule")

    identity = _get(c, "identity", "contract")
    _keys(identity, {"canonical_field", "participates", "value_rule", "rule"},
          "contract.identity")
    _strict_eq({k: v for k, v in identity.items() if k != "rule"},
               IDENTITY, "contract.identity.fields")
    _text(_get(identity, "rule", "contract.identity"),
          "contract.identity.rule")
    _need(IDENTITY["canonical_field"] == "en_passant",
          "identity.canonical_field must be en_passant (variant contract field)")

    _strict_eq(_get(c, "failure_classes", "contract"),
               FAILURE_CLASSES, "contract.failure_classes")
    mapping = _get(c, "failure_mapping", "contract")
    _strict_eq(mapping, FAILURE_MAPPING, "contract.failure_mapping")
    errors = _get(c, "errors", "contract")
    _strict_eq(errors, {"closed_enum": ERROR_ENUM, "shape": ERROR_SHAPE},
               "contract.errors")
    for cls in FAILURE_CLASSES:
        err = mapping[cls]["error"]
        _need(err in errors["closed_enum"],
              f"failure class {cls} maps to undeclared error {err}")
    versioning = _get(c, "versioning", "contract")
    _keys(versioning, {"base_path", "client_pin", "minor_policy",
                       "minor_additions", "downgrade_policy", "major_bump",
                       "rule"}, "contract.versioning")
    _strict_eq({k: v for k, v in versioning.items() if k != "rule"},
               VERSIONING, "contract.versioning.fields")
    _text(_get(versioning, "rule", "contract.versioning"),
          "contract.versioning.rule")
    _check_links(_get(c, "links", "contract"), root)


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        path = Path(argv[1]).resolve()
        root = path.parents[2] if path.parent.name == "contracts" else ROOT
    else:
        path, root = CONTRACT, ROOT
    try:
        lint(yaml.safe_load(path.read_text()), root)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("OK en-passant contract lint: chess en-passant contract v1 clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
