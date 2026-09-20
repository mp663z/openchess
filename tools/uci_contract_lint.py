"""T0104: chess UCI contract lint.

Enforces the normative CONTENT of data/contracts/uci.yaml by EXACT
STRUCTURED COMPARISON, never prose-marker presence: transport framing
and whitespace rules, long-algebraic move encoding with the lowercase
promotion enum, the exact GUI command set and per-command grammars,
go parameter kinds with duplicate ban and integer grammar, engine
responses with per-type option tails and info field kinds, the
lifecycle ordering (uci first, position-before-go, stop/ponderhit
preconditions, nothing after quit, rejection changes no state), the
three-class failure model with its exact mapping (no orphan classes,
no undeclared error codes), the closed error enum + exact shape,
canonical serialization, and linkage VERIFIED AGAINST THE ACTUAL
sibling artifacts (legal-moves square grammar and promotion enum,
FEN six-field order). Anything less passes silently and every engine
session rots.

    python tools/uci_contract_lint.py [path]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "uci.yaml"
LEGAL_MOVES = ROOT / "data" / "contracts" / "legal_moves.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"

BYTE_FRAMING = {
    "input": "byte-stream",
    "frame_delimiter": "exactly-one-LF-per-frame",
    "crlf": "malformed",
    "bare_cr": "malformed",
    "unterminated_trailing_bytes": "malformed",
    "empty_frame": "malformed",
    "invalid_utf8": "malformed",
    "one_command_per_frame": True,
    "chunking":
        "frames-may-arrive-split-across-arbitrary-read-boundaries",
}
LINE_GRAMMAR = {
    "token_separator": "single-space",
    "leading_trailing_whitespace": "forbidden",
    "empty_line": "malformed",
    "keyword_case": "exact-lowercase",
}
MOVE_ENCODING = {
    "form": "from-square-then-to-square-then-optional-promotion-letter",
    "square_grammar": "exactly-file-char-then-rank-char-per-legal-moves",
    "promotion_letters": ["q", "r", "b", "n"],
    "promotion_letter_case": "lowercase",
    "castling": "king-two-square-move-per-legal-moves",
    "none_token": "(none)",
}
GUI_COMMANDS = {
    "uci": "bare-keyword",
    "debug": "keyword-then-on-or-off",
    "isready": "bare-keyword",
    "setoption": "keyword-name-then-id-then-optional-value",
    "register": "keyword-then-later-or-name-code-pair",
    "ucinewgame": "bare-keyword",
    "position":
        "keyword-then-startpos-or-fen-then-optional-moves-validated",
    "go": "keyword-then-zero-or-more-declared-parameters",
    "stop": "bare-keyword",
    "ponderhit": "bare-keyword",
    "quit": "bare-keyword",
}
MOVE_LIST_GREEDY = "until-next-declared-parameter-keyword"
INT_GRAMMAR = "ascii-digits-0-9-only-no-leading-zeros-except-zero-itself"
GO_PARAMETERS = {
    "searchmoves": {"kind": "move-list", "min": 1,
                    "greedy": MOVE_LIST_GREEDY},
    "ponder": {"kind": "flag"},
    "wtime": {"kind": "nonneg-int"},
    "btime": {"kind": "nonneg-int"},
    "winc": {"kind": "nonneg-int"},
    "binc": {"kind": "nonneg-int"},
    "movestogo": {"kind": "pos-int"},
    "depth": {"kind": "pos-int"},
    "nodes": {"kind": "pos-int"},
    "mate": {"kind": "pos-int"},
    "movetime": {"kind": "pos-int"},
    "infinite": {"kind": "flag"},
}
ENGINE_RESPONSES = {
    "id": "keyword-then-name-or-author-then-nonempty-rest",
    "uciok": "bare-keyword",
    "readyok": "bare-keyword",
    "bestmove": "keyword-then-move-or-none-then-optional-ponder-move",
    "copyprotection": "keyword-then-checking-ok-or-error",
    "registration": "keyword-then-checking-ok-or-error",
    "info": "keyword-then-one-or-more-declared-fields",
    "option": "keyword-name-then-id-then-type-then-type-specific-tail",
}
FIELD_GREEDY = "until-next-declared-field-keyword"
INFO_FIELDS = {
    "depth": {"kind": "nonneg-int"},
    "seldepth": {"kind": "nonneg-int"},
    "time": {"kind": "nonneg-int"},
    "nodes": {"kind": "nonneg-int"},
    "pv": {"kind": "move-list", "min": 1, "greedy": FIELD_GREEDY},
    "multipv": {"kind": "pos-int", "min": 1},
    "score": {"kind": "score", "forms": ["cp", "mate"],
              "value_kind": "int",
              "bound": {"optional": True,
                        "values": ["lowerbound", "upperbound"],
                        "at_most_once": True,
                        "position": "immediately-after-value"}},
    "currmove": {"kind": "move"},
    "currmovenumber": {"kind": "pos-int", "min": 1},
    "hashfull": {"kind": "permill-int", "min": 0, "max": 1000},
    "nps": {"kind": "nonneg-int"},
    "tbhits": {"kind": "nonneg-int"},
    "sbhits": {"kind": "nonneg-int"},
    "cpuload": {"kind": "permill-int", "min": 0, "max": 1000},
    "string": {"kind": "rest-of-line", "min": 1},
    "refutation": {"kind": "move-list", "min": 1, "greedy": FIELD_GREEDY},
    "currline": {"kind": "currline", "optional_cpunr": "nonneg-int",
                 "then_moves": "min-1"},
}
OPTION_TYPES = {
    "check": {"tail": ["default"],
              "default_kind": "bool-literal-true-or-false",
              "required": ["default"]},
    "spin": {"tail": ["default", "min", "max"], "value_kind": "int",
             "required": ["default", "min", "max"],
             "bounds": "min-le-default-le-max"},
    "combo": {"tail": ["default", "var-plus"], "required": ["var"],
              "value_kind": "span-string",
              "default_membership":
                  "default-equals-one-complete-var-when-present"},
    "button": {"tail": [], "required": []},
    "string": {"tail": ["default"],
               "default_kind": "rest-of-line-may-be-empty",
               "required": []},
}
OPTION_MARKERS = {
    "name_terminator":
        'last-"-type-"-marker-whose-next-token-is-a-declared-type',
    "combo_default_span": "from-default-marker-until-first-var-marker",
    "combo_var_span":
        "from-var-marker-until-next-var-marker-or-end-of-line",
    "span_value": "rest-of-span-nonempty",
}
POSITION_VALIDATION = {
    "fen_fields": "validated-by-linked-fen-contract-parser",
    "startpos":
        "resolves-to-linked-variant-registry-standard-start_fen",
    "moves_tail":
        "grammar-per-linked-legal-moves-move-encoding",
    "fen_malformed_maps_to": "malformed_line",
    "fen_impossible_maps_to": "malformed_line",
    "subclass_preservation": "fen-failure-class-preserved-as-subclass",
}
LIFECYCLE_STATES = [
    "pre_uci", "awaiting_uciok", "ready", "searching", "pondering",
    "stop_requested", "ponder_stop_requested", "terminated",
]
LIFECYCLE_DEBUG = {
    "model": "orthogonal-session-setting",
    "values": ["on", "off"],
    "initial": "off",
    "set_by": "debug-command",
    "accepted_in_states": ["ready", "searching", "pondering",
                           "stop_requested", "ponder_stop_requested"],
    "pre_uciok": "rejected-protocol_state",
    "state_preservation":
        "debug-never-touches-underlying-state-or-flags",
}
LIFECYCLE_COPYPROTECTION = {
    "model": "orthogonal-phase-variable",
    "phases": ["cp_idle", "cp_checking", "cp_done"],
    "initial": "cp_idle",
    "accepted_in_states": ["ready", "searching", "pondering",
                           "stop_requested", "ponder_stop_requested"],
    "pre_uciok": "rejected-protocol_state",
    "on_termination": "phase-closed-every-status-rejected",
    "state_preservation":
        "phase-changes-never-touch-lifecycle-readiness-debug-"
        "registration-or-flags",
    "transitions": {
        "cp_idle": {"checking": "cp_checking",
                    "ok": "rejected-protocol_state",
                    "error": "rejected-protocol_state"},
        "cp_checking": {"checking": "rejected-protocol_state",
                        "ok": "cp_done", "error": "cp_done"},
        "cp_done": {"checking": "rejected-protocol_state",
                    "ok": "rejected-protocol_state",
                    "error": "rejected-protocol_state"},
    },
}
LIFECYCLE_REGISTRATION = {
    "model": "orthogonal-phase-variable-with-gui-correlation",
    "phases": ["reg_awaiting_indication", "reg_indication_pending",
               "reg_unregistered", "reg_attempt_pending",
               "reg_attempt_checking", "reg_registered", "reg_failed"],
    "initial": "reg_awaiting_indication",
    "accepted_in_states": ["ready", "searching", "pondering",
                           "stop_requested", "ponder_stop_requested"],
    "pre_uciok": "rejected-protocol_state",
    "on_termination":
        "phase-closed-every-status-and-register-rejected",
    "state_preservation":
        "phase-changes-never-touch-lifecycle-readiness-debug-"
        "copyprotection-or-flags",
    "engine_transitions": {
        "reg_awaiting_indication": {
            "checking": "reg_indication_pending",
            "ok": "rejected-protocol_state",
            "error": "rejected-protocol_state"},
        "reg_indication_pending": {
            "checking": "rejected-protocol_state",
            "ok": "reg_registered",
            "error": "reg_unregistered"},
        "reg_unregistered": {
            "checking": "reg_indication_pending",
            "ok": "rejected-protocol_state",
            "error": "rejected-protocol_state"},
        "reg_attempt_pending": {
            "checking": "reg_attempt_checking",
            "ok": "rejected-protocol_state",
            "error": "rejected-protocol_state"},
        "reg_attempt_checking": {
            "checking": "rejected-protocol_state",
            "ok": "reg_registered",
            "error": "reg_failed"},
        "reg_registered": {
            "checking": "rejected-protocol_state",
            "ok": "rejected-protocol_state",
            "error": "rejected-protocol_state"},
        "reg_failed": {
            "checking": "rejected-protocol_state",
            "ok": "rejected-protocol_state",
            "error": "rejected-protocol_state"},
    },
    "gui_register_transitions": {
        "name_code": {"reg_unregistered": "reg_attempt_pending",
                      "reg_failed": "reg_attempt_pending",
                      "elsewhere": "rejected-protocol_state"},
        "later": {"reg_unregistered": "reg_unregistered",
                  "reg_failed": "reg_unregistered",
                  "elsewhere": "rejected-protocol_state"},
    },
    "correlation":
        "register-name-code-opens-exactly-one-checking-then-exactly-"
        "one-terminal",
    "register_later":
        "defers-without-opening-an-attempt-engine-may-start-one-later-"
        "checking-cycle-from-unregistered",
}
LIFECYCLE_HANDSHAKE = {
    "model": "orthogonal-handshake-progress",
    "phases": ["awaiting_name", "awaiting_author_or_options",
               "awaiting_options"],
    "initial": "awaiting_name",
    "active_in_states": ["awaiting_uciok"],
    "name_required":
        "exactly-one-id-name-before-any-option-and-uciok",
    "author": "optional-at-most-one-after-name-before-first-option",
    "ordering": "name-then-optional-author-then-options-then-uciok",
    "uciok_target": "ready",
    "on_termination": "handshake-events-rejected-protocol_state",
    "transitions": {
        "awaiting_name": {
            "id_name": "awaiting_author_or_options",
            "id_author": "rejected-protocol_state",
            "option": "rejected-protocol_state",
            "uciok": "rejected-protocol_state"},
        "awaiting_author_or_options": {
            "id_name": "rejected-protocol_state",
            "id_author": "awaiting_options",
            "option": "awaiting_options",
            "uciok": "ready"},
        "awaiting_options": {
            "id_name": "rejected-protocol_state",
            "id_author": "rejected-protocol_state",
            "option": "awaiting_options",
            "uciok": "ready"},
    },
}
LIFECYCLE_READINESS = {
    "model": "orthogonal-pending-flag",
    "set_by": "isready",
    "set_from_states": ["ready", "searching", "pondering",
                        "stop_requested", "ponder_stop_requested"],
    "cleared_by": "readyok-or-termination",
    "readyok_requires": "flag-set",
    "second_isready": "rejected-while-flag-set-never-queued",
    "state_preservation": "flag-changes-never-touch-underlying-state",
    "liveness_gate": "terminated-rejects-isready-and-readyok",
    "on_termination": "flag-cleared-on-entry-to-terminated",
    "post_termination_observability":
        "no-event-may-observe-or-mutate-flag",
}
LIVE_POST_UCIOK = set(LIFECYCLE_READINESS["set_from_states"])
LIFECYCLE_META = {
    "model": "bidirectional-session-state-machine",
    "initial": "pre_uci",
    "position_flag":
        "set-by-position-command-reset-by-uci-or-ucinewgame",
    "go_requires": "position_flag-set",
    "go_ponder_target":
        "pondering-when-ponder-parameter-else-searching",
    "ponder_bestmove_gate":
        "gui-release-required-even-on-mate-or-completion",
    "unlisted_pair": "protocol_state",
}
SEARCH_START = "search-start"
LIFECYCLE_TRANSITIONS = {
    "pre_uci": {"gui": {"uci": "awaiting_uciok", "quit": "terminated"},
                "engine": {}},
    "awaiting_uciok": {"gui": {"quit": "terminated"},
                       "engine": {}},
    "ready": {"gui": {"setoption": "ready", "ucinewgame": "ready",
                      "position": "ready", "go": SEARCH_START,
                      "quit": "terminated"},
              "engine": {}},
    "searching": {"gui": {"stop": "stop_requested",
                          "quit": "terminated"},
                  "engine": {"info": "searching",
                             "bestmove": "ready"}},
    "pondering": {"gui": {"ponderhit": "searching",
                          "stop": "ponder_stop_requested",
                          "quit": "terminated"},
                  "engine": {"info": "pondering"}},
    "stop_requested": {"gui": {"quit": "terminated"},
                       "engine": {"info": "stop_requested",
                                  "bestmove": "ready"}},
    "ponder_stop_requested": {"gui": {"ponderhit": "stop_requested",
                                      "quit": "terminated"},
                              "engine": {"info": "ponder_stop_requested",
                                         "bestmove": "ready"}},
    "terminated": {"gui": {}, "engine": {}},
}
RESOLUTION_FAILURES = {
    "malformed_line": "any-token-or-grammar-violation",
    "unknown_command": "first-token-not-a-declared-keyword",
    "protocol_state": "declared-command-out-of-lifecycle-order",
}
FAILURE_CLASSES = ["malformed_line", "unknown_command",
                   "protocol_state"]
FAILURE_MAPPING = {
    "malformed_line": {
        "trigger": "any-token-or-grammar-violation",
        "error": "malformed_request",
    },
    "unknown_command": {
        "trigger": "first-token-not-a-declared-keyword",
        "error": "unknown_command",
    },
    "protocol_state": {
        "trigger": "declared-command-out-of-lifecycle-order",
        "error": "illegal_state",
    },
}
ERROR_ENUM = ["malformed_request", "unknown_command", "illegal_state",
              "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}
LINKS = {
    "legal_moves_contract": "data/contracts/legal_moves.yaml",
    "fen_contract": "data/contracts/fen.yaml",
    "variant_contract": "data/contracts/variant.yaml",
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {
    "id", "transport", "move_encoding", "gui_commands",
    "bestmove_semantics",
    "go_parameters", "engine_responses", "info_fields",
    "option_markers", "option_types", "position_validation",
    "setoption_semantics",
    "lifecycle", "resolution_failures",
    "failure_classes", "failure_mapping", "errors", "serialization",
    "links", "versioning",
}
BESTMOVE_SEMANTICS = {
    "ponder_condition": "only-when-primary-move-is-real",
    "none_tail": "ends-immediately",
}
ALLOWED_TRANSPORT = {"byte_framing", "line_grammar"}
ALLOWED_BYTE_FRAMING = set(BYTE_FRAMING) | {"rule"}
ALLOWED_LINE_GRAMMAR = set(LINE_GRAMMAR) | {"rule"}
ALLOWED_MOVE_ENCODING = set(MOVE_ENCODING) | {"rule"}
ALLOWED_GO_PARAMETERS_SECTION = set(GO_PARAMETERS) | {
    "duplicates", "int_grammar", "rule"}
ALLOWED_ENGINE_RESPONSES = set(ENGINE_RESPONSES) | {"rule"}
ALLOWED_INFO_FIELDS_SECTION = set(INFO_FIELDS) | {
    "duplicates", "int_grammar", "rule"}
ALLOWED_OPTION_MARKERS = set(OPTION_MARKERS) | {"rule"}
ALLOWED_OPTION_TYPES = set(OPTION_TYPES) | {"rule"}
ALLOWED_POSITION_VALIDATION = set(POSITION_VALIDATION) | {"rule"}
SETOPTION_SEMANTICS = {
    "registry": "declared-options-by-exact-name-from-handshake",
    "name_matching": "exact-case-sensitive-declared-name",
    "undeclared_name_maps_to": "malformed_line",
    "domain_violation_maps_to": "malformed_line",
    "duplicate_declaration_maps_to": "malformed_line",
    "repeat_setoption": "allowed-validation-idempotent",
    "state_preservation":
        "valid-setoption-mutates-no-lifecycle-or-orthogonal-state",
    "type_rules": {
        "check": "value-marker-required-exactly-true-or-false",
        "spin": "value-marker-required-integer-within-declared-min-max",
        "combo":
            "value-marker-required-exactly-one-declared-full-var-string",
        "button": "no-value-marker-permitted",
        "string": "value-marker-optional-free-form-empty-allowed",
    },
}
ALLOWED_SETOPTION_SEMANTICS = set(SETOPTION_SEMANTICS) | {"rule"}
ALLOWED_LIFECYCLE = (set(LIFECYCLE_META)
                     | {"states", "transitions", "readiness", "debug",
                        "copyprotection", "registration", "handshake",
                        "rule"})
ALLOWED_READINESS = set(LIFECYCLE_READINESS) | {"rule"}
ALLOWED_DEBUG = set(LIFECYCLE_DEBUG) | {"rule"}
ALLOWED_COPYPROTECTION = set(LIFECYCLE_COPYPROTECTION) | {"rule"}
ALLOWED_REGISTRATION = set(LIFECYCLE_REGISTRATION) | {"rule"}
ALLOWED_HANDSHAKE = set(LIFECYCLE_HANDSHAKE) | {"rule"}
ALLOWED_RESOLUTION = set(RESOLUTION_FAILURES) | {"rule"}
ALLOWED_ERRORS = {"closed_enum", "shape"}
ALLOWED_SERIALIZATION = {"canonical", "roundtrip", "rule"}
ALLOWED_LINKS = set(LINKS)
ALLOWED_VERSIONING = {"base_path", "rule"}

ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ContractError(msg)


def _mapping(value, name: str) -> dict:
    _need(type(value) is dict, f"{name}: mapping required")
    return value


def _text(value, name: str) -> str:
    _need(type(value) is str and value.strip(),
          f"{name}: nonempty documentation string required")
    return value


def _keys(mapping: dict, allowed: set, name: str) -> None:
    extra = set(mapping) - allowed
    missing = allowed - set(mapping)
    _need(not extra, f"{name}: undeclared keys {sorted(extra)}")
    _need(not missing, f"{name}: missing keys {sorted(missing)}")


def _exact(actual, expected, name: str) -> None:
    _need(actual == expected and type(actual) is type(expected),
          f"{name}: expected {expected!r}, got {actual!r}")


def _load(path: Path, name: str) -> dict:
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(
            f"{name}: cannot load {path}: {exc!r}") from exc
    _need(type(doc) is dict and "contract" in doc,
          f"{name}: contract mapping required in {path}")
    return doc["contract"]


def _check_links(root: Path) -> None:
    """Linkage verified against the ACTUAL sibling artifacts."""
    legal = _load(root / LINKS["legal_moves_contract"],
                  "legal-moves link")
    model = _mapping(legal.get("move_model"), "legal_moves.move_model")
    grammar = _mapping(model.get("square_grammar"),
                       "legal_moves square grammar")
    _exact(grammar.get("form"), "exactly-file-char-then-rank-char",
           "legal_moves square grammar form")
    _exact(grammar.get("files"), ["a", "b", "c", "d", "e", "f", "g", "h"],
           "legal_moves square files")
    _exact(grammar.get("ranks"),
           ["1", "2", "3", "4", "5", "6", "7", "8"],
           "legal_moves square ranks")
    shape = _mapping(model.get("shape"), "legal_moves move shape")
    types = _mapping(shape.get("types"), "legal_moves move types")
    _exact(_mapping(types.get("promotion"),
                    "legal_moves promotion type").get("enum"),
           ["q", "r", "b", "n"], "legal_moves promotion enum")

    fen = _load(root / LINKS["fen_contract"], "fen link")
    fields = _mapping(fen.get("fields"), "fen.fields")
    _exact(fields.get("order"),
           ["placement", "active_color", "castling", "en_passant",
            "halfmove_clock", "fullmove_number"],
           "fen field order")
    _exact(fields.get("exactly_six"), True, "fen exactly_six")
    rules = _mapping(fen.get("position_rules"), "fen.position_rules")
    for key, value in {
            "white_kings": "exactly-1",
            "black_kings": "exactly-1",
            "kings_adjacent": "forbidden",
            "pawns_on_back_ranks": "forbidden",
            "non_mover_king_attacked": "forbidden"}.items():
        _exact(rules.get(key), value, f"fen.position_rules.{key}")
    fen_ep = _mapping(fen.get("en_passant"), "fen.en_passant")
    _exact(fen_ep.get("storage"), "recorded-on-every-two-square-advance",
           "fen.en_passant.storage")
    _exact(fen_ep.get("ranks"), ["3", "6"], "fen.en_passant.ranks")

    variant = _load(root / LINKS["variant_contract"], "variant link")
    entries = variant.get("variants", {}).get("entries")
    _need(type(entries) is list and entries,
          "variant registry: nonempty entries required")
    standard = [e for e in entries
                if isinstance(e, dict) and e.get("id") == "standard"]
    _need(len(standard) == 1,
          "variant registry: exactly one standard entry required")
    _exact(standard[0].get("start_fen"),
           "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
           "variant registry standard start_fen")


def lint(doc: dict, root: Path | None = None) -> None:
    root = root or ROOT
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _exact(doc.get("schema_version"), 1, "schema_version")
    contract = _mapping(doc.get("contract"), "contract")
    _keys(contract, ALLOWED_CONTRACT, "contract")
    _exact(contract.get("id"), "chess-uci", "contract.id")

    transport = _mapping(contract.get("transport"), "contract.transport")
    _keys(transport, ALLOWED_TRANSPORT, "contract.transport")
    framing = _mapping(transport.get("byte_framing"),
                       "transport.byte_framing")
    _keys(framing, ALLOWED_BYTE_FRAMING, "transport.byte_framing")
    for key, value in BYTE_FRAMING.items():
        _exact(framing.get(key), value, f"transport.byte_framing.{key}")
    _text(framing.get("rule"), "transport.byte_framing.rule")
    grammar = _mapping(transport.get("line_grammar"),
                       "transport.line_grammar")
    _keys(grammar, ALLOWED_LINE_GRAMMAR, "transport.line_grammar")
    for key, value in LINE_GRAMMAR.items():
        _exact(grammar.get(key), value, f"transport.line_grammar.{key}")
    _text(grammar.get("rule"), "transport.line_grammar.rule")

    enc = _mapping(contract.get("move_encoding"),
                   "contract.move_encoding")
    _keys(enc, ALLOWED_MOVE_ENCODING, "contract.move_encoding")
    for key, value in MOVE_ENCODING.items():
        _exact(enc.get(key), value, f"move_encoding.{key}")
    _text(enc.get("rule"), "move_encoding.rule")

    commands = _mapping(contract.get("gui_commands"),
                        "contract.gui_commands")
    _exact(commands, GUI_COMMANDS | {"rule": commands.get("rule")},
           "contract.gui_commands")
    _text(commands.get("rule"), "gui_commands.rule")

    gop = _mapping(contract.get("go_parameters"),
                   "contract.go_parameters")
    _keys(gop, ALLOWED_GO_PARAMETERS_SECTION, "contract.go_parameters")
    for key, value in GO_PARAMETERS.items():
        _exact(gop.get(key), value, f"go_parameters.{key}")
    _exact(gop.get("duplicates"), "forbidden", "go_parameters.duplicates")
    _exact(gop.get("int_grammar"), INT_GRAMMAR,
           "go_parameters.int_grammar")
    _text(gop.get("rule"), "go_parameters.rule")

    bms = _mapping(contract.get("bestmove_semantics"),
                   "contract.bestmove_semantics")
    _keys(bms, set(BESTMOVE_SEMANTICS) | {"rule"},
          "contract.bestmove_semantics")
    for key, value in BESTMOVE_SEMANTICS.items():
        _exact(bms.get(key), value, f"bestmove_semantics.{key}")
    _text(bms.get("rule"), "bestmove_semantics.rule")

    responses = _mapping(contract.get("engine_responses"),
                         "contract.engine_responses")
    _exact(responses,
           ENGINE_RESPONSES | {"rule": responses.get("rule")},
           "contract.engine_responses")
    _text(responses.get("rule"), "engine_responses.rule")

    info = _mapping(contract.get("info_fields"), "contract.info_fields")
    _keys(info, ALLOWED_INFO_FIELDS_SECTION, "contract.info_fields")
    for key, value in INFO_FIELDS.items():
        _exact(info.get(key), value, f"info_fields.{key}")
    _exact(info.get("duplicates"), "forbidden", "info_fields.duplicates")
    _exact(info.get("int_grammar"), INT_GRAMMAR,
           "info_fields.int_grammar")
    _text(info.get("rule"), "info_fields.rule")

    markers = _mapping(contract.get("option_markers"),
                       "contract.option_markers")
    _keys(markers, ALLOWED_OPTION_MARKERS, "contract.option_markers")
    for key, value in OPTION_MARKERS.items():
        _exact(markers.get(key), value, f"option_markers.{key}")
    _text(markers.get("rule"), "option_markers.rule")

    option = _mapping(contract.get("option_types"),
                      "contract.option_types")
    _keys(option, ALLOWED_OPTION_TYPES, "contract.option_types")
    for key, value in OPTION_TYPES.items():
        _exact(option.get(key), value, f"option_types.{key}")
    _text(option.get("rule"), "option_types.rule")

    sem = _mapping(contract.get("setoption_semantics"),
                   "contract.setoption_semantics")
    _keys(sem, ALLOWED_SETOPTION_SEMANTICS,
          "contract.setoption_semantics")
    for key, value in SETOPTION_SEMANTICS.items():
        _exact(sem.get(key), value, f"setoption_semantics.{key}")
    _need(set(sem["type_rules"]) == (
        set(OPTION_TYPES) - {"rule"}),
          "setoption_semantics.type_rules must cover every declared "
          "option type")
    for key in ("undeclared_name_maps_to", "domain_violation_maps_to",
                "duplicate_declaration_maps_to"):
        _need(sem[key] in _mapping(contract.get("failure_mapping"),
                                   "contract.failure_mapping"),
              f"setoption_semantics.{key} must map to a declared "
              "failure class")
    _text(sem.get("rule"), "setoption_semantics.rule")

    pv = _mapping(contract.get("position_validation"),
                  "contract.position_validation")
    _keys(pv, ALLOWED_POSITION_VALIDATION,
          "contract.position_validation")
    for key, value in POSITION_VALIDATION.items():
        _exact(pv.get(key), value, f"position_validation.{key}")
    _text(pv.get("rule"), "position_validation.rule")

    life = _mapping(contract.get("lifecycle"), "contract.lifecycle")
    _keys(life, ALLOWED_LIFECYCLE, "contract.lifecycle")
    for key, value in LIFECYCLE_META.items():
        _exact(life.get(key), value, f"lifecycle.{key}")
    _exact(life.get("states"), LIFECYCLE_STATES, "lifecycle.states")
    transitions = _mapping(life.get("transitions"),
                           "lifecycle.transitions")
    _exact(transitions, LIFECYCLE_TRANSITIONS,
           "lifecycle.transitions")
    states = set(LIFECYCLE_STATES)
    _need(set(transitions) == states,
          "lifecycle.transitions: must define every declared state")
    for state, pair in transitions.items():
        for direction, mapping in pair.items():
            _need(direction in ("gui", "engine"),
                  f"lifecycle.transitions.{state}: direction"
                  f" {direction!r} must be gui or engine")
            for event, target in mapping.items():
                if target == SEARCH_START:
                    _need(state == "ready" and event == "go",
                          "lifecycle.transitions: search-start only"
                          " on ready/go")
                    continue
                _need(target in states,
                      f"lifecycle.transitions.{state}.{direction}"
                      f".{event}: target {target!r} not a declared"
                      " state")
    _need(LIFECYCLE_META["unlisted_pair"] in FAILURE_CLASSES,
          "lifecycle.unlisted_pair must name a declared failure class")
    for state, pair in transitions.items():
        for direction, mapping in pair.items():
            for event in mapping:
                _need(event not in ("isready", "readyok"),
                      f"lifecycle.transitions.{state}.{direction}: "
                      f"{event} belongs to the orthogonal readiness "
                      "flag, never the state table")
                _need(event != "debug",
                      f"lifecycle.transitions.{state}.{direction}: "
                      "debug belongs to the orthogonal session "
                      "setting, never the state table")
                _need(event not in ("copyprotection", "registration",
                                    "register"),
                      f"lifecycle.transitions.{state}.{direction}: "
                      f"{event} belongs to the orthogonal phase "
                      "variables, never the state table")
                _need(direction != "engine"
                      or event not in ("id", "option", "uciok"),
                      f"lifecycle.transitions.{state}.{direction}: "
                      f"{event} belongs to the orthogonal handshake "
                      "progress, never the state table")
    debug = _mapping(life.get("debug"), "lifecycle.debug")
    _keys(debug, ALLOWED_DEBUG, "lifecycle.debug")
    for key, value in LIFECYCLE_DEBUG.items():
        _exact(debug.get(key), value, f"lifecycle.debug.{key}")
    _need(set(debug["accepted_in_states"]) <= states,
          "lifecycle.debug.accepted_in_states must be declared "
          "states")
    _need(not ({"pre_uci", "awaiting_uciok", "terminated"}
               & set(debug["accepted_in_states"])),
          "lifecycle.debug.accepted_in_states must exclude pre-uciok "
          "and terminal states (pinned rejection)")
    _text(debug.get("rule"), "lifecycle.debug.rule")

    for section, pins, allowed, label in (
            ("copyprotection", LIFECYCLE_COPYPROTECTION,
             ALLOWED_COPYPROTECTION, "lifecycle.copyprotection"),
            ("registration", LIFECYCLE_REGISTRATION,
             ALLOWED_REGISTRATION, "lifecycle.registration")):
        spec = _mapping(life.get(section), label)
        _keys(spec, allowed, label)
        for key, value in pins.items():
            _exact(spec.get(key), value, f"{label}.{key}")
        _need(set(spec["accepted_in_states"]) <= states,
              f"{label}.accepted_in_states must be declared states")
        _need(not ({"pre_uci", "awaiting_uciok", "terminated"}
                   & set(spec["accepted_in_states"])),
              f"{label}.accepted_in_states must exclude pre-uciok "
              "and terminal states (pinned rejection)")
        _need(spec["initial"] in spec["phases"],
              f"{label}.initial must be a declared phase")
        tables = [spec["transitions"]] if section == "copyprotection" \
            else [spec["engine_transitions"]]
        for table in tables:
            _need(set(table) == set(spec["phases"]),
                  f"{label}: every phase needs a transition row")
            for phase, row in table.items():
                for status, target in row.items():
                    _need(target in spec["phases"]
                          or target == "rejected-protocol_state",
                          f"{label}.{phase}.{status}: target must be "
                          "a declared phase or the pinned rejection")
        _text(spec.get("rule"), f"{label}.rule")

    handshake = _mapping(life.get("handshake"), "lifecycle.handshake")
    _keys(handshake, ALLOWED_HANDSHAKE, "lifecycle.handshake")
    for key, value in LIFECYCLE_HANDSHAKE.items():
        _exact(handshake.get(key), value, f"lifecycle.handshake.{key}")
    _need(set(handshake["active_in_states"]) <= states,
          "lifecycle.handshake.active_in_states must be declared "
          "states")
    _need(handshake["initial"] in handshake["phases"],
          "lifecycle.handshake.initial must be a declared phase")
    _need(set(handshake["transitions"]) == set(handshake["phases"]),
          "lifecycle.handshake: every phase needs a transition row")
    for phase, row in handshake["transitions"].items():
        for event, target in row.items():
            _need(target in handshake["phases"]
                  or target in ("rejected-protocol_state",
                                handshake["uciok_target"]),
                  f"lifecycle.handshake.{phase}.{event}: target must "
                  "be a declared phase, the pinned rejection or the "
                  "pinned uciok target")
    _text(handshake.get("rule"), "lifecycle.handshake.rule")

    readiness = _mapping(life.get("readiness"), "lifecycle.readiness")
    _keys(readiness, ALLOWED_READINESS, "lifecycle.readiness")
    for key, value in LIFECYCLE_READINESS.items():
        _exact(readiness.get(key), value, f"lifecycle.readiness.{key}")
    _need(set(readiness["set_from_states"]) <= states,
          "lifecycle.readiness.set_from_states must be declared states")
    _need("terminated" not in readiness["set_from_states"],
          "lifecycle.readiness.set_from_states must exclude the "
          "terminal state (liveness gate)")
    _text(readiness.get("rule"), "lifecycle.readiness.rule")
    _text(life.get("rule"), "lifecycle.rule")

    res = _mapping(contract.get("resolution_failures"),
                   "contract.resolution_failures")
    _keys(res, ALLOWED_RESOLUTION, "contract.resolution_failures")
    for key, value in RESOLUTION_FAILURES.items():
        _exact(res.get(key), value, f"resolution_failures.{key}")
    _text(res.get("rule"), "resolution_failures.rule")

    classes = contract.get("failure_classes")
    _exact(classes, FAILURE_CLASSES, "failure_classes")
    mapping = _mapping(contract.get("failure_mapping"),
                       "contract.failure_mapping")
    _exact(mapping, FAILURE_MAPPING, "failure_mapping")
    _need(set(mapping) == set(classes),
          "failure_mapping must cover exactly the declared classes")

    errors = _mapping(contract.get("errors"), "contract.errors")
    _keys(errors, ALLOWED_ERRORS, "contract.errors")
    enum = errors.get("closed_enum")
    _need(type(enum) is list, "errors.closed_enum: list required")
    for i, code in enumerate(enum):
        _need(type(code) is str and ERROR_CODE_RE.match(code),
              f"errors.closed_enum[{i}]: snake_case string required,"
              f" got {code!r}")
    _need(len(set(enum)) == len(enum),
          "errors.closed_enum: duplicate codes")
    for code in ("malformed_request", "unknown_command",
                 "illegal_state"):
        _need(code in enum, f"errors.closed_enum: {code} required")
    _exact(errors.get("shape"), ERROR_SHAPE, "errors.shape")
    for cls, m in FAILURE_MAPPING.items():
        _need(m["error"] in enum,
              f"failure_mapping.{cls}: error {m['error']!r} not in the"
              " closed enum")

    ser = _mapping(contract.get("serialization"),
                   "contract.serialization")
    _keys(ser, ALLOWED_SERIALIZATION, "contract.serialization")
    _exact(ser.get("canonical"), True, "serialization.canonical")
    _exact(ser.get("roundtrip"), "parse-of-emit-is-structure-identity",
           "serialization.roundtrip")
    _text(ser.get("rule"), "serialization.rule")

    links = _mapping(contract.get("links"), "contract.links")
    _keys(links, ALLOWED_LINKS, "contract.links")
    _exact(links, LINKS, "contract.links")
    _check_links(root)

    versioning = _mapping(contract.get("versioning"),
                          "contract.versioning")
    _keys(versioning, ALLOWED_VERSIONING, "contract.versioning")
    base = _text(versioning.get("base_path"), "versioning.base_path")
    _need(re.fullmatch(r"/uci/v[1-9][0-9]*", base),
          "versioning.base_path: must be /uci/vN with numeric N")
    vrule = _text(versioning.get("rule"), "versioning.rule")
    for marker in ("MINOR", "MAJOR", "downgrade"):
        _need(marker in vrule,
              f"versioning.rule: must state {marker!r}")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        print(f"FAIL uci contract lint: malformed YAML: {exc}")
        return 1
    try:
        lint(doc)
    except ContractError as exc:
        print(f"FAIL uci contract lint: {exc}")
        return 1
    except Exception as exc:  # classified rejection, never a traceback
        print(f"FAIL uci contract lint: internal error: {exc!r}")
        return 1
    print("OK uci contract lint: chess UCI contract v1 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
