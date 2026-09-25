"""Pure reference intake for T0570/T0581/T0592 scenario refusal batteries."""
from __future__ import annotations

from dataclasses import dataclass

from ingest.pgn import (
    Board,
    IllegalMove,
    MalformedPGN,
    _match_san,
    _parse_tags,
    _strip_movetext,
    validate_pgn_game,
)
from tests.test_t0548_import_multi_pgn_contract import (
    ReferenceStore,
    Refusal,
    _split,
    reference_import,
)


@dataclass
class Rejection:
    kind: str
    source_id: str
    game_number: int
    code: str
    detail: str
    marker: dict


def reference_refusal(text, store: ReferenceStore, *, source_id="pgn-file", scenario):
    if source_id not in scenario["sources"]:
        raise Refusal("unknown_rights")
    # This model only accepts fixtures containing a rejected game. Detect an
    # all-valid fixture before any prefix can be committed or telemetry emitted.
    for game in _split(text):
        try:
            validate_pgn_game(game)
        except (MalformedPGN, IllegalMove):
            break
    else:
        raise AssertionError("refusal fixture has no rejected game")

    store.telemetry.append("import.started")
    for seq, game in enumerate(_split(text), start=1):
        try:
            validate_pgn_game(game)
        except (MalformedPGN, IllegalMove) as error:
            code = "illegal_move" if isinstance(error, IllegalMove) else "malformed_request"
            # T0537 has only unstructured exception text. Narrowest shape
            # carrying the stated location/position: game number plus the
            # exact offending line or SAN token, without guessed coordinates.
            marker_name = "position" if code == "illegal_move" else "location"
            detail = str(error)
            needle = detail.split(": ")[-1].strip("'")
            lines = game.splitlines()
            if "duplicate tag:" in detail:
                # The second tag occurrence is the offending one.
                needle = "[" + needle + " \""
                occurrences = [i for i, value in enumerate(lines, start=1)
                               if value.lstrip().startswith(needle)]
                line = occurrences[1] if len(occurrences) >= 2 else None
            else:
                line = next((i for i, value in enumerate(lines, start=1)
                             if needle in value), None)
            if marker_name == "location":
                if scenario["id"] == "import-truncated":
                    # T0581's separate EOF reading deliberately reports
                    # the last line, even for an earlier unmatched opener.
                    line = len(lines)
                elif detail in ("unterminated comment", "unbalanced variation open"):
                    # Only movetext is parsed for openers: tag values are
                    # data. Match the validator's semicolon/comment and
                    # variation handling, retaining original game line bases.
                    open_variations = []
                    comment_start = None
                    in_tags = True
                    for line_number, value in enumerate(lines, start=1):
                        trimmed = value.strip()
                        if not trimmed:
                            continue
                        if in_tags and trimmed.startswith("["):
                            continue
                        in_tags = False
                        for ch in value:
                            if comment_start is not None:
                                if ch == "}":
                                    comment_start = None
                            elif ch == "{" and not open_variations:
                                comment_start = line_number
                            elif ch == ";" and not open_variations:
                                break
                            elif ch == "(":
                                open_variations.append(line_number)
                            elif ch == ")" and open_variations:
                                open_variations.pop()
                    if detail == "unterminated comment":
                        line = comment_start
                    else:
                        line = open_variations[-1] if open_variations else None
                    if line is None:
                        raise AssertionError("unlocatable unmatched opener") from None
                elif detail in ("unbalanced comment close", "unbalanced variation close"):
                    # Locate the first illegal closer using the same state
                    # changes as _strip_movetext; tag values are not movetext.
                    expected = "}" if detail == "unbalanced comment close" else ")"
                    depth = 0
                    in_tags = True
                    comment = False
                    found = None
                    for line_number, value in enumerate(lines, start=1):
                        trimmed = value.strip()
                        if not trimmed:
                            continue
                        if in_tags and trimmed.startswith("["):
                            continue
                        in_tags = False
                        cursor = 0
                        while cursor < len(value):
                            ch = value[cursor]
                            if comment:
                                if ch == "}":
                                    comment = False
                            elif ch == "{" and depth == 0:
                                comment = True
                            elif ch == ";" and depth == 0:
                                break
                            elif ch == "(":
                                depth += 1
                            elif ch == ")":
                                if depth == 0:
                                    found = (ch, line_number)
                                    break
                                depth -= 1
                            elif ch == "}" and depth == 0:
                                found = (ch, line_number)
                                break
                            cursor += 1
                        if found is not None:
                            break
                    if found is None or found[0] != expected:
                        raise AssertionError("unlocatable stray closer") from None
                    line = found[1]
                marker = {"game_number": seq, "line": line or len(lines)}
            else:
                san = detail.rsplit("'", 2)[1]
                # Reuse the validator's own mainline tokenization so comments,
                # NAGs, variations and glued move numbers do not shift ply.
                try:
                    tokens = [token for token in _strip_movetext(_parse_tags(game)[1])
                              if token not in ("1-0", "0-1", "1/2-1/2", "*")]
                    board = Board.initial()
                    ply = None
                    for index, token in enumerate(tokens, start=1):
                        try:
                            move = _match_san(board, token)
                        except (IllegalMove, MalformedPGN):
                            ply = index
                            break
                        board = board.apply(move)
                    if ply is None or tokens[ply - 1] != san:
                        raise ValueError("unlocatable rejected SAN")
                except (MalformedPGN, IllegalMove, ValueError, IndexError, TypeError):
                    raise Refusal("malformed_request") from None
                marker = {"game_number": seq, "ply": ply, "san": san}
            rejection = Rejection(scenario["visible_output"], source_id, seq,
                                  code, str(error), {marker_name: marker})
            store.rejections = [rejection]
            store.telemetry.extend(("import.game_rejected", "import.completed"))
            return rejection
        # The game is validated before state change. A valid prefix game
        # commits per the T0548 merged per-game atomicity reading.
        prior_events = len(store.telemetry)
        reference_import(game, store, source_id=source_id, scenario=scenario)
        store.telemetry[prior_events:] = ["import.game_stored"]
        store.summary = None
