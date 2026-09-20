"""T0104: the UCI contract is the enforced source of truth.

The contract (data/contracts/uci.yaml) declares the transport framing,
long-algebraic move encoding, the exact GUI command set and go
parameters, engine responses with info fields and option types, the
session lifecycle, the three-class failure model, and canonical
serialization. This battery proves the behavior with an EXECUTABLE
parser/emitter/session model DERIVED FROM the contract data (move
grammar from the LINKED legal-moves contract, FEN field count from the
LINKED FEN contract), then replays contradiction/reversal/rogue-key/
enum mutations plus linkage mutations against the real sibling
contracts.
"""

from __future__ import annotations

import contextlib
import copy
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_t0086_fen_contract import FenError
from tests.test_t0086_fen_contract import parse_fen as fen_parse
from tools.uci_contract_lint import lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "uci.yaml"
DATA_DIR = ROOT / "data" / "contracts"


def _doc():
    return yaml.safe_load(CONTRACT.read_text())


def _lint():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "uci_contract_lint.py")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


@__import__("functools").cache
def _square_grammar():
    path = DATA_DIR / "legal_moves.yaml"
    with open(path) as fh:
        g = yaml.safe_load(fh)["contract"]["move_model"]["square_grammar"]
    return g["files"], g["ranks"]


@__import__("functools").cache
def _fen_contract():
    path = DATA_DIR / "fen.yaml"
    with open(path) as fh:
        return yaml.safe_load(fh)["contract"]


@__import__("functools").cache
def _fen_field_count():
    path = DATA_DIR / "fen.yaml"
    with open(path) as fh:
        fields = yaml.safe_load(fh)["contract"]["fields"]
    assert fields["exactly_six"] is True
    return len(fields["order"])


class UciError(Exception):
    def __init__(self, failure_class, code, subclass=None):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code
        self.subclass = subclass


def _fail(contract, failure_class):
    raise UciError(failure_class,
                   contract["failure_mapping"][failure_class]["error"])


# -- executable reference parser/emitter, contract-derived ------------

class FrameReader:
    """The contract's byte-framing layer, derived from
    transport.byte_framing: a byte stream in, complete frames out.
    Each frame is exactly one LF-terminated line; any CR byte, an
    empty frame, invalid UTF-8, or unterminated trailing bytes are
    malformed at THIS layer. Buffering never depends on chunking."""

    def __init__(self, contract):
        framing = contract["transport"]["byte_framing"]
        assert framing["input"] == "byte-stream"
        assert framing["frame_delimiter"] == "exactly-one-LF-per-frame"
        assert framing["one_command_per_frame"] is True
        self.c = contract
        self.buffer = b""

    def feed(self, chunk: bytes):
        self.buffer += chunk
        frames = []
        while b"\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n", 1)
            frames.append(self._decode(raw))
        return frames

    def finish(self):
        if self.buffer:
            _fail(self.c, "malformed_line")  # unterminated bytes

    def _decode(self, raw: bytes) -> str:
        framing = self.c["transport"]["byte_framing"]
        if raw == b"":
            _fail(self.c, "malformed_line")  # empty_frame
        if framing["crlf"] == "malformed" and b"\r" in raw:
            _fail(self.c, "malformed_line")  # crlf / bare_cr
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _fail(self.c, "malformed_line")  # invalid_utf8


def _check_line(contract, line):
    t = contract["transport"]["line_grammar"]
    if type(line) is not str or line == "":
        _fail(contract, "malformed_line")
    if line != line.strip():
        _fail(contract, "malformed_line")
    if "  " in line or "\t" in line:
        _fail(contract, "malformed_line")
    if t["keyword_case"] == "exact-lowercase":
        first = line.split(" ")[0]
        if first != first.lower():
            _fail(contract, "unknown_command")
    return line.split(" ")


def _is_move(contract, token):
    """Move grammar derived from the contract move_encoding and the
    LINKED legal-moves square grammar + promotion enum."""
    enc = contract["move_encoding"]
    files, ranks = _square_grammar()
    if type(token) is not str or len(token) not in (4, 5):
        return False
    if not (token[0] in files and token[1] in ranks
            and token[2] in files and token[3] in ranks):
        return False
    return len(token) != 5 or token[4] in enc["promotion_letters"]


def _int_kind(contract, token, kind, section, spec=None):
    grammar = section["int_grammar"]
    assert grammar == ("ascii-digits-0-9-only-no-leading-zeros-"
                       "except-zero-itself")
    if type(token) is not str or not re.fullmatch(r"[0-9]+", token):
        return None
    if len(token) > 1 and token[0] == "0":
        return None
    value = int(token)
    if kind == "pos-int" and value < 1:
        return None
    if spec is not None:
        # declared bounds, read from the contract, never hardcoded
        if "min" in spec and value < spec["min"]:
            return None
        if "max" in spec and value > spec["max"]:
            return None
    return value


def _parse_move_list(contract, tokens, keywords, section):
    """Greedy move list: move tokens until the next declared keyword;
    at least one move required."""
    out = []
    for tok in tokens:
        if tok in keywords:
            break
        if not _is_move(contract, tok):
            _fail(contract, "malformed_line")
        out.append(tok)
    if not out:
        _fail(contract, "malformed_line")
    return out


def parse_gui(contract, line):
    c = contract
    tokens = _check_line(c, line)
    kw = tokens[0]
    if kw not in c["gui_commands"]:
        _fail(c, "unknown_command")
    form = c["gui_commands"][kw]
    rest = tokens[1:]
    if form == "bare-keyword":
        if rest:
            _fail(c, "malformed_line")
        return {"command": kw}
    if kw == "debug":
        if rest != ["on"] and rest != ["off"]:
            _fail(c, "malformed_line")
        return {"command": "debug", "value": rest[0]}
    if kw == "setoption":
        if len(rest) < 2 or rest[0] != "name":
            _fail(c, "malformed_line")
        body = rest[1:]
        if "value" in body:
            i = body.index("value")
            name, value = body[:i], body[i + 1:]
        else:
            name, value = body, None
        if not name:
            _fail(c, "malformed_line")
        return {"command": "setoption", "name": " ".join(name),
                "value": None if value is None else " ".join(value)}
    if kw == "register":
        if rest == ["later"]:
            return {"command": "register", "mode": "later"}
        if (len(rest) >= 4 and rest[0] == "name" and "code" in rest):
            i = rest.index("code")
            name, code = rest[1:i], rest[i + 1:]
            if name and code:
                return {"command": "register", "mode": "name_code",
                        "name": " ".join(name), "code": " ".join(code)}
        _fail(c, "malformed_line")
    if kw == "position":
        moves = None
        body = rest
        if "moves" in rest:
            i = rest.index("moves")
            body, tail = rest[:i], rest[i + 1:]
            moves = []
            for tok in tail:
                if not _is_move(c, tok):
                    _fail(c, "malformed_line")
                moves.append(tok)
            if not moves:
                _fail(c, "malformed_line")
        if body == ["startpos"]:
            out = {"command": "position", "setup": "startpos"}
        elif len(body) == 1 + _fen_field_count() and body[0] == "fen":
            # position_validation: the joined fields are validated by
            # the LINKED FEN contract parser; its failure class is
            # preserved as the subclass while both map to
            # malformed_line at this layer.
            pv = c["position_validation"]
            assert pv["fen_fields"] == (
                "validated-by-linked-fen-contract-parser")
            fen_contract = _fen_contract()
            try:
                fen_parse(fen_contract, " ".join(body[1:]))
            except FenError as exc:
                raise UciError(
                    "malformed_line",
                    c["failure_mapping"]["malformed_line"]["error"],
                    subclass=exc.failure_class) from exc
            out = {"command": "position", "setup": "fen",
                   "fen": body[1:]}
        else:
            _fail(c, "malformed_line")
        if moves is not None:
            out["moves"] = moves
        return out
    if kw == "go":
        params = c["go_parameters"]
        out = {}
        i = 0
        while i < len(rest):
            key = rest[i]
            if key not in params:
                _fail(c, "malformed_line")
            if key in out:
                _fail(c, "malformed_line")  # duplicates: forbidden
            spec = params[key]
            kind = spec["kind"]
            if kind == "flag":
                out[key] = True
                i += 1
            elif kind == "move-list":
                moves = _parse_move_list(c, rest[i + 1:], params,
                                         c["go_parameters"])
                out[key] = moves
                i += 1 + len(moves)
            else:
                if i + 1 >= len(rest):
                    _fail(c, "malformed_line")
                value = _int_kind(c, rest[i + 1], kind,
                                  c["go_parameters"])
                if value is None:
                    _fail(c, "malformed_line")
                out[key] = value
                i += 2
        return {"command": "go", "parameters": out}
    raise AssertionError(f"declared command {kw} without a parser arm")


def parse_engine(contract, line):
    c = contract
    tokens = _check_line(c, line)
    kw = tokens[0]
    if kw not in c["engine_responses"]:
        _fail(c, "unknown_command")
    form = c["engine_responses"][kw]
    rest = tokens[1:]
    if form == "bare-keyword":
        if rest:
            _fail(c, "malformed_line")
        return {"response": kw}
    if kw == "id":
        if len(rest) < 2 or rest[0] not in ("name", "author"):
            _fail(c, "malformed_line")
        return {"response": "id", "kind": rest[0],
                "text": " ".join(rest[1:])}
    if kw in ("copyprotection", "registration"):
        if rest not in (["checking"], ["ok"], ["error"]):
            _fail(c, "malformed_line")
        return {"response": kw, "state": rest[0]}
    if kw == "bestmove":
        none = c["move_encoding"]["none_token"]
        bms = c["bestmove_semantics"]
        assert bms["ponder_condition"] == (
            "only-when-primary-move-is-real")
        assert bms["none_tail"] == "ends-immediately"
        if not rest or (rest[0] != none and not _is_move(c, rest[0])):
            _fail(c, "malformed_line")
        out = {"response": "bestmove", "move": rest[0]}
        if len(rest) > 1:
            # Relational constraint: ponder is conditional on a REAL
            # best move; the none token ends the line immediately -
            # no legal move means no predicted reply.
            if rest[0] == none:
                _fail(c, "malformed_line")
            if (len(rest) != 3 or rest[1] != "ponder"
                    or not _is_move(c, rest[2])):
                _fail(c, "malformed_line")
            out["ponder"] = rest[2]
        return out
    if kw == "info":
        fields = c["info_fields"]
        if not rest:
            _fail(c, "malformed_line")
        out = {}
        i = 0
        while i < len(rest):
            key = rest[i]
            if key not in fields:
                _fail(c, "malformed_line")
            if key in out:
                _fail(c, "malformed_line")  # duplicates: forbidden
            spec = fields[key]
            kind = spec["kind"]
            if kind in ("pos-int", "nonneg-int", "permill-int"):
                if i + 1 >= len(rest):
                    _fail(c, "malformed_line")
                value = _int_kind(c, rest[i + 1], kind,
                                  c["info_fields"], spec)
                if value is None:
                    _fail(c, "malformed_line")
                out[key] = value
                i += 2
            elif kind == "move":
                if i + 1 >= len(rest) or not _is_move(c, rest[i + 1]):
                    _fail(c, "malformed_line")
                out[key] = rest[i + 1]
                i += 2
            elif kind == "move-list":
                moves = _parse_move_list(c, rest[i + 1:], fields,
                                         c["info_fields"])
                out[key] = moves
                i += 1 + len(moves)
            elif kind == "score":
                if (i + 2 >= len(rest) + 1 or i + 2 > len(rest) - 1
                        or rest[i + 1] not in spec["forms"]):
                    _fail(c, "malformed_line")
                raw = rest[i + 2]
                if not re.fullmatch(r"-?[0-9]+", raw):
                    _fail(c, "malformed_line")
                digits = raw.lstrip("-")
                if len(digits) > 1 and digits[0] == "0":
                    _fail(c, "malformed_line")
                entry = {"form": rest[i + 1], "value": int(raw)}
                bound = spec["bound"]
                consumed = 3
                if (i + 3 < len(rest)
                        and rest[i + 3] in bound["values"]):
                    entry["bound"] = rest[i + 3]
                    consumed = 4
                out[key] = entry
                i += consumed
            elif kind == "rest-of-line":
                text = rest[i + 1:]
                if not text:
                    _fail(c, "malformed_line")
                out[key] = " ".join(text)
                i = len(rest)
            elif kind == "currline":
                tail = rest[i + 1:]
                cpunr = None
                if (tail and _int_kind(c, tail[0], "nonneg-int",
                                       c["info_fields"]) is not None
                        and len(tail) > 1 and _is_move(c, tail[1])):
                    cpunr = int(tail[0])
                    tail = tail[1:]
                moves = _parse_move_list(c, tail, fields,
                                         c["info_fields"])
                out[key] = ({"cpunr": cpunr, "moves": moves}
                            if cpunr is not None else {"moves": moves})
                i += 1 + (1 if cpunr is not None else 0) + len(moves)
            else:
                raise AssertionError(f"undeclared info kind {kind}")
        return {"response": "info", "fields": out}
    if kw == "option":
        types = c["option_types"]
        markers = c["option_markers"]
        assert markers["name_terminator"] == (
            'last-"-type-"-marker-whose-next-token-is-a-declared-type')
        if not rest or rest[0] != "name":
            _fail(c, "malformed_line")
        candidates = [j for j in range(1, len(rest) - 1)
                      if rest[j] == "type" and rest[j + 1] in types]
        if not candidates:
            _fail(c, "malformed_line")
        ti = candidates[-1]  # the LAST marker before a declared type
        name = rest[1:ti]
        if not name:
            _fail(c, "malformed_line")
        otype = rest[ti + 1]
        tail = rest[ti + 2:]
        out = {"response": "option", "name": " ".join(name),
               "type": otype}
        spec = types[otype]
        if otype == "button":
            if tail:
                _fail(c, "malformed_line")
        elif otype == "check":
            if (len(tail) != 2 or tail[0] != "default"
                    or tail[1] not in ("true", "false")):
                _fail(c, "malformed_line")
            out["default"] = tail[1] == "true"
        elif otype == "spin":
            vals = {}
            j = 0
            while j < len(tail):
                if (tail[j] not in ("default", "min", "max")
                        or j + 1 >= len(tail)):
                    _fail(c, "malformed_line")
                if not re.fullmatch(r"-?[0-9]+", tail[j + 1]):
                    _fail(c, "malformed_line")
                if tail[j] in vals:
                    _fail(c, "malformed_line")
                vals[tail[j]] = int(tail[j + 1])
                j += 2
            if set(vals) != {"default", "min", "max"}:
                _fail(c, "malformed_line")
            if not vals["min"] <= vals["default"] <= vals["max"]:
                _fail(c, "malformed_line")
            out.update(vals)
        elif otype == "combo":
            # Marker-delimited spans: an optional default span first
            # (until the first var marker), then one or more var
            # spans (until the next var marker or end of line); every
            # span value nonempty; the default must equal one
            # complete var string.
            varz = []
            default = None
            j = 0
            if j < len(tail) and tail[j] == "default":
                k = j + 1
                while k < len(tail) and tail[k] != "var":
                    if tail[k] in ("default",):
                        _fail(c, "malformed_line")
                    k += 1
                span = tail[j + 1:k]
                if not span:
                    _fail(c, "malformed_line")
                default = " ".join(span)
                j = k
            while j < len(tail):
                if tail[j] != "var":
                    _fail(c, "malformed_line")
                k = j + 1
                while k < len(tail) and tail[k] != "var":
                    if tail[k] == "default":
                        _fail(c, "malformed_line")
                    k += 1
                span = tail[j + 1:k]
                if not span:
                    _fail(c, "malformed_line")
                varz.append(" ".join(span))
                j = k
            if not varz:
                _fail(c, "malformed_line")
            if default is not None and default not in varz:
                _fail(c, "malformed_line")
            out["var"] = varz
            if default is not None:
                out["default"] = default
        elif otype == "string" and tail:
            if tail[0] != "default":
                _fail(c, "malformed_line")
            out["default"] = " ".join(tail[1:])
        return out
    raise AssertionError(f"declared response {kw} without a parser arm")


def emit(struct):
    """Canonical line for a parsed structure; total over everything
    the parser accepts."""
    if "command" in struct:
        kw = struct["command"]
        if kw in ("uci", "isready", "ucinewgame", "stop", "ponderhit",
                  "quit"):
            return kw
        if kw == "debug":
            return f"debug {struct['value']}"
        if kw == "setoption":
            line = f"setoption name {struct['name']}"
            if struct["value"] is not None:
                line += " value"
                if struct["value"]:
                    line += f" {struct['value']}"
            return line
        if kw == "register":
            if struct["mode"] == "later":
                return "register later"
            return f"register name {struct['name']} code {struct['code']}"
        if kw == "position":
            line = ("position startpos" if struct["setup"] == "startpos"
                    else "position fen " + " ".join(struct["fen"]))
            if "moves" in struct:
                line += " moves " + " ".join(struct["moves"])
            return line
        if kw == "go":
            parts = ["go"]
            for key, value in struct["parameters"].items():
                if value is True:
                    parts.append(key)
                elif isinstance(value, list):
                    parts.append(key + " " + " ".join(value))
                else:
                    parts.append(f"{key} {value}")
            return " ".join(parts)
    else:
        kw = struct["response"]
        if kw in ("uciok", "readyok"):
            return kw
        if kw == "id":
            return f"id {struct['kind']} {struct['text']}"
        if kw in ("copyprotection", "registration"):
            return f"{kw} {struct['state']}"
        if kw == "bestmove":
            line = f"bestmove {struct['move']}"
            if "ponder" in struct:
                line += f" ponder {struct['ponder']}"
            return line
        if kw == "info":
            parts = ["info"]
            for key, value in struct["fields"].items():
                if isinstance(value, bool):
                    parts.append(key)
                elif isinstance(value, list):
                    parts.append(key + " " + " ".join(value))
                elif isinstance(value, dict) and "moves" in value:
                    chunk = key
                    if "cpunr" in value:
                        chunk += f" {value['cpunr']}"
                    parts.append(chunk + " " + " ".join(value["moves"]))
                elif isinstance(value, dict):
                    chunk = f"{key} {value['form']} {value['value']}"
                    if "bound" in value:
                        chunk += f" {value['bound']}"
                    parts.append(chunk)
                elif isinstance(value, int):
                    parts.append(f"{key} {value}")
                else:
                    parts.append(f"{key} {value}")
            return " ".join(parts)
        if kw == "option":
            line = f"option name {struct['name']} type {struct['type']}"
            t = struct["type"]
            if t == "check":
                line += f" default {'true' if struct['default'] else 'false'}"
            elif t == "spin":
                line += (f" default {struct['default']}"
                         f" min {struct['min']} max {struct['max']}")
            elif t == "combo":
                if "default" in struct:
                    line += f" default {struct['default']}"
                line += "".join(f" var {v}" for v in struct["var"])
            elif t == "string" and "default" in struct:
                line += f" default {struct['default']}"
            return line
    raise AssertionError(f"cannot emit {struct!r}")


# -- session lifecycle model, contract-derived -------------------------

class Session:
    """The contract lifecycle as an executable BIDIRECTIONAL state
    machine: both GUI commands and engine responses step the machine
    through the contract's own transition table. Parse failures
    (malformed/unknown) raise before any state inspection; an
    unlisted (state, event) pair is protocol_state; a rejected line
    changes NO state - the snapshot is bit-identical."""

    def __init__(self, contract):
        self.c = contract
        life = contract["lifecycle"]
        assert life["model"] == "bidirectional-session-state-machine"
        self.transitions = life["transitions"]
        self.readiness_spec = life["readiness"]
        assert self.readiness_spec["model"] == "orthogonal-pending-flag"
        self.debug_spec = life["debug"]
        assert self.debug_spec["model"] == "orthogonal-session-setting"
        self.cp_spec = life["copyprotection"]
        assert self.cp_spec["model"] == "orthogonal-phase-variable"
        self.reg_spec = life["registration"]
        assert self.reg_spec["model"] == (
            "orthogonal-phase-variable-with-gui-correlation")
        self.state = life["initial"]
        # Terminal state derived from the contract: the declared state
        # whose transition table is empty in both directions.
        self.terminal = next(
            st for st in life["states"]
            if not self.transitions[st]["gui"]
            and not self.transitions[st]["engine"])
        self.position_flag = False
        self.readiness = False
        self.debug = self.debug_spec["initial"]
        self.cp_phase = self.cp_spec["initial"]
        self.reg_phase = self.reg_spec["initial"]

    def snapshot(self):
        return (self.state, self.position_flag, self.readiness,
                self.debug, self.cp_phase, self.reg_phase)

    def _step(self, direction, event, ponder=False):
        c = self.c
        life = c["lifecycle"]
        mapping = self.transitions[self.state][direction]
        if event not in mapping:
            _fail(c, life["unlisted_pair"])
        target = mapping[event]
        if target == "search-start":
            # go_ponder_target: pondering when the ponder parameter
            # is present, searching otherwise.
            target = "pondering" if ponder else "searching"
        self.state = target
        if self.state == self.terminal:
            # on_termination: entering terminated clears the flag.
            self.readiness = False

    def _isready(self):
        c = self.c
        r = self.readiness_spec
        # Liveness gate first: terminated rejects regardless of flag.
        # Then the orthogonal rule: valid from every live post-uciok
        # state, rejected while already pending (never queued).
        if (self.state == self.terminal
                or self.state not in r["set_from_states"]
                or self.readiness):
            _fail(c, c["lifecycle"]["unlisted_pair"])
        self.readiness = True  # underlying state untouched

    def _debug(self, value):
        c = self.c
        # Orthogonal session setting: accepted in every live
        # post-uciok state (including while thinking); PINNED as
        # protocol_state pre-uciok and after termination. Changes
        # ONLY the setting - state, position flag, outstanding
        # search and readiness flag are all preserved.
        if self.state not in self.debug_spec["accepted_in_states"]:
            _fail(c, c["lifecycle"]["unlisted_pair"])
        assert value in self.debug_spec["values"]
        self.debug = value

    def _copyprotection(self, status):
        c = self.c
        spec = self.cp_spec
        # Orthogonal phase variable: liveness gate first (terminated
        # closes the phase), then the contract's own phase table -
        # terminal status without checking, duplicate checking and
        # every status after completion are protocol_state. Changes
        # ONLY the phase.
        if self.state not in spec["accepted_in_states"]:
            _fail(c, c["lifecycle"]["unlisted_pair"])
        target = spec["transitions"][self.cp_phase][status]
        if target == "rejected-protocol_state":
            _fail(c, c["lifecycle"]["unlisted_pair"])
        self.cp_phase = target

    def _registration(self, status):
        c = self.c
        spec = self.reg_spec
        # Orthogonal correlated phase variable: liveness gate first,
        # then the contract's engine transition table - unsolicited
        # terminal statuses, duplicate checkings and responses after
        # completion are protocol_state. Changes ONLY the phase.
        if self.state not in spec["accepted_in_states"]:
            _fail(c, c["lifecycle"]["unlisted_pair"])
        target = spec["engine_transitions"][self.reg_phase][status]
        if target == "rejected-protocol_state":
            _fail(c, c["lifecycle"]["unlisted_pair"])
        self.reg_phase = target

    def _register(self, mode):
        c = self.c
        spec = self.reg_spec
        # GUI correlation: register name/code opens exactly one
        # correlated attempt (only from unregistered/failed);
        # register later defers without opening an attempt. Every
        # other phase rejects (protocol_state). Changes ONLY the
        # phase - never the lifecycle state.
        if self.state not in spec["accepted_in_states"]:
            _fail(c, c["lifecycle"]["unlisted_pair"])
        mapping = spec["gui_register_transitions"][mode]
        target = mapping.get(self.reg_phase, mapping["elsewhere"])
        if target == "rejected-protocol_state":
            _fail(c, c["lifecycle"]["unlisted_pair"])
        self.reg_phase = target

    def _readyok(self):
        c = self.c
        # Liveness gate first: terminated rejects regardless of flag,
        # so a pending readyok can never bypass the empty terminated
        # transition table.
        if self.state == self.terminal or not self.readiness:
            _fail(c, c["lifecycle"]["unlisted_pair"])
        self.readiness = False  # clears ONLY the flag

    def feed_gui(self, line):
        c = self.c
        life = c["lifecycle"]
        cmd = parse_gui(c, line)  # malformed/unknown raise first
        kw = cmd["command"]
        if kw == "isready":
            self._isready()
            return cmd
        if kw == "debug":
            self._debug(cmd["value"])
            return cmd
        if kw == "register":
            self._register(cmd["mode"])
            return cmd
        if kw == "go" and self.state == "ready" and not \
                self.position_flag:
            _fail(c, life["unlisted_pair"])  # go_requires
        self._step("gui", kw,
                   ponder=(kw == "go" and cmd["parameters"].get("ponder")
                           is True))
        if kw in ("uci", "ucinewgame"):
            self.position_flag = False
        elif kw == "position":
            self.position_flag = True
        return cmd

    def feed_engine(self, line):
        c = self.c
        resp = parse_engine(c, line)  # malformed/unknown raise first
        if resp["response"] == "readyok":
            self._readyok()
            return resp
        if resp["response"] == "copyprotection":
            self._copyprotection(resp["state"])
            return resp
        if resp["response"] == "registration":
            self._registration(resp["state"])
            return resp
        self._step("engine", resp["response"])
        return resp


# -- happy: every declared command and response form --------------------

HAPPY_GUI = [
    ("uci", {"command": "uci"}),
    ("debug on", {"command": "debug", "value": "on"}),
    ("debug off", {"command": "debug", "value": "off"}),
    ("isready", {"command": "isready"}),
    ("setoption name Threads value 4",
     {"command": "setoption", "name": "Threads", "value": "4"}),
    ("setoption name Clear Hash",
     {"command": "setoption", "name": "Clear Hash", "value": None}),
    ("setoption name Slow Mover value 84",
     {"command": "setoption", "name": "Slow Mover", "value": "84"}),
    ("setoption name Threads value",
     {"command": "setoption", "name": "Threads", "value": ""}),
    ("register later", {"command": "register", "mode": "later"}),
    ("register name Stockfish Dev code abc-123",
     {"command": "register", "mode": "name_code",
      "name": "Stockfish Dev", "code": "abc-123"}),
    ("ucinewgame", {"command": "ucinewgame"}),
    ("position startpos", {"command": "position", "setup": "startpos"}),
    ("position startpos moves e2e4 e7e5 g1f3",
     {"command": "position", "setup": "startpos",
      "moves": ["e2e4", "e7e5", "g1f3"]}),
    ("position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b "
     "KQkq e3 0 1",
     {"command": "position", "setup": "fen",
      "fen": ["rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR", "b",
              "KQkq", "e3", "0", "1"]}),
    ("position fen 4k3/8/8/8/8/8/8/4K3 w - - 0 1 moves e1e2",
     {"command": "position", "setup": "fen",
      "fen": ["4k3/8/8/8/8/8/8/4K3", "w", "-", "-", "0", "1"],
      "moves": ["e1e2"]}),
    ("go", {"command": "go", "parameters": {}}),
    ("go infinite", {"command": "go", "parameters": {"infinite": True}}),
    ("go ponder wtime 300000 btime 300000 winc 2000 binc 2000",
     {"command": "go", "parameters": {"ponder": True, "wtime": 300000,
                                      "btime": 300000, "winc": 2000,
                                      "binc": 2000}}),
    ("go depth 20", {"command": "go", "parameters": {"depth": 20}}),
    ("go movetime 1000", {"command": "go",
                          "parameters": {"movetime": 1000}}),
    ("go nodes 500000", {"command": "go",
                         "parameters": {"nodes": 500000}}),
    ("go mate 3", {"command": "go", "parameters": {"mate": 3}}),
    ("go movestogo 40 wtime 60000 btime 60000",
     {"command": "go", "parameters": {"movestogo": 40, "wtime": 60000,
                                      "btime": 60000}}),
    ("go searchmoves e2e4 d2d4",
     {"command": "go",
      "parameters": {"searchmoves": ["e2e4", "d2d4"]}}),
    ("go searchmoves e2e4 depth 10",
     {"command": "go", "parameters": {"searchmoves": ["e2e4"],
                                      "depth": 10}}),
    ("go wtime 0 btime 0",
     {"command": "go", "parameters": {"wtime": 0, "btime": 0}}),
    ("stop", {"command": "stop"}),
    ("ponderhit", {"command": "ponderhit"}),
    ("quit", {"command": "quit"}),
]

HAPPY_ENGINE = [
    ("id name Stockfish 17", {"response": "id", "kind": "name",
                              "text": "Stockfish 17"}),
    ("id author the Stockfish developers",
     {"response": "id", "kind": "author",
      "text": "the Stockfish developers"}),
    ("uciok", {"response": "uciok"}),
    ("readyok", {"response": "readyok"}),
    ("bestmove e2e4", {"response": "bestmove", "move": "e2e4"}),
    ("bestmove e2e4 ponder e7e5",
     {"response": "bestmove", "move": "e2e4", "ponder": "e7e5"}),
    ("bestmove a7a8q", {"response": "bestmove", "move": "a7a8q"}),
    ("bestmove (none)", {"response": "bestmove", "move": "(none)"}),
    ("copyprotection checking",
     {"response": "copyprotection", "state": "checking"}),
    ("copyprotection ok", {"response": "copyprotection", "state": "ok"}),
    ("copyprotection error",
     {"response": "copyprotection", "state": "error"}),
    ("registration checking",
     {"response": "registration", "state": "checking"}),
    ("registration ok", {"response": "registration", "state": "ok"}),
    ("registration error",
     {"response": "registration", "state": "error"}),
    ("info depth 20 seldepth 30 time 1234 nodes 5000000 nps 4000000",
     {"response": "info", "fields": {"depth": 20, "seldepth": 30,
                                     "time": 1234, "nodes": 5000000,
                                     "nps": 4000000}}),
    ("info depth 12 score cp 35",
     {"response": "info",
      "fields": {"depth": 12, "score": {"form": "cp", "value": 35}}}),
    ("info depth 12 score cp -17",
     {"response": "info",
      "fields": {"depth": 12, "score": {"form": "cp", "value": -17}}}),
    ("info depth 25 score mate 4",
     {"response": "info",
      "fields": {"depth": 25, "score": {"form": "mate", "value": 4}}}),
    ("info depth 25 score mate -2",
     {"response": "info",
      "fields": {"depth": 25, "score": {"form": "mate", "value": -2}}}),
    ("info depth 12 score cp 35 lowerbound",
     {"response": "info",
      "fields": {"depth": 12, "score": {"form": "cp", "value": 35,
                                        "bound": "lowerbound"}}}),
    ("info depth 12 score cp -17 upperbound",
     {"response": "info",
      "fields": {"depth": 12, "score": {"form": "cp", "value": -17,
                                        "bound": "upperbound"}}}),
    ("info depth 25 score mate 4 upperbound",
     {"response": "info",
      "fields": {"depth": 25, "score": {"form": "mate", "value": 4,
                                        "bound": "upperbound"}}}),
    ("info depth 25 score mate -2 lowerbound",
     {"response": "info",
      "fields": {"depth": 25, "score": {"form": "mate", "value": -2,
                                        "bound": "lowerbound"}}}),
    ("info depth 0 seldepth 0 time 0 nodes 0 multipv 1 "
     "currmovenumber 1 hashfull 0 nps 0 tbhits 0 sbhits 0 cpuload 0",
     {"response": "info",
      "fields": {"depth": 0, "seldepth": 0, "time": 0, "nodes": 0,
                 "multipv": 1, "currmovenumber": 1, "hashfull": 0,
                 "nps": 0, "tbhits": 0, "sbhits": 0, "cpuload": 0}}),
    # domain boundaries: ordinals at their minimum 1, permill fields
    # at both inclusive bounds 0 and 1000.
    ("info multipv 1 currmovenumber 1 hashfull 1000 cpuload 1000",
     {"response": "info",
      "fields": {"multipv": 1, "currmovenumber": 1,
                 "hashfull": 1000, "cpuload": 1000}}),
    ("info depth 11 currline 0 e2e4 e7e5",
     {"response": "info", "fields": {
         "depth": 11,
         "currline": {"cpunr": 0, "moves": ["e2e4", "e7e5"]}}}),
    ("info depth 10 multipv 2 currmove e2e4 currmovenumber 1",
     {"response": "info", "fields": {"depth": 10, "multipv": 2,
                                     "currmove": "e2e4",
                                     "currmovenumber": 1}}),
    ("info depth 14 pv e2e4 e7e5 g1f3 b8c6",
     {"response": "info", "fields": {
         "depth": 14, "pv": ["e2e4", "e7e5", "g1f3", "b8c6"]}}),
    ("info depth 14 pv e2e4 e7e5 score cp 20 nodes 100",
     {"response": "info", "fields": {"depth": 14,
                                     "pv": ["e2e4", "e7e5"],
                                     "score": {"form": "cp",
                                               "value": 20},
                                     "nodes": 100}}),
    ("info depth 9 hashfull 500 tbhits 12 sbhits 3 cpuload 750",
     {"response": "info", "fields": {"depth": 9, "hashfull": 500,
                                     "tbhits": 12, "sbhits": 3,
                                     "cpuload": 750}}),
    ("info string node count low",
     {"response": "info", "fields": {"string": "node count low"}}),
    ("info depth 11 refutation d2d4 g8f6",
     {"response": "info", "fields": {"depth": 11,
                                     "refutation": ["d2d4", "g8f6"]}}),
    ("info depth 11 currline e2e4 e7e5",
     {"response": "info", "fields": {
         "depth": 11, "currline": {"moves": ["e2e4", "e7e5"]}}}),
    ("info depth 11 currline 1 e2e4 e7e5",
     {"response": "info", "fields": {
         "depth": 11,
         "currline": {"cpunr": 1, "moves": ["e2e4", "e7e5"]}}}),
    ("option name Threads type spin default 1 min 1 max 512",
     {"response": "option", "name": "Threads", "type": "spin",
      "default": 1, "min": 1, "max": 512}),
    ("option name Ponder type check default false",
     {"response": "option", "name": "Ponder", "type": "check",
      "default": False}),
    ("option name UCI_Chess960 type check default true",
     {"response": "option", "name": "UCI_Chess960", "type": "check",
      "default": True}),
    ("option name Skill Level type spin default 20 min 0 max 20",
     {"response": "option", "name": "Skill Level", "type": "spin",
      "default": 20, "min": 0, "max": 20}),
    ("option name Style type combo default Normal var Solid var "
     "Normal var Risky",
     {"response": "option", "name": "Style", "type": "combo",
      "default": "Normal", "var": ["Solid", "Normal", "Risky"]}),
    ("option name Style type combo default Very Solid var Very Solid "
     "var Aggressive",
     {"response": "option", "name": "Style", "type": "combo",
      "default": "Very Solid", "var": ["Very Solid", "Aggressive"]}),
    ("option name Style type combo var Very Solid var Aggressive",
     {"response": "option", "name": "Style", "type": "combo",
      "var": ["Very Solid", "Aggressive"]}),
    ("option name My type Filter type check default true",
     {"response": "option", "name": "My type Filter", "type": "check",
      "default": True}),
    ("option name Clear Hash type button",
     {"response": "option", "name": "Clear Hash", "type": "button"}),
    ("option name NalimovPath type string default c:/chess/tb",
     {"response": "option", "name": "NalimovPath", "type": "string",
      "default": "c:/chess/tb"}),
    ("option name Debug Log File type string",
     {"response": "option", "name": "Debug Log File",
      "type": "string"}),
]


def test_lint_clean():
    _lint()


@pytest.mark.parametrize("line,expected", HAPPY_GUI)
def test_happy_gui_commands(line, expected):
    doc = _doc()["contract"]
    got = parse_gui(doc, line)
    assert got == expected
    assert emit(got) == line  # canonical emitter, exact round trip


@pytest.mark.parametrize("line,expected", HAPPY_ENGINE)
def test_happy_engine_responses(line, expected):
    doc = _doc()["contract"]
    got = parse_engine(doc, line)
    assert got == expected
    assert emit(got) == line


def test_emit_parse_roundtrip_identity():
    doc = _doc()["contract"]
    for _line, expected in HAPPY_GUI:
        assert parse_gui(doc, emit(expected)) == expected
    for _line, expected in HAPPY_ENGINE:
        assert parse_engine(doc, emit(expected)) == expected


# -- lifecycle: happy sessions ------------------------------------------

HAPPY_TRACE = [
    ("gui", "uci", "awaiting_uciok"),
    ("engine", "id name Stockfish 17", "awaiting_uciok"),
    ("engine", "id author the Stockfish developers", "awaiting_uciok"),
    ("engine", "uciok", "ready"),
    ("gui", "setoption name Threads value 4", "ready"),
    ("gui", "debug on", "ready"),
    ("gui", "ucinewgame", "ready"),
    ("gui", "position startpos", "ready"),
    ("gui", "isready", "ready"),
    ("engine", "readyok", "ready"),
    ("gui", "go depth 10", "searching"),
    ("engine", "info depth 5 score cp 30", "searching"),
    ("gui", "stop", "stop_requested"),
    ("engine", "info depth 6 score cp 31", "stop_requested"),
    ("engine", "bestmove e2e4 ponder e7e5", "ready"),
    ("gui", "position startpos moves e2e4", "ready"),
    ("gui", "go ponder wtime 1000 btime 1000", "pondering"),
    ("engine", "info depth 1", "pondering"),
    ("gui", "stop", "ponder_stop_requested"),
    ("gui", "ponderhit", "stop_requested"),
    ("engine", "bestmove e7e5", "ready"),
    ("engine", "copyprotection checking", "ready"),
    ("engine", "copyprotection ok", "ready"),
    ("engine", "registration checking", "ready"),
    ("engine", "registration error", "ready"),   # unregistered
    ("gui", "register later", "ready"),          # deferred
    ("engine", "registration checking", "ready"),  # later re-check
    ("engine", "registration error", "ready"),   # unregistered again
    ("gui", "register name Stockfish Dev code abc-123", "ready"),
    ("engine", "registration checking", "ready"),  # correlated
    ("engine", "registration ok", "ready"),      # registered
    ("gui", "quit", "terminated"),
]


def test_lifecycle_full_session():
    doc = _doc()["contract"]
    s = Session(doc)
    for direction, line, expected_state in HAPPY_TRACE:
        if direction == "gui":
            s.feed_gui(line)
        else:
            s.feed_engine(line)
        assert s.state == expected_state, (line, s.state)
    assert s.state == "terminated"
    assert s.position_flag is True


def _ready_session():
    doc = _doc()["contract"]
    s = Session(doc)
    s.feed_gui("uci")
    s.feed_engine("uciok")
    return doc, s


def test_copyprotection_ordered_mini_protocol():
    doc, s = _ready_session()
    # terminal status without a preceding checking: rejected
    for bad in ("copyprotection ok", "copyprotection error"):
        before = s.snapshot()
        with pytest.raises(UciError) as exc:
            s.feed_engine(bad)
        assert exc.value.failure_class == "protocol_state"
        assert s.snapshot() == before
        assert s.cp_phase == "cp_idle"
    # one checking, then exactly one terminal
    s.feed_engine("copyprotection checking")
    assert s.cp_phase == "cp_checking"
    # duplicate checking while pending: rejected
    before = s.snapshot()
    with pytest.raises(UciError):
        s.feed_engine("copyprotection checking")
    assert s.snapshot() == before
    s.feed_engine("copyprotection ok")
    assert s.cp_phase == "cp_done"
    # every status after completion: rejected
    for bad in ("copyprotection checking", "copyprotection ok",
                "copyprotection error"):
        before = s.snapshot()
        with pytest.raises(UciError):
            s.feed_engine(bad)
        assert s.snapshot() == before
        assert s.cp_phase == "cp_done"


def test_copyprotection_error_completes_once():
    doc, s = _ready_session()
    s.feed_engine("copyprotection checking")
    s.feed_engine("copyprotection error")
    assert s.cp_phase == "cp_done"
    with pytest.raises(UciError):
        s.feed_engine("copyprotection ok")


def test_registration_initial_indication_and_attempt_correlation():
    doc, s = _ready_session()
    # unsolicited success: rejected (no pending checking)
    for bad in ("registration ok", "registration error"):
        before = s.snapshot()
        with pytest.raises(UciError) as exc:
            s.feed_engine(bad)
        assert exc.value.failure_class == "protocol_state"
        assert s.snapshot() == before
    # GUI attempt before any indication: rejected
    before = s.snapshot()
    with pytest.raises(UciError):
        s.feed_gui("register name A B code C")
    assert s.snapshot() == before
    # initial indication: checking -> error = unregistered
    s.feed_engine("registration checking")
    assert s.reg_phase == "reg_indication_pending"
    before = s.snapshot()
    with pytest.raises(UciError):
        s.feed_engine("registration checking")  # duplicate pending
    assert s.snapshot() == before
    s.feed_engine("registration error")
    assert s.reg_phase == "reg_unregistered"
    # register later: defers, stays unregistered, opens no attempt
    s.feed_gui("register later")
    assert s.reg_phase == "reg_unregistered"
    # a terminal without a NEW checking is still rejected
    with pytest.raises(UciError):
        s.feed_engine("registration ok")
    # GUI attempt correlates exactly one checking then one terminal
    s.feed_gui("register name Stockfish Dev code abc-123")
    assert s.reg_phase == "reg_attempt_pending"
    before = s.snapshot()
    with pytest.raises(UciError):
        s.feed_engine("registration ok")  # checking must come first
    assert s.snapshot() == before
    s.feed_engine("registration checking")
    assert s.reg_phase == "reg_attempt_checking"
    s.feed_engine("registration ok")
    assert s.reg_phase == "reg_registered"
    # registered is terminal: statuses and register both rejected
    for direction, line in (("engine", "registration checking"),
                            ("engine", "registration ok"),
                            ("engine", "registration error"),
                            ("gui", "register later"),
                            ("gui", "register name A code B")):
        before = s.snapshot()
        with pytest.raises(UciError):
            (s.feed_engine if direction == "engine"
             else s.feed_gui)(line)
        assert s.snapshot() == before
        assert s.reg_phase == "reg_registered"


def test_registration_failed_attempt_allows_retry():
    doc, s = _ready_session()
    s.feed_engine("registration checking")
    s.feed_engine("registration error")
    s.feed_gui("register name A code B")
    s.feed_engine("registration checking")
    s.feed_engine("registration error")
    assert s.reg_phase == "reg_failed"
    # after failure the engine may not self-start; a new GUI attempt
    # correlates a fresh cycle
    with pytest.raises(UciError):
        s.feed_engine("registration checking")
    s.feed_gui("register name C code D")
    assert s.reg_phase == "reg_attempt_pending"
    s.feed_engine("registration checking")
    s.feed_engine("registration ok")
    assert s.reg_phase == "reg_registered"


def test_registration_initial_ok_registers():
    doc, s = _ready_session()
    s.feed_engine("registration checking")
    s.feed_engine("registration ok")
    assert s.reg_phase == "reg_registered"
    with pytest.raises(UciError):
        s.feed_gui("register later")


def test_phases_orthogonal_to_lifecycle_readiness_debug():
    """Phase changes never touch the underlying lifecycle state, the
    position flag, the readiness flag or the debug setting - and run
    during an outstanding search exactly as in ready."""
    doc, s = _ready_session()
    s.feed_gui("debug on")
    s.feed_gui("position startpos")
    s.feed_gui("isready")
    s.feed_gui("go depth 10")
    assert s.state == "searching"
    before = s.snapshot()
    s.feed_engine("copyprotection checking")
    s.feed_engine("copyprotection ok")
    s.feed_engine("registration checking")
    s.feed_engine("registration error")
    s.feed_gui("register later")
    after = s.snapshot()
    # every component except the two phases is bit-identical
    assert after[:4] == before[:4]
    assert (s.state, s.readiness, s.debug) == ("searching", True, "on")
    assert (s.cp_phase, s.reg_phase) == ("cp_done", "reg_unregistered")
    # the search is still outstanding and completes normally
    s.feed_engine("bestmove e2e4")
    assert s.state == "ready"


def test_phases_closed_on_termination():
    doc, s = _ready_session()
    s.feed_engine("copyprotection checking")
    s.feed_engine("registration checking")
    s.feed_gui("quit")
    assert s.state == "terminated"
    for direction, line in (("engine", "copyprotection ok"),
                            ("engine", "copyprotection checking"),
                            ("engine", "registration ok"),
                            ("engine", "registration error"),
                            ("gui", "register later"),
                            ("gui", "register name A code B")):
        before = s.snapshot()
        with pytest.raises(UciError) as exc:
            (s.feed_engine if direction == "engine"
             else s.feed_gui)(line)
        assert exc.value.failure_class == "protocol_state"
        assert s.snapshot() == before


def test_phases_rejected_pre_uciok():
    doc = _doc()["contract"]
    s = Session(doc)
    s.feed_gui("uci")  # awaiting_uciok: post-uci command, pre-uciok
    for direction, line in (("engine", "copyprotection checking"),
                            ("engine", "registration checking"),
                            ("gui", "register later")):
        before = s.snapshot()
        with pytest.raises(UciError) as exc:
            (s.feed_engine if direction == "engine"
             else s.feed_gui)(line)
        assert exc.value.failure_class == "protocol_state"
        assert s.snapshot() == before


def test_lifecycle_ucinewgame_resets_position_requirement():
    doc = _doc()["contract"]
    s = Session(doc)
    s.feed_gui("uci")
    s.feed_engine("uciok")
    s.feed_gui("position startpos")
    s.feed_gui("go depth 5")
    s.feed_engine("bestmove e2e4")
    s.feed_gui("ucinewgame")
    with pytest.raises(UciError) as exc:
        s.feed_gui("go depth 5")  # no position since ucinewgame
    assert exc.value.failure_class == "protocol_state"


# -- malformed / unknown / protocol_state --------------------------------

MALFORMED_GUI = [
    "", " ", " uci", "uci ", "uci  x", "debug", "debug ON", "debug yes",
    "isready now", "setoption", "setoption name", "setoption value 4",
 "register", "register now",
    "register name OnlyName", "register name N code",
    "position", "position startpos e2e4", "position startpos moves",
    "position startpos moves e2e4 e7e5x", "position startpos moves e9e4",
    "position fen 4k3/8/8/8/8/8/8/4K3 w - - 0",  # five fen fields
    "position fen 4k3/8/8/8/8/8/8/4K3 w - - 0 1 extra",
    "position fen garbage x6 tokens here now bad",  # not a FEN at all
    "position fen 8/8/8/8/8/8/8/4K3 w - - 0 1",    # kingless
    "position fen r3k2r/8/8/8/8/8/8/4K3 w Kq - 0 1",  # right w/o rook
    "position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR "
    "w KQkq e3 0 1",                               # ep wrong side
    "position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR "
    "b KQkq e3 1 1",                               # stale ep clock
    "position fen 4k3/8/8/8/8/8/8/4K3 w - - x 1",  # nonnumeric clock
    "go wtime", "go wtime -5", "go wtime 05", "go depth 0", "go mate x",
    "go wtime ١٠٠",  # Arabic-Indic digits are not ASCII
    "go depth 5 depth 6", "go ponder ponder",
    "go searchmoves", "go searchmoves e2e4x", "go searchmoves depth",
    "go foo 1", "stop now", "quit now", "ponderhit now",
]

UNKNOWN = [
    "UCI", "IsReady", "quit()",
    "perft 5", "xboard", "new", "force", "bestmove e2e4",  # engine-side
    "uciok", "readyok", "info depth 3",
]

MALFORMED_ENGINE = [
    "id", "id name", "id rating 3000", "uciok now", "readyok ok",
    "bestmove", "bestmove e2e4 ponder",
    "bestmove e2e4 e7e5",
    "bestmove (none) ponder e2e4",  # no legal move: no predicted reply
    "bestmove (none) e2e4",
    "copyprotection", "copyprotection fine", "registration ok ok",
    "info", "info depth", "info depth -1", "info nodes -1",
    "info depth 03", "info depth x", "info foo 1",
    "info depth 5 depth 6", "info score cp", "info score elo 20",
    "info score cp +3", "info score cp 03",
    "info score cp lowerbound",  # qualifier where the value belongs
    "info score lowerbound",     # qualifier without a score value
    "info score cp 4 lowerbound upperbound",   # both qualifiers
    "info score cp 4 lowerbound lowerbound",   # duplicate qualifier
    "info score cp 4 middlebound",             # undeclared qualifier
    "info depth ٣",              # non-ASCII digit
    "info multipv 0",            # ordinal minimum is 1
    "info currmovenumber 0",     # one-based: first move is 1
    "info hashfull 1001",        # permill bound is inclusive 1000
    "info cpuload 1001",
    "info hashfull 999999999999999999999999999999999999999999999999",
    "info pv", "info pv e2e4x", "info currmove e9e4",
    "info currline", "info currline 1", "info currline 1 e9e4",
    "info string", "info time -3",
    "option", "option name", "option name X", "option name X type",
    "option name X type dial", "option name X type check",
    "option name X type check default yes",
    "option name X type spin default 1 min 1",  # missing max
    "option name X type spin default 0 min 1 max 5",  # default < min
    "option name X type spin default 6 min 1 max 5",  # default > max
    "option name X type spin default 3 min 5 max 1",  # min > max
    "option name X type combo",  # no var
    "option name X type combo default A var B",  # default not in vars
    "option name X type combo default var X",    # empty default span
    "option name X type combo var var X",        # empty var span
    "option name X type combo var X var",        # trailing empty span
    "option name X type combo default A default A var A",  # dup default
    "option name X type combo var A default A",  # default after var
    "option name X type combo default Very var Very Solid",
    # ^ default must equal one COMPLETE var string
    "option name X type button default 1",
    "option name X type string min 3",
    "option type spin name X default 1 min 1 max 2",  # name must lead
]


@pytest.mark.parametrize("bad", MALFORMED_GUI)
def test_malformed_gui_rejected(bad):
    doc = _doc()["contract"]
    with pytest.raises(UciError) as exc:
        parse_gui(doc, bad)
    assert exc.value.failure_class == "malformed_line"
    assert exc.value.code == "malformed_request"


POSITION_SUBCLASSES = [
    ("position fen garbage x6 tokens here now bad", "malformed_fen"),
    ("position fen 4k3/8/8/8/8/8/8/4K3 w - - x 1", "malformed_fen"),
    ("position fen 8/8/8/8/8/8/8/4K3 w - - 0 1", "impossible_position"),
    ("position fen r3k2r/8/8/8/8/8/8/4K3 w Kq - 0 1",
     "impossible_position"),
    ("position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR "
     "w KQkq e3 0 1", "impossible_position"),
    ("position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR "
     "b KQkq e3 1 1", "impossible_position"),
]


@pytest.mark.parametrize("bad,subclass", POSITION_SUBCLASSES)
def test_position_fen_subclass_preserved(bad, subclass):
    # position_validation: the FEN contract's own failure class is
    # preserved as the rejection subclass; both map to malformed_line.
    doc = _doc()["contract"]
    with pytest.raises(UciError) as exc:
        parse_gui(doc, bad)
    assert exc.value.failure_class == "malformed_line"
    assert exc.value.subclass == subclass


def test_startpos_resolves_to_variant_registry():
    doc = _doc()["contract"]
    assert doc["position_validation"]["startpos"] == (
        "resolves-to-linked-variant-registry-standard-start_fen")
    with open(DATA_DIR / "variant.yaml") as fh:
        registry = yaml.safe_load(fh)["contract"]["variants"]["entries"]
    standard = [e for e in registry if e["id"] == "standard"]
    assert len(standard) == 1
    # the registry start FEN is itself valid under the FEN contract
    fen_parse(_fen_contract(), standard[0]["start_fen"])


# -- byte framing: the wire layer ---------------------------------------

def test_framing_happy_and_multiframe():
    doc = _doc()["contract"]
    r = FrameReader(doc)
    assert r.feed(b"uci\n") == ["uci"]
    assert r.feed(b"isready\ngo depth 5\n") == ["isready",
                                                  "go depth 5"]
    r.finish()


def test_framing_chunk_boundaries_and_utf8_split():
    doc = _doc()["contract"]
    r = FrameReader(doc)
    assert r.feed(b"uc") == []
    assert r.feed(b"i\nid name \xc3") == ["uci"]
    assert r.feed(b"\xa9checs\n") == ["id name échecs"]
    r.finish()


FRAMING_MALFORMED = [
    b"uci\r\n",            # CRLF
    b"uc\ri\n",            # bare CR
    b"\n",                  # empty frame
    b"uci\n\n",            # empty second frame
    b"uc\xffi\n",          # invalid UTF-8
    b"id name \xc3\xa9\xc3\n",  # truncated multi-byte sequence
]


@pytest.mark.parametrize("blob", FRAMING_MALFORMED)
def test_framing_rejected(blob):
    doc = _doc()["contract"]
    r = FrameReader(doc)
    with pytest.raises(UciError) as exc:
        r.feed(blob)
    assert exc.value.failure_class == "malformed_line"
    assert exc.value.code == "malformed_request"


def test_framing_unterminated_trailing_bytes():
    doc = _doc()["contract"]
    r = FrameReader(doc)
    r.feed(b"uci")
    with pytest.raises(UciError) as exc:
        r.finish()
    assert exc.value.failure_class == "malformed_line"


def test_framing_then_grammar_layers_compose():
    doc = _doc()["contract"]
    r = FrameReader(doc)
    frames = r.feed(b"go depth 5\n")
    assert [parse_gui(doc, f) for f in frames] == [
        {"command": "go", "parameters": {"depth": 5}}]


@pytest.mark.parametrize("bad", UNKNOWN)
def test_unknown_keyword_rejected(bad):
    doc = _doc()["contract"]
    with pytest.raises(UciError) as exc:
        parse_gui(doc, bad)
    assert exc.value.failure_class == "unknown_command"
    assert exc.value.code == "unknown_command"


@pytest.mark.parametrize("bad", MALFORMED_ENGINE)
def test_malformed_engine_rejected(bad):
    doc = _doc()["contract"]
    with pytest.raises(UciError) as exc:
        parse_engine(doc, bad)
    assert exc.value.failure_class == "malformed_line"
    assert exc.value.code == "malformed_request"


def _feed(session, direction, line):
    if direction == "gui":
        return session.feed_gui(line)
    return session.feed_engine(line)


HANDSHAKE = [("gui", "uci"), ("engine", "id name S"),
             ("engine", "uciok")]
POSITIONED = HANDSHAKE + [("gui", "position startpos")]
SEARCHING = POSITIONED + [("gui", "go depth 5")]
PONDERING = POSITIONED + [("gui", "go ponder wtime 1 btime 1")]
STOPPED = SEARCHING + [("gui", "stop")]
READINESS = POSITIONED + [("gui", "isready")]
AFTER_QUIT = HANDSHAKE + [("gui", "quit")]

PROTOCOL_VIOLATIONS = [
    ([], "gui", "position startpos"),       # uci first
    ([("gui", "uci")], "gui", "isready"),   # uciok gates commands
    ([("gui", "uci")], "gui", "position startpos"),
    ([("gui", "uci")], "gui", "go depth 5"),
    ([("gui", "uci")], "gui", "setoption name X value 1"),
    ([("gui", "uci")], "engine", "readyok"),
    ([("gui", "uci")], "engine", "bestmove e2e4"),
    ([("gui", "uci")], "engine", "info depth 3"),
    (HANDSHAKE, "engine", "uciok"),         # duplicate uciok
    (HANDSHAKE, "engine", "id name X"),     # id only pre-uciok
    (HANDSHAKE, "gui", "go depth 5"),       # no position set
    (HANDSHAKE, "gui", "stop"),             # no search outstanding
    (HANDSHAKE, "gui", "ponderhit"),
    (HANDSHAKE, "gui", "uci"),              # uci twice
    (HANDSHAKE, "engine", "readyok"),       # no pending readiness
    (SEARCHING, "gui", "go depth 6"),       # second go
    (SEARCHING, "gui", "position startpos"),
    (SEARCHING, "gui", "setoption name X value 1"),
    (SEARCHING, "gui", "ucinewgame"),
    # isready during searching is LEGAL (orthogonal flag); covered by
    # test_readiness_during_normal_search.
    (SEARCHING, "gui", "ponderhit"),        # not a pondered search
    (SEARCHING, "engine", "readyok"),
    (STOPPED, "gui", "stop"),               # already stop-requested
    (STOPPED, "gui", "position startpos"),  # search still outstanding
    (STOPPED, "gui", "go depth 6"),
    (STOPPED, "gui", "ponderhit"),          # search was not pondered
    (PONDERING, "gui", "go depth 6"),
    # ponder_bestmove_gate: a pondering engine never emits bestmove
    # on its own - not on completion, not on mate; only ponderhit
    # (-> searching) or stop (-> ponder_stop_requested) releases it.
    (PONDERING, "engine", "bestmove e2e4 ponder e7e5"),
    (PONDERING + [("gui", "stop")], "gui", "stop"),
    (READINESS, "gui", "isready"),          # second isready: never queued
    (READINESS, "engine", "bestmove e2e4"),  # no search outstanding
    (SEARCHING + [("gui", "isready")], "gui", "isready"),
    # Terminal-state liveness gate: a pending readyok can never
    # bypass the empty terminated transition table (verifier #2 v3
    # finding, both independent repros).
    (POSITIONED + [("gui", "isready"), ("gui", "quit")],
     "engine", "readyok"),
    (POSITIONED + [("gui", "go infinite"), ("gui", "isready"),
                   ("gui", "quit")], "engine", "readyok"),
    ([], "gui", "debug on"),                # pinned: pre-uci reject
    ([("gui", "uci")], "gui", "debug off"),  # pinned: pre-uciok reject
    (AFTER_QUIT, "gui", "debug on"),        # nothing after quit
    (AFTER_QUIT, "gui", "isready"),         # nothing after quit
    (AFTER_QUIT, "engine", "readyok"),
    (AFTER_QUIT, "gui", "quit"),
    (AFTER_QUIT, "engine", "uciok"),
    (AFTER_QUIT, "engine", "info depth 3"),
]


@pytest.mark.parametrize("prefix,direction,final", PROTOCOL_VIOLATIONS)
def test_protocol_state_rejected(prefix, direction, final):
    doc = _doc()["contract"]
    s = Session(doc)
    for d, line in prefix:
        _feed(s, d, line)
    with pytest.raises(UciError) as exc:
        _feed(s, direction, final)
    assert exc.value.failure_class == "protocol_state"
    assert exc.value.code == "illegal_state"


def test_readiness_during_normal_search():
    # isready/readyok while calculating: the flag moves, the search
    # stays outstanding until bestmove.
    doc = _doc()["contract"]
    s = Session(doc)
    for d, line in SEARCHING:
        _feed(s, d, line)
    s.feed_gui("isready")
    assert s.snapshot() == ("searching", True, True, "off", "cp_idle", "reg_awaiting_indication")
    s.feed_engine("info depth 7")           # search unaffected
    assert s.state == "searching"
    with pytest.raises(UciError):           # still outstanding
        s.feed_gui("go depth 3")
    s.feed_engine("readyok")
    assert s.snapshot() == ("searching", True, False, "off", "cp_idle", "reg_awaiting_indication")
    with pytest.raises(UciError):           # still outstanding
        s.feed_gui("position startpos")
    s.feed_engine("bestmove e2e4")
    assert s.snapshot() == ("ready", True, False, "off", "cp_idle", "reg_awaiting_indication")


def test_readiness_during_ponder_search():
    doc = _doc()["contract"]
    s = Session(doc)
    for d, line in PONDERING:
        _feed(s, d, line)
    s.feed_gui("isready")
    assert s.snapshot() == ("pondering", True, True, "off", "cp_idle", "reg_awaiting_indication")
    s.feed_gui("ponderhit")                 # converts, flag preserved
    assert s.snapshot() == ("searching", True, True, "off", "cp_idle", "reg_awaiting_indication")
    s.feed_engine("readyok")
    assert s.snapshot() == ("searching", True, False, "off", "cp_idle", "reg_awaiting_indication")
    s.feed_engine("bestmove d2d4")
    assert s.state == "ready"


def test_readiness_after_stop_requested():
    doc = _doc()["contract"]
    s = Session(doc)
    for d, line in STOPPED:
        _feed(s, d, line)
    s.feed_gui("isready")
    assert s.snapshot() == ("stop_requested", True, True, "off", "cp_idle", "reg_awaiting_indication")
    s.feed_engine("readyok")
    assert s.snapshot() == ("stop_requested", True, False, "off", "cp_idle", "reg_awaiting_indication")
    # stop-requested survived the whole exchange: bestmove still due
    s.feed_engine("bestmove e2e4")
    assert s.state == "ready"


def test_readiness_during_ponder_stop_requested():
    doc = _doc()["contract"]
    s = Session(doc)
    for d, line in PONDERING + [("gui", "stop")]:
        _feed(s, d, line)
    assert s.state == "ponder_stop_requested"
    s.feed_gui("isready")
    assert s.snapshot() == ("ponder_stop_requested", True, True, "off", "cp_idle", "reg_awaiting_indication")
    s.feed_engine("readyok")
    assert s.snapshot() == ("ponder_stop_requested", True, False, "off", "cp_idle", "reg_awaiting_indication")
    s.feed_gui("ponderhit")
    assert s.state == "stop_requested"


def test_readyok_without_pending_rejected_everywhere():
    doc = _doc()["contract"]
    for prefix in (HANDSHAKE, SEARCHING, STOPPED, PONDERING):
        s = Session(doc)
        for d, line in prefix:
            _feed(s, d, line)
        before = s.snapshot()
        with pytest.raises(UciError) as exc:
            s.feed_engine("readyok")
        assert exc.value.failure_class == "protocol_state"
        assert s.snapshot() == before


def test_readiness_terminal_gate():
    """Liveness gate: isready/readyok after quit are protocol_state
    regardless of the flag; quit clears the flag on entry to
    terminated and no later event observes or mutates it."""
    doc = _doc()["contract"]
    for prefix in (POSITIONED,
                   POSITIONED + [("gui", "go infinite")]):
        s = Session(doc)
        for d, line in prefix:
            _feed(s, d, line)
        _feed(s, "gui", "isready")
        assert s.readiness is True  # flag pending pre-quit
        _feed(s, "gui", "quit")
        # on_termination: flag cleared on entry to terminated.
        assert s.snapshot() == (
            "terminated", s.position_flag, False, "off",
            "cp_idle", "reg_awaiting_indication")
        before = s.snapshot()
        for d, line in (("engine", "readyok"), ("gui", "isready")):
            with pytest.raises(UciError) as exc:
                _feed(s, d, line)
            assert exc.value.failure_class == "protocol_state"
            # bit-identical rollback: the rejection changes nothing.
            assert s.snapshot() == before


def test_ponder_release_paths():
    """Ponder semantics: the engine cannot exit a ponder search on
    its own - bestmove from pondering is protocol_state even when
    the search completed or found mate, with bit-identical
    rollback. Only ponderhit (-> searching) or stop (->
    ponder_stop_requested) releases bestmove."""
    doc = _doc()["contract"]
    # negative: direct ponder -> bestmove (the verifier repro),
    # including the mate/completed-search wording of the gate.
    s = Session(doc)
    for d, line in PONDERING:
        _feed(s, d, line)
    _feed(s, "engine", "info depth 20 score mate 3")
    before = s.snapshot()
    with pytest.raises(UciError) as exc:
        s.feed_engine("bestmove e2e4 ponder e7e5")
    assert exc.value.failure_class == "protocol_state"
    assert s.snapshot() == before  # bit-identical rollback
    # release via ponderhit: bestmove completes normally.
    _feed(s, "gui", "ponderhit")
    assert s.state == "searching"
    _feed(s, "engine", "bestmove e2e4 ponder e7e5")
    assert s.state == "ready"
    # release via stop: bestmove completes the stopped search.
    s = Session(doc)
    for d, line in PONDERING:
        _feed(s, d, line)
    _feed(s, "gui", "stop")
    assert s.state == "ponder_stop_requested"
    _feed(s, "engine", "bestmove e7e5")
    assert s.state == "ready"
    # info flows freely while pondering (unchanged).
    s = Session(doc)
    for d, line in PONDERING:
        _feed(s, d, line)
    _feed(s, "engine", "info depth 1")
    assert s.state == "pondering"


def test_debug_orthogonal_setting():
    """debug on/off is accepted in every live post-uciok state -
    including while the engine is thinking - and changes ONLY the
    setting: underlying state, position flag, outstanding search
    and readiness flag are preserved, and info/stop/ponderhit/
    readyok/bestmove continue per the unchanged state."""
    doc = _doc()["contract"]

    def toggles(s, base):
        assert s.debug == "off"
        _feed(s, "gui", "debug on")
        assert s.debug == "on"
        assert s.snapshot()[:3] == base
        _feed(s, "gui", "debug off")
        assert s.debug == "off"
        assert s.snapshot()[:3] == base

    # ready
    s = Session(doc)
    for d, line in POSITIONED:
        _feed(s, d, line)
    toggles(s, ("ready", True, False))
    # normal search: debug mid-calculation, search stays outstanding
    s = Session(doc)
    for d, line in SEARCHING:
        _feed(s, d, line)
    toggles(s, ("searching", True, False))
    _feed(s, "engine", "info depth 3")  # info still flows
    _feed(s, "gui", "debug on")
    _feed(s, "engine", "bestmove e2e4")  # bestmove ends the search
    assert s.snapshot() == ("ready", True, False, "on", "cp_idle", "reg_awaiting_indication")
    # ponder search: debug, then ponderhit still converts
    s = Session(doc)
    for d, line in PONDERING:
        _feed(s, d, line)
    toggles(s, ("pondering", True, False))
    _feed(s, "gui", "debug on")
    _feed(s, "gui", "ponderhit")
    assert s.snapshot() == ("searching", True, False, "on", "cp_idle", "reg_awaiting_indication")
    # stop-requested: debug, search outstanding until bestmove
    s = Session(doc)
    for d, line in SEARCHING + [("gui", "stop")]:
        _feed(s, d, line)
    toggles(s, ("stop_requested", True, False))
    _feed(s, "engine", "bestmove e2e4")
    assert s.snapshot() == ("ready", True, False, "off", "cp_idle", "reg_awaiting_indication")
    # readiness-pending search: debug touches neither flag nor search
    s = Session(doc)
    for d, line in SEARCHING + [("gui", "isready")]:
        _feed(s, d, line)
    toggles(s, ("searching", True, True))
    _feed(s, "engine", "readyok")  # flag still pending, clears it
    assert s.snapshot() == ("searching", True, False, "off", "cp_idle", "reg_awaiting_indication")
    _feed(s, "engine", "bestmove e2e4")
    assert s.snapshot() == ("ready", True, False, "off", "cp_idle", "reg_awaiting_indication")


def test_stop_requests_but_never_clears_search():
    # stop marks the search stop-requested; ONLY bestmove clears it.
    doc = _doc()["contract"]
    s = Session(doc)
    for d, line in STOPPED:
        _feed(s, d, line)
    assert s.state == "stop_requested"
    s.feed_engine("info depth 9")   # still searching: info valid
    assert s.state == "stop_requested"
    s.feed_engine("bestmove e2e4")
    assert s.state == "ready"


# -- rollback: rejection changes NO state -------------------------------

def test_rejected_commands_leave_state_bit_identical():
    doc = _doc()["contract"]
    scripts = [
        (SEARCHING,
         [("gui", "go depth 6"), ("gui", "stop stop"),
          ("gui", "ponderhit"), ("gui", "position startpos"),
          ("gui", "setoption name X value 1"),
          ("engine", "readyok"), ("engine", "uciok"),
          ("engine", "bestmove"), ("gui", "go")]),
        (STOPPED,
         [("gui", "stop"), ("gui", "go depth 6"),
          ("gui", "ponderhit"), ("engine", "readyok")]),
        ([("gui", "uci")],
         [("gui", "go infinite"), ("gui", "stop"),
          ("engine", "readyok"), ("gui", "position"),
          ("gui", "setoption name"), ("engine", "bestmove e2e4")]),
        (AFTER_QUIT,
         [("gui", "isready"), ("gui", "quit"), ("gui", "debug on"),
          ("engine", "uciok"), ("engine", "bestmove e2e4")]),
        ([("gui", "uci")],
         [("gui", "debug on"), ("gui", "isready")]),
        (POSITIONED + [("gui", "isready"), ("gui", "quit")],
         [("engine", "readyok"), ("gui", "isready"),
          ("engine", "bestmove e2e4")]),
        (POSITIONED + [("gui", "go infinite"), ("gui", "isready"),
                       ("gui", "quit")],
         [("engine", "readyok")]),
        (PONDERING,
         [("engine", "bestmove e2e4 ponder e7e5"),
          ("engine", "bestmove (none)")]),
    ]
    for prefix, rejections in scripts:
        s = Session(doc)
        for d, line in prefix:
            _feed(s, d, line)
        before = s.snapshot()
        for d, bad in rejections:
            with contextlib.suppress(UciError):
                _feed(s, d, bad)
            assert s.snapshot() == before, (
                f"{bad!r} changed state {before} -> {s.snapshot()}")


# -- mutation battery ----------------------------------------------------

def _mutants():
    doc = _doc()
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    add("framing input drift", ["contract", "transport",
                                "byte_framing", "input"], "text-stream")
    add("crlf tolerated", ["contract", "transport", "byte_framing",
                           "crlf"], "tolerated")
    add("bare cr tolerated", ["contract", "transport", "byte_framing",
                              "bare_cr"], "tolerated")
    add("unterminated tolerated", ["contract", "transport",
                                   "byte_framing",
                                   "unterminated_trailing_bytes"],
        "tolerated")
    add("invalid utf8 tolerated", ["contract", "transport",
                                   "byte_framing", "invalid_utf8"],
        "replacement-char")
    add("empty frame tolerated", ["contract", "transport",
                                  "byte_framing", "empty_frame"],
        "skipped")
    add("chunking drift", ["contract", "transport", "byte_framing",
                           "chunking"], "frames-align-with-reads")
    add("separator drift", ["contract", "transport", "line_grammar",
                            "token_separator"], "any-whitespace")
    add("keyword case folding", ["contract", "transport",
                                 "line_grammar", "keyword_case"],
        "case-insensitive")
    add("move form drift", ["contract", "move_encoding", "form"],
        "san")
    add("promotion uppercase", ["contract", "move_encoding",
                                "promotion_letter_case"], "uppercase")
    add("promotion enum drift", ["contract", "move_encoding",
                                 "promotion_letters"], ["q", "r", "b"])
    add("none token drift", ["contract", "move_encoding", "none_token"],
        "0000")
    add("command dropped", ["contract", "gui_commands", "stop"],
        "keyword-then-optional-move")
    add("command rogue", ["contract", "gui_commands", "perft"],
        "keyword-then-depth")
    add("go param kind drift", ["contract", "go_parameters", "depth"],
        {"kind": "nonneg-int"})
    add("go param dropped", ["contract", "go_parameters", "ponder"],
        None)
    add("go duplicates allowed", ["contract", "go_parameters",
                                  "duplicates"], "allowed")
    add("go int grammar drift", ["contract", "go_parameters",
                                 "int_grammar"], "any-digits")
    add("response dropped", ["contract", "engine_responses", "readyok"],
        "bare-keyword-with-suffix")
    add("ponder laundered to independent syntax", ["contract",
                                                   "bestmove_semantics",
                                                   "ponder_condition"],
        "independent-optional-syntax")
    add("none tail drift", ["contract", "bestmove_semantics",
                            "none_tail"], "ponder-allowed")
    add("info field kind drift", ["contract", "info_fields", "score"],
        {"kind": "score", "forms": ["cp"], "value_kind": "int"})
    add("info counter pos-int drift", ["contract", "info_fields",
                                       "depth"], {"kind": "pos-int"})
    add("multipv laundered to nonneg", ["contract", "info_fields",
                                        "multipv"],
        {"kind": "nonneg-int"})
    add("multipv min drift", ["contract", "info_fields", "multipv",
                              "min"], 0)
    add("currmovenumber laundered", ["contract", "info_fields",
                                     "currmovenumber"],
        {"kind": "nonneg-int"})
    add("currmovenumber min drift", ["contract", "info_fields",
                                     "currmovenumber", "min"], 0)
    add("hashfull laundered", ["contract", "info_fields", "hashfull"],
        {"kind": "nonneg-int"})
    add("hashfull max drift", ["contract", "info_fields", "hashfull",
                               "max"], 1001)
    add("hashfull min drift", ["contract", "info_fields", "hashfull",
                               "min"], 1)
    add("cpuload laundered", ["contract", "info_fields", "cpuload"],
        {"kind": "nonneg-int"})
    add("cpuload max drift", ["contract", "info_fields", "cpuload",
                              "max"], 10000)
    add("score bound dropped", ["contract", "info_fields", "score",
                                "bound"], None)
    add("score bound values drift", ["contract", "info_fields",
                                     "score", "bound", "values"],
        ["lowerbound"])
    add("info duplicates allowed", ["contract", "info_fields",
                                    "duplicates"], "allowed")
    add("option type dropped", ["contract", "option_types", "spin"],
        None)
    add("spin bounds dropped", ["contract", "option_types", "spin",
                                "bounds"], "none")
    add("combo default free", ["contract", "option_types", "combo",
                               "default_membership"], "any-token")
    add("combo value kind drift", ["contract", "option_types", "combo",
                                   "value_kind"], "single-token")
    add("option marker drift", ["contract", "option_markers",
                                "name_terminator"], "first-type-token")
    add("combo span drift", ["contract", "option_markers",
                             "combo_var_span"], "single-token")
    add("position validation drift", ["contract", "position_validation",
                                      "fen_fields"], "token-count-only")
    add("fen impossible remap", ["contract", "position_validation",
                                 "fen_impossible_maps_to"],
        "protocol_state")
    add("subclass dropped", ["contract", "position_validation",
                             "subclass_preservation"], "dropped")
    add("startpos resolution drift", ["contract", "position_validation",
                                      "startpos"], "hardcoded-fen")
    add("lifecycle model drift", ["contract", "lifecycle", "model"],
        "gui-command-only")
    add("lifecycle initial drift", ["contract", "lifecycle", "initial"],
        "ready")
    add("lifecycle states drift", ["contract", "lifecycle", "states"],
        ["pre_uci", "ready", "terminated"])
    add("stop clears search", ["contract", "lifecycle", "transitions",
                               "searching", "gui", "stop"], "ready")
    add("bestmove does not clear", ["contract", "lifecycle",
                                    "transitions", "stop_requested",
                                    "engine", "bestmove"],
        "stop_requested")
    add("uciok gate removed", ["contract", "lifecycle", "transitions",
                               "awaiting_uciok", "gui"],
        {"position": "ready", "quit": "terminated"})
    add("readiness model drift", ["contract", "lifecycle", "readiness",
                                  "model"], "top-level-state")
    add("debug model drift", ["contract", "lifecycle", "debug",
                              "model"], "lifecycle-state")
    add("debug acceptance drops searching", ["contract", "lifecycle",
                                             "debug",
                                             "accepted_in_states"],
        ["ready", "pondering", "stop_requested",
         "ponder_stop_requested"])
    add("debug acceptance drops pondering", ["contract", "lifecycle",
                                             "debug",
                                             "accepted_in_states"],
        ["ready", "searching", "stop_requested",
         "ponder_stop_requested"])
    add("debug acceptance drops stop_requested", ["contract",
                                                  "lifecycle",
                                                  "debug",
                                                  "accepted_in_states"],
        ["ready", "searching", "pondering", "ponder_stop_requested"])
    add("debug acceptance drops ponder_stop", ["contract", "lifecycle",
                                               "debug",
                                               "accepted_in_states"],
        ["ready", "searching", "pondering", "stop_requested"])
    add("debug pre-uciok allowed", ["contract", "lifecycle", "debug",
                                    "pre_uciok"], "accepted")
    add("debug returns search to ready", ["contract", "lifecycle",
                                          "transitions", "searching",
                                          "gui", "debug"], "ready")
    add("debug clears readiness", ["contract", "lifecycle", "debug",
                                   "state_preservation"],
        "debug-clears-readiness-flag")
    add("second isready queued", ["contract", "lifecycle", "readiness",
                                  "second_isready"], "queued")
    add("isready from awaiting", ["contract", "lifecycle", "readiness",
                                  "set_from_states"],
        ["awaiting_uciok", "ready", "searching", "pondering",
         "stop_requested", "ponder_stop_requested"])
    add("readiness cleared by stop", ["contract", "lifecycle",
                                      "readiness", "cleared_by"],
        "readyok-or-stop")
    add("readiness cleared by readyok only", ["contract", "lifecycle",
                                              "readiness",
                                              "cleared_by"],
        "readyok-only")
    add("liveness gate drift", ["contract", "lifecycle", "readiness",
                                "liveness_gate"],
        "terminated-ignores-readiness")
    add("termination keeps flag", ["contract", "lifecycle",
                                   "readiness", "on_termination"],
        "flag-preserved")
    add("post-termination flag observable", ["contract", "lifecycle",
                                             "readiness",
                                             "post_termination_observability"],
        "flag-readable-after-quit")
    add("isready from terminated", ["contract", "lifecycle",
                                    "readiness", "set_from_states"],
        ["ready", "searching", "pondering", "stop_requested",
         "ponder_stop_requested", "terminated"])
    add("flag touches state", ["contract", "lifecycle", "readiness",
                               "state_preservation"],
        "readyok-returns-to-ready")
    add("isready in state table", ["contract", "lifecycle",
                                   "transitions", "ready", "gui",
                                   "isready"], "ready")
    add("readiness_pending state back", ["contract", "lifecycle",
                                         "states"],
        ["pre_uci", "awaiting_uciok", "ready", "readiness_pending",
         "searching", "pondering", "stop_requested",
         "ponder_stop_requested", "terminated"])
    add("ponder bestmove direct", ["contract", "lifecycle",
                                   "transitions", "pondering",
                                   "engine", "bestmove"], "ready")
    add("searching bestmove dropped", ["contract", "lifecycle",
                                       "transitions", "searching",
                                       "engine"], {"info": "searching"})
    add("stop_requested bestmove dropped", ["contract", "lifecycle",
                                            "transitions",
                                            "stop_requested",
                                            "engine"],
        {"info": "stop_requested"})
    add("ponder_stop bestmove dropped", ["contract", "lifecycle",
                                         "transitions",
                                         "ponder_stop_requested",
                                         "engine"],
        {"info": "ponder_stop_requested"})
    add("ponder gate drift", ["contract", "lifecycle",
                              "ponder_bestmove_gate"],
        "engine-may-exit-ponder-freely")
    add("ponderhit free", ["contract", "lifecycle", "transitions",
                           "searching", "gui"],
        {"stop": "stop_requested", "ponderhit": "searching",
         "quit": "terminated"})
    add("info in ready", ["contract", "lifecycle", "transitions",
                          "ready", "engine"],
        {"info": "ready", "copyprotection": "ready",
         "registration": "ready"})
    add("copyprotection terminal without checking",
        ["contract", "lifecycle", "copyprotection", "transitions",
         "cp_idle"],
        {"checking": "cp_checking", "ok": "cp_done",
         "error": "cp_done"})
    add("copyprotection completion reopens",
        ["contract", "lifecycle", "copyprotection", "transitions",
         "cp_done"],
        {"checking": "cp_checking", "ok": "cp_done",
         "error": "cp_done"})
    add("registration unsolicited success",
        ["contract", "lifecycle", "registration",
         "engine_transitions", "reg_awaiting_indication"],
        {"checking": "reg_indication_pending", "ok": "reg_registered",
         "error": "reg_unregistered"})
    add("registration terminal without correlated checking",
        ["contract", "lifecycle", "registration",
         "engine_transitions", "reg_attempt_pending"],
        {"checking": "reg_attempt_checking", "ok": "reg_registered",
         "error": "reg_failed"})
    add("registration completion reopens",
        ["contract", "lifecycle", "registration",
         "engine_transitions", "reg_registered"],
        {"checking": "reg_indication_pending",
         "ok": "reg_registered", "error": "rejected-protocol_state"})
    add("register accepted anywhere",
        ["contract", "lifecycle", "registration",
         "gui_register_transitions", "name_code"],
        {"reg_unregistered": "reg_attempt_pending",
         "reg_failed": "reg_attempt_pending",
         "elsewhere": "reg_attempt_pending"})
    add("post quit commands", ["contract", "lifecycle", "transitions",
                               "terminated", "gui"], {"uci": "ready"})
    add("unlisted pair drift", ["contract", "lifecycle",
                                "unlisted_pair"], "ignored")
    add("transition target invented", ["contract", "lifecycle",
                                       "transitions", "searching",
                                       "engine", "bestmove"],
        "done")
    add("go ponder target drift", ["contract", "lifecycle",
                                   "go_ponder_target"], "always-normal")
    add("variant link dropped", ["contract", "links",
                                 "variant_contract"], None)
    add("failure class dropped", ["contract", "resolution_failures",
                                  "protocol_state"], "none")
    add("failure classes drift", ["contract", "failure_classes"],
        ["malformed_line", "unknown_command"])
    add("failure mapping drift", ["contract", "failure_mapping",
                                  "protocol_state", "error"],
        "malformed_request")
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "unknown_command", "internal"])
    add("serialization off", ["contract", "serialization", "canonical"],
        False)
    add("roundtrip drift", ["contract", "serialization", "roundtrip"],
        "best-effort")
    add("fen link drift", ["contract", "links", "fen_contract"],
        "data/contracts/san.yaml")
    add("base path drift", ["contract", "versioning", "base_path"],
        "/uci/v0")
    return out


def test_phase_flattening_mutants_launder_violations():
    """If either phase variable is flattened back to self-loops IN
    MEMORY, the violating traces the real contract rejects become
    accepted - proving the phase tables, not prose, carry the
    ordering constraint (the linter separately rejects these
    mutations in the FILE)."""
    doc = _doc()["contract"]

    cp_flat = copy.deepcopy(doc)
    for phase in cp_flat["lifecycle"]["copyprotection"]["transitions"]:
        cp_flat["lifecycle"]["copyprotection"]["transitions"][phase] = \
            {"checking": phase, "ok": phase, "error": phase}
    s = Session(cp_flat)
    s.feed_gui("uci")
    s.feed_engine("uciok")
    s.feed_engine("copyprotection ok")        # no checking: laundered
    s.feed_engine("copyprotection ok")        # repeat: laundered
    s.feed_engine("copyprotection error")     # contradict: laundered

    reg_flat = copy.deepcopy(doc)
    for phase in reg_flat["lifecycle"]["registration"][
            "engine_transitions"]:
        reg_flat["lifecycle"]["registration"][
            "engine_transitions"][phase] = \
            {"checking": phase, "ok": phase, "error": phase}
    s = Session(reg_flat)
    s.feed_gui("uci")
    s.feed_engine("uciok")
    s.feed_engine("registration ok")          # unsolicited: laundered
    s.feed_engine("registration checking")
    s.feed_engine("registration checking")    # duplicate: laundered
    s.feed_engine("registration error")
    s.feed_engine("registration ok")          # after error: laundered

    # the REAL contract rejects every one of those (phase tables
    # carry the constraint)
    s = Session(doc)
    s.feed_gui("uci")
    s.feed_engine("uciok")
    for line in ("copyprotection ok", "registration ok"):
        with pytest.raises(UciError):
            s.feed_engine(line)


def test_mutations_fail_lint():
    mutants = _mutants()
    assert len(mutants) >= 30
    for name, m in mutants:
        try:
            lint(m)
        except ContractError:
            continue
        raise AssertionError(f"mutant {name!r} passed the lint")


def test_mutants_never_silent_subset():
    names = {name for name, _ in _mutants()}
    assert len(names) == len(_mutants())


def _lint_with_root(tmp_path, mutate=None):
    dst = tmp_path / "data" / "contracts"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DATA_DIR, dst)
    if mutate:
        mutate(dst)
    doc = yaml.safe_load((dst / "uci.yaml").read_text())
    return doc, dst


def test_linkage_clean_copy_passes(tmp_path):
    doc, dst = _lint_with_root(tmp_path)
    lint(doc, root=tmp_path)


def test_linkage_legal_moves_square_grammar_drift_fails(tmp_path):
    def mutate(dst):
        p = dst / "legal_moves.yaml"
        d = yaml.safe_load(p.read_text())
        d["contract"]["move_model"]["square_grammar"]["files"] = \
            ["a", "b", "c", "d", "e", "f", "g"]
        p.write_text(yaml.safe_dump(d))
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)


def test_linkage_legal_moves_promotion_enum_drift_fails(tmp_path):
    def mutate(dst):
        p = dst / "legal_moves.yaml"
        d = yaml.safe_load(p.read_text())
        d["contract"]["move_model"]["shape"]["types"]["promotion"][
            "enum"] = ["q", "r", "b"]
        p.write_text(yaml.safe_dump(d))
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)


def test_linkage_fen_field_order_drift_fails(tmp_path):
    def mutate(dst):
        p = dst / "fen.yaml"
        d = yaml.safe_load(p.read_text())
        d["contract"]["fields"]["order"] = [
            "placement", "castling", "active_color", "en_passant",
            "halfmove_clock", "fullmove_number"]
        p.write_text(yaml.safe_dump(d))
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)


def test_linkage_fen_semantic_rule_drift_fails(tmp_path):
    # a SEMANTIC rule change, not only grammar/field-order
    def mutate(dst):
        p = dst / "fen.yaml"
        d = yaml.safe_load(p.read_text())
        d["contract"]["position_rules"]["white_kings"] = "at-most-1"
        p.write_text(yaml.safe_dump(d))
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)


def test_linkage_fen_ep_storage_drift_fails(tmp_path):
    def mutate(dst):
        p = dst / "fen.yaml"
        d = yaml.safe_load(p.read_text())
        d["contract"]["en_passant"]["storage"] = \
            "recorded-only-when-capturable"
        p.write_text(yaml.safe_dump(d))
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)


def test_linkage_variant_start_fen_drift_fails(tmp_path):
    def mutate(dst):
        p = dst / "variant.yaml"
        d = yaml.safe_load(p.read_text())
        d["contract"]["variants"]["entries"][0]["start_fen"] = \
            "8/8/8/8/8/8/8/8 w - - 0 1"
        p.write_text(yaml.safe_dump(d))
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)


def test_linkage_missing_sibling_fails(tmp_path):
    def mutate(dst):
        (dst / "fen.yaml").unlink()
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)
