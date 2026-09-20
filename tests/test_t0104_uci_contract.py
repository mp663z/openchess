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
def _fen_field_count():
    path = DATA_DIR / "fen.yaml"
    with open(path) as fh:
        fields = yaml.safe_load(fh)["contract"]["fields"]
    assert fields["exactly_six"] is True
    return len(fields["order"])


class UciError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(contract, failure_class):
    raise UciError(failure_class,
                   contract["failure_mapping"][failure_class]["error"])


# -- executable reference parser/emitter, contract-derived ------------

def _check_line(contract, line):
    t = contract["transport"]
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


def _int_kind(contract, token, kind, section):
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
        if not rest or (rest[0] != none and not _is_move(c, rest[0])):
            _fail(c, "malformed_line")
        out = {"response": "bestmove", "move": rest[0]}
        if len(rest) > 1:
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
            if kind in ("pos-int", "nonneg-int"):
                if i + 1 >= len(rest):
                    _fail(c, "malformed_line")
                value = _int_kind(c, rest[i + 1], kind,
                                  c["info_fields"])
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
                out[key] = {rest[i + 1]: int(raw)}
                i += 3
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
        if "name" not in rest or "type" not in rest:
            _fail(c, "malformed_line")
        if rest[0] != "name":
            _fail(c, "malformed_line")
        ti = rest.index("type")
        name = rest[1:ti]
        if not name or ti + 1 >= len(rest):
            _fail(c, "malformed_line")
        otype = rest[ti + 1]
        types = c["option_types"]
        if otype not in types:
            _fail(c, "malformed_line")
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
            varz = []
            default = None
            j = 0
            while j < len(tail):
                if tail[j] == "var" and j + 1 < len(tail):
                    varz.append(tail[j + 1])
                    j += 2
                elif tail[j] == "default" and j + 1 < len(tail):
                    if default is not None:
                        _fail(c, "malformed_line")
                    default = tail[j + 1]
                    j += 2
                else:
                    _fail(c, "malformed_line")
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
                    form, n = next(iter(value.items()))
                    parts.append(f"{key} {form} {n}")
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
    """The contract lifecycle as an executable tracker. A rejected
    command changes NO state."""

    def __init__(self, contract):
        self.c = contract
        self.state = {"uci": False, "position": False, "search": False,
                      "ponder_search": False, "quit": False}

    def feed(self, line):
        c = self.c
        life = c["lifecycle"]
        assert life["first_command"] == "uci"
        cmd = parse_gui(c, line)  # malformed/unknown raise first
        s = self.state
        kw = cmd["command"]
        if s["quit"]:
            _fail(c, "protocol_state")
        if kw == "uci":
            if s["uci"]:
                _fail(c, "protocol_state")
            s["uci"] = True
            s["position"] = False
        elif not s["uci"]:
            _fail(c, "protocol_state")
        elif kw == "ucinewgame":
            s["position"] = False
        elif kw == "position":
            s["position"] = True
        elif kw == "go":
            if not s["position"] or s["search"]:
                _fail(c, "protocol_state")
            s["search"] = True
            s["ponder_search"] = cmd["parameters"].get("ponder") is True
        elif kw == "stop":
            if not s["search"]:
                _fail(c, "protocol_state")
            s["search"] = s["ponder_search"] = False
        elif kw == "ponderhit":
            if not s["ponder_search"]:
                _fail(c, "protocol_state")
            s["ponder_search"] = False
        elif kw == "quit":
            s["quit"] = True
        # debug/isready/setoption/register: valid once uci was sent
        return cmd


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
      "fields": {"depth": 12, "score": {"cp": 35}}}),
    ("info depth 12 score cp -17",
     {"response": "info",
      "fields": {"depth": 12, "score": {"cp": -17}}}),
    ("info depth 25 score mate 4",
     {"response": "info",
      "fields": {"depth": 25, "score": {"mate": 4}}}),
    ("info depth 25 score mate -2",
     {"response": "info",
      "fields": {"depth": 25, "score": {"mate": -2}}}),
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
                                     "score": {"cp": 20},
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

def test_lifecycle_full_session():
    doc = _doc()["contract"]
    s = Session(doc)
    for line in ("uci", "setoption name Threads value 4", "isready",
                 "ucinewgame", "position startpos", "go depth 10",
                 "stop", "position startpos moves e2e4",
                 "go ponder wtime 1000 btime 1000", "ponderhit", "stop",
                 "quit"):
        s.feed(line)
    assert s.state == {"uci": True, "position": True, "search": False,
                       "ponder_search": False, "quit": True}


def test_lifecycle_ucinewgame_resets_position_requirement():
    doc = _doc()["contract"]
    s = Session(doc)
    for line in ("uci", "position startpos", "go depth 5", "stop",
                 "ucinewgame"):
        s.feed(line)
    with pytest.raises(UciError) as exc:
        s.feed("go depth 5")  # no position since ucinewgame
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
    "copyprotection", "copyprotection fine", "registration ok ok",
    "info", "info depth", "info depth -1", "info depth 0",
    "info depth 03", "info depth x", "info foo 1",
    "info depth 5 depth 6", "info score cp", "info score elo 20",
    "info score cp +3", "info score cp 03",
    "info pv", "info pv e2e4x", "info currmove e9e4",
    "info currline", "info currline 1", "info currline 1 e9e4",
    "info string", "info multipv 0", "info time -3",
    "option", "option name", "option name X", "option name X type",
    "option name X type dial", "option name X type check",
    "option name X type check default yes",
    "option name X type spin default 1 min 1",  # missing max
    "option name X type spin default 0 min 1 max 5",  # default < min
    "option name X type spin default 6 min 1 max 5",  # default > max
    "option name X type spin default 3 min 5 max 1",  # min > max
    "option name X type combo",  # no var
    "option name X type combo default A var B",  # default not in vars
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


PROTOCOL_VIOLATIONS = [
    ([], "position startpos"),                      # uci not first
    (["uci"], "go depth 5"),                        # no position set
    (["uci", "position startpos", "go depth 5"], "go depth 5"),
    (["uci"], "stop"),                              # no search
    (["uci", "position startpos", "go depth 5", "stop"], "stop"),
    (["uci", "position startpos", "go depth 5"], "ponderhit"),
    (["uci", "position startpos", "go depth 5", "stop"], "ponderhit"),
    (["uci", "quit"], "isready"),                   # after quit
    (["uci", "quit"], "quit"),
    (["uci", "uci"], None),                         # uci twice
]


@pytest.mark.parametrize("prefix,final", [
    (p, f) for p, f in PROTOCOL_VIOLATIONS if f is not None])
def test_protocol_state_rejected(prefix, final):
    doc = _doc()["contract"]
    s = Session(doc)
    for line in prefix:
        s.feed(line)
    with pytest.raises(UciError) as exc:
        s.feed(final)
    assert exc.value.failure_class == "protocol_state"
    assert exc.value.code == "illegal_state"


def test_protocol_uci_twice_rejected():
    doc = _doc()["contract"]
    s = Session(doc)
    s.feed("uci")
    with pytest.raises(UciError) as exc:
        s.feed("uci")
    assert exc.value.failure_class == "protocol_state"


# -- rollback: rejection changes NO state -------------------------------

def test_rejected_commands_leave_state_bit_identical():
    doc = _doc()["contract"]
    scripts = [
        (["uci", "position startpos", "go depth 5"],
         ["go depth 6", "stop stop", "ponderhit", "go",
          "position startpos moves e2e4x", "quit quit"]),
        (["uci"], ["go infinite", "stop", "ponderhit", "position",
                   "setoption name", "register name N code"]),
        (["uci", "quit"], ["isready", "quit", "position startpos"]),
    ]
    for prefix, rejections in scripts:
        s = Session(doc)
        for line in prefix:
            s.feed(line)
        before = copy.deepcopy(s.state)
        for bad in rejections:
            with contextlib.suppress(UciError):
                s.feed(bad)
            assert s.state == before, (
                f"{bad!r} changed state {before} -> {s.state}")


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

    add("framing drift", ["contract", "transport", "framing"],
        "json-rpc")
    add("terminator drift", ["contract", "transport",
                             "line_terminator"], "CRLF")
    add("separator drift", ["contract", "transport", "token_separator"],
        "any-whitespace")
    add("keyword case folding", ["contract", "transport",
                                 "keyword_case"], "case-insensitive")
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
    add("info field kind drift", ["contract", "info_fields", "score"],
        {"kind": "score", "forms": ["cp"], "value_kind": "int"})
    add("info duplicates allowed", ["contract", "info_fields",
                                    "duplicates"], "allowed")
    add("option type dropped", ["contract", "option_types", "spin"],
        None)
    add("spin bounds dropped", ["contract", "option_types", "spin",
                                "bounds"], "none")
    add("combo default free", ["contract", "option_types", "combo",
                               "default_membership"], "any-token")
    add("lifecycle first drift", ["contract", "lifecycle",
                                  "first_command"], "isready")
    add("lifecycle go drift", ["contract", "lifecycle", "go_requires"],
        "nothing")
    add("lifecycle stop drift", ["contract", "lifecycle",
                                 "stop_requires"], "nothing")
    add("lifecycle after_quit drift", ["contract", "lifecycle",
                                       "after_quit"], "commands-allowed")
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


def test_linkage_missing_sibling_fails(tmp_path):
    def mutate(dst):
        (dst / "fen.yaml").unlink()
    doc, dst = _lint_with_root(tmp_path, mutate)
    with pytest.raises(ContractError):
        lint(doc, root=tmp_path)
