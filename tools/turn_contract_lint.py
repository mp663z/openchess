"""T0050: chess turn contract lint.

Enforces the normative CONTENT of data/contracts/turn.yaml by EXACT
STRUCTURED COMPARISON, never prose-marker presence: the state fields,
bounds, transition operations (side flip, fullmove increment-when-black,
halfmove reset predicate and fallback), per-move cardinality, null-move
policy, identity participation booleans, termination closure policies,
the claim-vs-automatic fifty-move thresholds, the explicit per-class
failure mapping (no orphan classes, no undeclared error codes), the
closed error enum + exact shape, versioning, and the chess-variant
linkage VERIFIED AGAINST THE ACTUAL variant contract artifact (registry,
castling kind, identity field participation). Prose fields are required
to be nonempty documentation only. Anything less passes silently and
the turn machine rots.

    python tools/turn_contract_lint.py [path]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.variant_contract_lint import ContractError as _VariantContractError  # noqa: E402
from tools.variant_contract_lint import lint as _variant_lint  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "turn.yaml"

STATE_FIELDS = ["side_to_move", "halfmove_clock", "fullmove_number"]
SIDE_VALUES = ["w", "b"]
BOUNDS = {"halfmove_clock": {"min": 0}, "fullmove_number": {"min": 1}}
APPLIES_TO = "single-legal-move"
ON_MOVE = {
    "side_to_move": "flip",
    "fullmove_number": {"op": "increment", "amount": 1, "when": "black"},
    "halfmove_clock": {
        "reset_when": ["pawn_move", "capture"],
        "reset_to": 0,
        "otherwise": {"op": "increment", "amount": 1},
    },
}
PER_MOVE = "exactly-one"
NULL_MOVE = {"policy": "reject", "failure": "illegal_transition"}
IDENTITY = {"counters_participate": False, "side_to_move_participates": True}
TERMINATION_STATES = [
    "checkmate",
    "stalemate",
    "resignation",
    "timeout",
    "draw_agreement",
    "fifty_move_claim",
    "seventyfive_move_auto",
    "fivefold_auto",
    "threefold_claim",
    "insufficient_material",
    "variant_specific",
]
ON_TRANSITION_AFTER_TERMINATION = {"policy": "reject", "failure": "illegal_transition"}
UNKNOWN_STATE = {"policy": "reject", "failure": "unknown_termination"}
FIFTY_MOVE = {
    "claim": {"threshold": 100, "state": "fifty_move_claim", "automatic": False},
    "automatic": {"threshold": 150, "state": "seventyfive_move_auto", "automatic": True},
}
FAILURE_CLASSES = [
    "no_side_to_move",
    "bad_counter",
    "illegal_transition",
    "unknown_termination",
]
FAILURE_MAPPING = {
    "no_side_to_move": {
        "trigger": "side-missing-or-not-in-side_values",
        "error": "malformed_request",
    },
    "bad_counter": {
        "trigger": "counter-out-of-bounds-or-non-integer",
        "error": "malformed_request",
    },
    "illegal_transition": {
        "trigger": "transition-on-terminated-state-or-null-move",
        "error": "illegal_transition",
    },
    "unknown_termination": {
        "trigger": "termination-state-not-in-declared-enum",
        "error": "unknown_termination",
    },
}
ERROR_ENUM = ["malformed_request", "illegal_transition", "unknown_termination", "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}
INVARIANTS = [
    "side_to_move is exactly one declared side value",
    "halfmove_clock is an integer >= 0",
    "fullmove_number is an integer >= 1",
    "counters never participate in position identity",
    "side_to_move always participates in position identity",
    "a terminated state accepts no transition",
]
VARIANT_PATH = "data/contracts/variant.yaml"
APPLIES_TO_VARIANTS = ["standard"]

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {
    "id",
    "state",
    "transition",
    "termination",
    "invariants",
    "failure_classes",
    "failure_mapping",
    "errors",
    "versioning",
    "variant_link",
}
ALLOWED_STATE = {"fields", "side_values", "bounds", "rule"}
ALLOWED_TRANSITION = {"applies_to", "on_move", "per_move", "null_move", "identity", "rule"}
ALLOWED_TERMINATION = {
    "states",
    "closed_after",
    "on_transition_after_termination",
    "unknown_state",
    "fifty_move",
    "rule",
}
ALLOWED_ERRORS = {"closed_enum", "shape"}
ALLOWED_VERSIONING = {"base_path", "rule"}
ALLOWED_VARIANT_LINK = {"contract", "path", "applies_to_variants", "rule"}
ERROR_CODE_RE = re.compile(r"^[a-z][a-z_]*$")


class ContractError(Exception):
    pass


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(problem)


def _mapping(node: object, where: str) -> dict:
    _need(type(node) is dict, f"{where}: mapping required")
    return node


def _keys(node: dict, allowed: set[str], where: str) -> None:
    for key in node:
        _need(type(key) is str, f"{where}: non-string key {key!r}")
    unknown = set(node) - allowed
    _need(not unknown, f"{where}: unknown keys {sorted(unknown)}")
    missing = allowed - set(node)
    _need(not missing, f"{where}: missing keys {sorted(missing)}")


def _text(node: object, where: str) -> str:
    _need(type(node) is str and node.strip(), f"{where}: nonempty text")
    return node


def _strict_eq(actual: object, expected: object, where: str) -> None:
    """Recursive exact comparison: type(actual) is type(expected) at
    EVERY scalar node (bool/int conflation is a violation), containers
    match in exact keys/order and length. Ordinary == accepts 0 for
    False and 1 for True; this never does."""
    if type(expected) is dict:
        _need(type(actual) is dict, f"{where}: mapping required")
        _need(
            list(actual) == list(expected),
            f"{where}: exact keys/order {list(expected)!r}",
        )
        for key in expected:
            _strict_eq(actual[key], expected[key], f"{where}.{key}")
    elif type(expected) is list:
        _need(type(actual) is list, f"{where}: list required")
        _need(len(actual) == len(expected), f"{where}: exact {expected!r}")
        for i in range(len(expected)):
            _strict_eq(actual[i], expected[i], f"{where}[{i}]")
    else:
        _need(
            type(actual) is type(expected) and actual == expected,
            f"{where}: exact {expected!r}",
        )


def _exact(node: object, expected: object, where: str) -> None:
    _strict_eq(node, expected, where)


def _check_variant_link(link: dict, root: Path) -> None:
    """Verify the linked chess-variant artifact really says what this
    contract claims: it exists, its id matches, every applies_to variant
    is registered with orthodox castling, and position identity includes
    side_to_move but never the counters."""
    path = root / link["path"]
    _need(path.is_file(), f"variant_link.path: {link['path']} does not exist")
    try:
        vdoc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ContractError(f"variant_link.path: malformed YAML: {exc}") from exc
    # the linked artifact must pass its OWN contract lint before any
    # claim relies on it: exact canonical tuple, unique variant ids,
    # declared castling kinds, exact schema - never a projection of it
    try:
        _variant_lint(vdoc)
    except _VariantContractError as exc:
        raise ContractError(
            f"variant_link.path: linked contract fails its own lint: {exc}"
        ) from exc
    vcontract = vdoc["contract"]
    _need(
        vcontract.get("id") == link["contract"],
        f"variant_link: linked id {vcontract.get('id')!r} != {link['contract']!r}",
    )
    identity = vcontract.get("identity")
    _need(type(identity) is dict, "variant identity: mapping required")
    canonical = identity.get("canonical_fields")
    _need(type(canonical) is list, "variant identity.canonical_fields: list required")
    _need(
        "side_to_move" in canonical,
        "variant identity must include side_to_move (turn/identity linkage)",
    )
    for counter in ("halfmove_clock", "fullmove_number", "halfmove", "fullmove"):
        _need(
            counter not in canonical,
            f"variant identity must NOT include counter field {counter!r}",
        )
    variants = vcontract.get("variants")
    _need(type(variants) is dict, "variant variants: mapping required")
    entries = variants.get("entries")
    _need(type(entries) is list, "variant variants.entries: list required")
    registry = {}
    for entry in entries:
        _need(type(entry) is dict, "variants.entries: mapping entries required")
        vid = entry.get("id")
        _need(
            type(vid) is str and vid not in registry,
            f"variants.entries: unique string variant ids required, got {vid!r}",
        )
        registry[vid] = entry.get("castling")
    for vid in link["applies_to_variants"]:
        _need(vid in registry, f"variant_link: {vid!r} not in the variant registry")
        _need(
            registry[vid] == "orthodox",
            f"variant_link: {vid!r} castling must be orthodox, got {registry[vid]!r}",
        )


def lint(doc: object, root: Path = ROOT) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _need(
        doc.get("schema_version") == 1 and type(doc.get("schema_version")) is int,
        "schema_version: exact int 1",
    )
    contract = _mapping(doc.get("contract"), "contract")
    _keys(contract, ALLOWED_CONTRACT, "contract")
    _need(contract.get("id") == "chess-turn", "contract.id: must be chess-turn")

    state = _mapping(contract.get("state"), "contract.state")
    _keys(state, ALLOWED_STATE, "contract.state")
    _exact(state.get("fields"), STATE_FIELDS, "state.fields")
    _exact(state.get("side_values"), SIDE_VALUES, "state.side_values")
    _exact(state.get("bounds"), BOUNDS, "state.bounds")
    _text(state.get("rule"), "state.rule")

    transition = _mapping(contract.get("transition"), "contract.transition")
    _keys(transition, ALLOWED_TRANSITION, "contract.transition")
    _exact(transition.get("applies_to"), APPLIES_TO, "transition.applies_to")
    _exact(transition.get("on_move"), ON_MOVE, "transition.on_move")
    _exact(transition.get("per_move"), PER_MOVE, "transition.per_move")
    _exact(transition.get("null_move"), NULL_MOVE, "transition.null_move")
    _exact(transition.get("identity"), IDENTITY, "transition.identity")
    _text(transition.get("rule"), "transition.rule")

    termination = _mapping(contract.get("termination"), "contract.termination")
    _keys(termination, ALLOWED_TERMINATION, "contract.termination")
    _exact(termination.get("states"), TERMINATION_STATES, "termination.states")
    _exact(termination.get("closed_after"), True, "termination.closed_after")
    _exact(
        termination.get("on_transition_after_termination"),
        ON_TRANSITION_AFTER_TERMINATION,
        "termination.on_transition_after_termination",
    )
    _exact(termination.get("unknown_state"), UNKNOWN_STATE, "termination.unknown_state")
    fifty = termination.get("fifty_move")
    _exact(fifty, FIFTY_MOVE, "termination.fifty_move")
    # the claim must stay strictly below the automatic threshold, and the
    # thresholds are the FIDE fifty/seventyfive-move numbers exactly
    _need(
        fifty["claim"]["threshold"] == 50 * 2,
        "termination.fifty_move.claim.threshold: 50-move rule is 100 halfmoves",
    )
    _need(
        fifty["automatic"]["threshold"] == 75 * 2,
        "termination.fifty_move.automatic.threshold: 75-move rule is 150 halfmoves",
    )
    _need(
        fifty["claim"]["state"] in termination["states"]
        and fifty["automatic"]["state"] in termination["states"],
        "termination.fifty_move: referenced states must be declared",
    )
    _text(termination.get("rule"), "termination.rule")

    _exact(contract.get("invariants"), INVARIANTS, "contract.invariants")
    _exact(contract.get("failure_classes"), FAILURE_CLASSES, "contract.failure_classes")

    fmap = _mapping(contract.get("failure_mapping"), "contract.failure_mapping")
    # exact key gate first (typo'd/orphan classes are violations), then
    # exact per-class mapping: no unused or ambiguous failure classes
    _keys(fmap, set(FAILURE_MAPPING), "contract.failure_mapping")
    _exact(fmap, FAILURE_MAPPING, "contract.failure_mapping")

    errors = _mapping(contract.get("errors"), "contract.errors")
    _keys(errors, ALLOWED_ERRORS, "contract.errors")
    enum = errors.get("closed_enum")
    _need(type(enum) is list and enum, "errors.closed_enum: nonempty list")
    for i, code in enumerate(enum):
        _need(
            type(code) is str and ERROR_CODE_RE.match(code),
            f"errors.closed_enum[{i}]: snake_case string required, got {code!r}",
        )
    _need(len(set(enum)) == len(enum), "errors.closed_enum: duplicate codes")
    for code in ("malformed_request", "illegal_transition", "unknown_termination"):
        _need(code in enum, f"errors.closed_enum: {code} required")
    _exact(errors.get("shape"), ERROR_SHAPE, "errors.shape")
    # every error code the failure mapping names must be declared
    for cls, mapping in FAILURE_MAPPING.items():
        _need(
            mapping["error"] in enum,
            f"failure_mapping.{cls}: error {mapping['error']!r} not in the closed enum",
        )

    versioning = _mapping(contract.get("versioning"), "contract.versioning")
    _keys(versioning, ALLOWED_VERSIONING, "contract.versioning")
    base = _text(versioning.get("base_path"), "versioning.base_path")
    _need(
        re.fullmatch(r"/turn/v[1-9][0-9]*", base),
        "versioning.base_path: must be /turn/vN with numeric N",
    )
    vrule = _text(versioning.get("rule"), "versioning.rule")
    for marker in ("MINOR", "MAJOR", "downgrade"):
        _need(marker in vrule, f"versioning.rule: must state {marker!r}")

    link = _mapping(contract.get("variant_link"), "contract.variant_link")
    _keys(link, ALLOWED_VARIANT_LINK, "contract.variant_link")
    _exact(link.get("contract"), "chess-variant", "variant_link.contract")
    _exact(link.get("path"), VARIANT_PATH, "variant_link.path")
    _exact(link.get("applies_to_variants"), APPLIES_TO_VARIANTS, "variant_link.applies_to_variants")
    _text(link.get("rule"), "variant_link.rule")
    _check_variant_link(link, root)


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        print(f"FAIL turn contract lint: malformed YAML: {exc}")
        return 1
    try:
        lint(doc)
    except ContractError as exc:
        print(f"FAIL turn contract lint: {exc}")
        return 1
    except Exception as exc:  # classified rejection, never a traceback
        print(f"FAIL turn contract lint: internal error: {exc!r}")
        return 1
    print("OK turn contract lint: chess turn contract v1 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
