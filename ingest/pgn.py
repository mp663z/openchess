"""Strict PGN game validation for the shipped import path.

Every game record the product stores must be REAL chess: this module is
the fail-closed gate. It parses a single PGN game (tag block + movetext)
and validates:

- tag syntax: each tag line is exactly [Name "Value"] with PGN string
  escaping; an unclosed tag is malformed, never repaired.
- movetext structure: balanced comments/variations, NAGs, move numbers,
  and a terminal result token; a truncated movetext is malformed.
- SAN legality: every mainline token is parsed as Standard-notation SAN
  and matched against full legal move generation (pins, check evasion,
  castling-through-check, en passant, promotion); check/mate suffixes
  are verified, not trusted. A token that is not legal chess is rejected
  as illegal, never stored.

No chess-library dependency (licensing: permissive deps only); the move
core below is self-contained Standard chess.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

FILES = "abcdefgh"
INITIAL_FEN_BOARD = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"


class MalformedPGN(ValueError):
    """Bytes/structure not well-formed PGN (contract: malformed_input)."""


class IllegalMove(ValueError):
    """Well-formed SAN that violates the game rules (contract: illegal_movetext)."""


@dataclass(frozen=True)
class Move:
    frm: int
    to: int
    piece: str = ""          # moved piece letter, filled by generation
    capture: bool = False
    promotion: str = ""      # 'Q' | 'R' | 'B' | 'N'
    castle: str = ""         # 'K' | 'Q'
    en_passant: bool = False
    gives_check: bool = False
    gives_mate: bool = False


def _sq(file_: int, rank: int) -> int:
    return rank * 8 + file_


def _name(idx: int) -> str:
    return FILES[idx % 8] + str(idx // 8 + 1)


@dataclass
class Board:
    squares: list[str] = field(default_factory=list)  # 64, '' empty, else e.g. 'P'/'q'
    turn: str = "w"
    castling: str = "KQkq"
    ep: int = -1           # en-passant target square index or -1

    @classmethod
    def initial(cls) -> Board:
        b = cls(squares=[""] * 64)
        for ri, rank_str in enumerate(INITIAL_FEN_BOARD.split("/")):
            rank = 7 - ri
            f = 0
            for ch in rank_str:
                if ch.isdigit():
                    f += int(ch)
                else:
                    b.squares[_sq(f, rank)] = ch
                    f += 1
        return b

    def king_square(self, side: str) -> int:
        k = "K" if side == "w" else "k"
        return self.squares.index(k)

    def attacked(self, target: int, by_side: str) -> bool:
        """Is target square attacked by by_side?"""
        tf, tr = target % 8, target // 8
        pawns = ("P",) if by_side == "w" else ("p",)
        # pawn attacks: a by_side pawn sits 'behind' the target
        pawn_rank = tr - 1 if by_side == "w" else tr + 1
        if 0 <= pawn_rank < 8:
            for df in (-1, 1):
                f = tf + df
                if 0 <= f < 8 and self.squares[_sq(f, pawn_rank)] in pawns:
                    return True
        for df, dr in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
            f, r = tf + df, tr + dr
            if (0 <= f < 8 and 0 <= r < 8
                    and self.squares[_sq(f, r)] == ("N" if by_side == "w" else "n")):
                return True
        for df, dr, attackers in (
            (1, 0, "RQ"), (-1, 0, "RQ"), (0, 1, "RQ"), (0, -1, "RQ"),
            (1, 1, "BQ"), (1, -1, "BQ"), (-1, 1, "BQ"), (-1, -1, "BQ"),
        ):
            f, r = tf + df, tr + dr
            while 0 <= f < 8 and 0 <= r < 8:
                p = self.squares[_sq(f, r)]
                if p:
                    if by_side == "w" and p in attackers and p.isupper():
                        return True
                    if by_side == "b" and p in attackers.lower() and p.islower():
                        return True
                    break
                f += df
                r += dr
        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                if not df and not dr:
                    continue
                f, r = tf + df, tr + dr
                if (0 <= f < 8 and 0 <= r < 8
                        and self.squares[_sq(f, r)] == ("K" if by_side == "w" else "k")):
                    return True
        return False

    def apply(self, m: Move) -> Board:
        b = Board(list(self.squares), "b" if self.turn == "w" else "w",
                  self.castling, -1)
        piece = self.squares[m.frm]
        b.squares[m.frm] = ""
        if m.en_passant:
            cap = m.to - 8 if self.turn == "w" else m.to + 8
            b.squares[cap] = ""
        b.squares[m.to] = m.promotion if m.promotion else piece
        if m.castle == "K":
            r = 0 if self.turn == "w" else 7
            b.squares[_sq(5, r)] = b.squares[_sq(7, r)]
            b.squares[_sq(7, r)] = ""
        elif m.castle == "Q":
            r = 0 if self.turn == "w" else 7
            b.squares[_sq(3, r)] = b.squares[_sq(0, r)]
            b.squares[_sq(0, r)] = ""
        # castling rights
        rights = b.castling
        if piece == "K":
            rights = rights.replace("K", "").replace("Q", "")
        elif piece == "k":
            rights = rights.replace("k", "").replace("q", "")
        for sq_idx, flag in ((0, "Q"), (7, "K"), (56, "q"), (63, "k")):
            if m.frm == sq_idx or m.to == sq_idx:
                rights = rights.replace(flag, "")
        b.castling = rights
        # double push sets ep target
        if piece in "Pp" and abs(m.to - m.frm) == 16:
            b.ep = (m.to + m.frm) // 2
        return b

    def legal_moves(self, _annotate: bool = True) -> list[Move]:
        side = self.turn
        own = str.isupper if side == "w" else str.islower
        enemy = "b" if side == "w" else "w"
        out: list[Move] = []
        for i, p in enumerate(self.squares):
            if not p or not own(p):
                continue
            f, r = i % 8, i // 8
            P = p.upper()
            if P == "P":
                dr = 1 if side == "w" else -1
                start_rank = 1 if side == "w" else 6
                promo_rank = 7 if side == "w" else 0
                one_r = r + dr
                if 0 <= one_r < 8 and not self.squares[_sq(f, one_r)]:
                    if one_r == promo_rank:
                        for pr in "QRBN":
                            out.append(Move(i, _sq(f, one_r), promotion=pr))
                    else:
                        out.append(Move(i, _sq(f, one_r)))
                        two_r = r + 2 * dr
                        if r == start_rank and not self.squares[_sq(f, two_r)]:
                            out.append(Move(i, _sq(f, two_r)))
                if 0 <= one_r < 8:
                    for df in (-1, 1):
                        cf = f + df
                        if 0 <= cf < 8:
                            t = _sq(cf, one_r)
                            tp = self.squares[t]
                            is_ep = t == self.ep
                            if (tp and (not own(tp))) or is_ep:
                                if one_r == promo_rank:
                                    for pr in "QRBN":
                                        out.append(Move(i, t, capture=True, promotion=pr,
                                                        en_passant=is_ep))
                                else:
                                    out.append(Move(i, t, capture=True, en_passant=is_ep))
            elif P == "N":
                for df, dr in ((1, 2), (2, 1), (-1, 2), (-2, 1),
                               (1, -2), (2, -1), (-1, -2), (-2, -1)):
                    tf, tr = f + df, r + dr
                    if 0 <= tf < 8 and 0 <= tr < 8:
                        t = _sq(tf, tr)
                        tp = self.squares[t]
                        if not tp or (not own(tp)):
                            out.append(Move(i, t, capture=bool(tp)))
            elif P in "BRQ":
                dirs = []
                if P in "RQ":
                    dirs += [(1, 0), (-1, 0), (0, 1), (0, -1)]
                if P in "BQ":
                    dirs += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
                for df, dr in dirs:
                    tf, tr = f + df, r + dr
                    while 0 <= tf < 8 and 0 <= tr < 8:
                        t = _sq(tf, tr)
                        tp = self.squares[t]
                        if tp:
                            if not own(tp):
                                out.append(Move(i, t, capture=True))
                            break
                        out.append(Move(i, t))
                        tf += df
                        tr += dr
            elif P == "K":
                for df in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        if not df and not dr:
                            continue
                        tf, tr = f + df, r + dr
                        if 0 <= tf < 8 and 0 <= tr < 8:
                            t = _sq(tf, tr)
                            tp = self.squares[t]
                            if not tp or (not own(tp)):
                                out.append(Move(i, t, capture=bool(tp)))
                home = 0 if side == "w" else 7
                if i == _sq(4, home) and not self.attacked(i, enemy):
                    kside = "K" if side == "w" else "k"
                    qside = "Q" if side == "w" else "q"
                    if (kside in self.castling
                            and not self.squares[_sq(5, home)] and not self.squares[_sq(6, home)]
                            and not self.attacked(_sq(5, home), enemy)
                            and not self.attacked(_sq(6, home), enemy)):
                        out.append(Move(i, _sq(6, home), castle="K"))
                    if (qside in self.castling
                            and not self.squares[_sq(1, home)] and not self.squares[_sq(2, home)]
                            and not self.squares[_sq(3, home)]
                            and not self.attacked(_sq(2, home), enemy)
                            and not self.attacked(_sq(3, home), enemy)):
                        out.append(Move(i, _sq(2, home), castle="Q"))
        # legality filter: own king not left in check
        legal = []
        for m in out:
            m2 = Move(m.frm, m.to, piece=self.squares[m.frm], capture=m.capture,
                      promotion=m.promotion, castle=m.castle, en_passant=m.en_passant)
            nxt = self.apply(m2)
            if not nxt.attacked(nxt.king_square(side), enemy):
                legal.append(m2)
        # annotate check/mate for SAN verification (mate probing uses
        # unannotated generation so recursion bottoms out at depth 2)
        if _annotate:
            for idx, m in enumerate(legal):
                nxt = self.apply(m)
                opp = "b" if side == "w" else "w"
                if nxt.attacked(nxt.king_square(opp), side):
                    legal[idx] = dataclasses.replace(
                        m, gives_check=True,
                        gives_mate=not nxt.legal_moves(_annotate=False))
        return legal


RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}
_CASTLES = {"O-O": "K", "O-O-O": "Q", "0-0": "K", "0-0-0": "Q"}


def _parse_san(token: str) -> dict:
    """Deterministic SAN parse (right-anchored destination square), so a
    token like Nd2 is never misread as disambiguation-file 'd'."""
    if token in _CASTLES:
        return {"castle": _CASTLES[token], "suffix": ""}
    suffix = ""
    if token.endswith(("+", "#")):
        suffix, token = token[-1], token[:-1]
    promo = ""
    if "=" in token:
        token, _, pr = token.partition("=")
        if len(pr) != 1 or pr not in "QRBN":
            raise MalformedPGN(f"bad promotion piece in SAN: {pr!r}")
        promo = pr
    if len(token) < 2 or not re.fullmatch(r"[a-h][1-8]", token[-2:]):
        raise MalformedPGN(f"SAN lacks a destination square: {token!r}")
    dest = token[-2:]
    head = token[:-2]
    capture = False
    if head.endswith("x"):
        capture, head = True, head[:-1]
    piece = "P"
    if head and head[0] in "KQRBN":
        piece, head = head[0], head[1:]
    if len(head) > 2:
        raise MalformedPGN(f"overlong disambiguation in SAN: {token!r}")
    dis_file = dis_rank = ""
    for ch in head:
        if ch in FILES and not dis_file:
            dis_file = ch
        elif ch in "12345678" and not dis_rank:
            dis_rank = ch
        else:
            raise MalformedPGN(f"bad disambiguation in SAN: {token!r}")
    if piece == "P" and (dis_rank or (dis_file and not capture)):
        # pawn SAN carries a source file only on captures, never a rank
        raise MalformedPGN(f"bad pawn SAN: {token!r}")
    return {"castle": "", "piece": piece, "dest": dest, "capture": capture,
            "promotion": promo, "dis_file": dis_file, "dis_rank": dis_rank,
            "suffix": suffix}


def _strip_movetext(movetext: str) -> list[str]:
    """Remove comments/NAGs/variations/move numbers; return mainline tokens.
    Fail closed on unbalanced or unterminated constructs."""
    out = []
    i, n = 0, len(movetext)
    depth = 0
    while i < n:
        ch = movetext[i]
        if ch == "{":
            if depth == 0:
                j = movetext.find("}", i + 1)
                if j == -1:
                    raise MalformedPGN("unterminated comment")
                i = j + 1
                continue
            i += 1
            continue
        if ch == ";" and depth == 0:
            j = movetext.find("\n", i + 1)
            i = n if j == -1 else j + 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            if depth == 0:
                raise MalformedPGN("unbalanced variation close")
            depth -= 1
            i += 1
            continue
        if depth > 0:
            i += 1
            continue
        if ch == "$":
            j = i + 1
            while j < n and movetext[j].isdigit():
                j += 1
            if j == i + 1:
                raise MalformedPGN("bare $ without NAG number")
            i = j
            continue
        if ch.isspace():
            i += 1
            continue
        j = i
        while j < n and not movetext[j].isspace() and movetext[j] not in "{}();()":
            j += 1
        tok = movetext[i:j]
        i = j
        if re.fullmatch(r"\d+\.(\.\.)?", tok):
            continue
        out.append(tok)
    if depth != 0:
        raise MalformedPGN("unbalanced variation open")
    return out


def _parse_tags(game_text: str) -> tuple[dict[str, str], str]:
    tags: dict[str, str] = {}
    lines = game_text.splitlines()
    move_lines: list[str] = []
    in_tags = True
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("["):
            m = re.fullmatch(r'\[([A-Za-z0-9_]+) "((?:[^"\\]|\\[\\"])*)"\]', s)
            if not m:
                raise MalformedPGN(f"malformed tag line: {s!r}")
            name, value = m.group(1), m.group(2)
            tags[name] = value.replace('\\"', '"').replace("\\\\", "\\")
        else:
            in_tags = False
            move_lines.append(line)
    if in_tags and not move_lines:
        raise MalformedPGN("no movetext")
    return tags, " ".join(move_lines)


def _match_san(board: Board, token: str) -> Move:
    san = _parse_san(token)
    legal = board.legal_moves()
    if san["castle"]:
        cands = [mv for mv in legal if mv.castle == san["castle"]]
    else:
        dest = (ord(san["dest"][0]) - 97) + 8 * (int(san["dest"][1]) - 1)
        cands = []
        for mv in legal:
            p = board.squares[mv.frm]
            if p.upper() != san["piece"] or mv.to != dest or mv.castle:
                continue
            if (mv.promotion or "") != san["promotion"]:
                continue
            if mv.capture != san["capture"]:
                continue
            if san["dis_file"] and FILES[mv.frm % 8] != san["dis_file"]:
                continue
            if san["dis_rank"] and str(mv.frm // 8 + 1) != san["dis_rank"]:
                continue
            cands.append(mv)
    if not cands:
        # well-formed SAN with no legal move -> illegal (game-rules violation)
        raise IllegalMove(f"illegal SAN in position: {token!r}")
    if len(cands) > 1:
        raise MalformedPGN(f"ambiguous SAN: {token!r}")
    mv = cands[0]
    suffix = san["suffix"]
    if suffix == "+" and not mv.gives_check:
        raise MalformedPGN(f"SAN claims check, move gives none: {token!r}")
    if suffix == "#" and not mv.gives_mate:
        raise MalformedPGN(f"SAN claims mate, move gives none: {token!r}")
    return mv


@dataclass(frozen=True)
class ParsedGame:
    tags: dict[str, str]
    movetext: str          # raw movetext (unstripped) for the stored record
    san_moves: list[str]   # validated mainline SAN tokens (no result token)
    result: str


def validate_pgn_game(game_text: str) -> ParsedGame:
    """Fail-closed validation of one PGN game. Returns the parsed game or
    raises MalformedPGN / IllegalMove. Nothing is repaired or guessed."""
    tags, movetext = _parse_tags(game_text)
    tokens = _strip_movetext(movetext)
    if not tokens:
        raise MalformedPGN("empty movetext")
    result = tokens[-1]
    if result not in RESULTS:
        raise MalformedPGN("movetext missing terminal result token (truncated?)")
    tag_result = tags.get("Result")
    if tag_result and result != "*" and tag_result != result:
        raise MalformedPGN(f"result token {result!r} != Result tag {tag_result!r}")
    board = Board.initial()
    variant = tags.get("Variant", "Standard")
    if variant not in ("Standard", "Chess"):
        raise MalformedPGN(f"unsupported Variant tag for standard import: {variant!r}")
    if "FEN" in tags or "SetUp" in tags:
        raise MalformedPGN("SetUp/FEN games are outside the import-pgn scenario")
    san_moves: list[str] = []
    for tok in tokens[:-1]:
        mv = _match_san(board, tok)
        board = board.apply(mv)
        san_moves.append(tok)
    if not san_moves:
        raise MalformedPGN("movetext has no moves")
    return ParsedGame(tags=tags, movetext=movetext, san_moves=san_moves, result=result)
