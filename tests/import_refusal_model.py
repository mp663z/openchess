"""Pure reference intake for T0570/T0581/T0592 scenario refusal batteries."""
from __future__ import annotations

from dataclasses import dataclass

from ingest.pgn import IllegalMove, MalformedPGN, validate_pgn_game
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, _split


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
        raise ValueError("source outside scenario")
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
            needle = str(error).split(": ")[-1].strip("'")
            line = next((i for i, value in enumerate(game.splitlines(), start=1)
                         if needle in value), None)
            marker = {"game_number": seq, "line": line} if marker_name == "location" else {
                "game_number": seq, "san": str(error).rsplit("'", 2)[1]}
            rejection = Rejection(scenario["visible_output"], source_id, seq,
                                  code, str(error), {marker_name: marker})
            store.rejections = [rejection]
            store.telemetry.extend(("import.game_rejected", "import.completed"))
            return rejection
    raise AssertionError("fixture must contain a rejected game")
