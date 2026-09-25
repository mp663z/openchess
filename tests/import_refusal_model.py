"""Pure reference intake for T0570/T0581/T0592 scenario refusal batteries."""
from __future__ import annotations

from dataclasses import dataclass

from ingest.pgn import IllegalMove, MalformedPGN, validate_pgn_game
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, Refusal, _split


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
                needle = "[" + needle
                occurrences = [i for i, value in enumerate(lines, start=1)
                               if value.lstrip().startswith(needle)]
                line = occurrences[1] if len(occurrences) >= 2 else None
            else:
                line = next((i for i, value in enumerate(lines, start=1)
                             if needle in value), None)
            if marker_name == "location":
                marker = {"game_number": seq, "line": line or len(lines)}
            else:
                san = detail.rsplit("'", 2)[1]
                # Count occurrences in mainline movetext up through the rejected
                # token; the last occurrence identifies its one-based ply.
                moves = game.split("\n\n", 1)[-1].split()
                tokens = [token for token in moves if not token.rstrip(".").isdigit()
                          and token not in ("1-0", "0-1", "1/2-1/2", "*")]
                # The PGN validator stops at the first illegal SAN. Match
                # its failing prefix rather than the last textual repeat.
                from ingest.pgn import Board, _match_san

                board = Board.initial()
                ply = None
                for index, token in enumerate(tokens, start=1):
                    try:
                        move = _match_san(board, token)
                    except (IllegalMove, MalformedPGN):
                        ply = index
                        break
                    board = board.apply(move)
                assert ply is not None and tokens[ply - 1] == san
                marker = {"game_number": seq, "ply": ply, "san": san}
            rejection = Rejection(scenario["visible_output"], source_id, seq,
                                  code, str(error), {marker_name: marker})
            store.rejections = [rejection]
            store.telemetry.extend(("import.game_rejected", "import.completed"))
            return rejection
    raise AssertionError("fixture must contain a rejected game")
