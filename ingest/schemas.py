"""T2715 - Versioned schema adapters for lichess games, puzzles and evals.

Every adapter converts one raw source record into a normalized row that:
- carries schema_name + schema_version for forward-compatible evolution,
- preserves every identifier present in the source record,
- carries the source license field verbatim (fail-closed: unknown license
  values are rejected, never assumed).
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1

KNOWN_LICENSES = {"CC0"}

_GAME_ID_FIELDS = ("Site", "White", "Black", "UTCDate", "UTCTime")

_PUZZLE_FIELDS = (
    "PuzzleId", "FEN", "Moves", "Rating", "RatingDeviation",
    "Popularity", "NbPlays", "Themes", "GameUrl", "OpeningTags",
)


@dataclass(frozen=True)
class Normalized:
    schema_name: str
    schema_version: int
    row: dict[str, Any]


def _check_license(license_value: str) -> str:
    if license_value not in KNOWN_LICENSES:
        raise ValueError(f"unknown license {license_value!r}: fail-closed, refusing to normalize")
    return license_value


def adapt_game_pgn(game_text: str, *, source_file: str, license_value: str) -> Normalized:
    """Normalize one lichess PGN game; all headers preserved verbatim."""
    headers: dict[str, str] = {}
    movetext_lines: list[str] = []
    for line in game_text.splitlines():
        if line.startswith("["):
            name, _, rest = line[1:].partition(" ")
            headers[name] = rest.strip().rstrip("]").strip('"')
        elif line.strip():
            movetext_lines.append(line.strip())
    if not movetext_lines or not headers:
        raise ValueError("not a PGN game record")
    identifiers = {k: v for k, v in headers.items() if k in _GAME_ID_FIELDS}
    row = {
        "identifiers": identifiers,
        "headers": headers,
        "movetext": " ".join(movetext_lines),
        "site_url": headers.get("Site"),
        "white": headers.get("White"),
        "black": headers.get("Black"),
        "result": headers.get("Result"),
        "utc_date": headers.get("UTCDate"),
        "time_control": headers.get("TimeControl"),
        "termination": headers.get("Termination"),
        "source_file": source_file,
        "license": _check_license(license_value),
    }
    return Normalized("game", SCHEMA_VERSION, row)


def adapt_puzzle_csv_line(
    header: list[str], line: str, *, source_file: str, license_value: str
) -> Normalized:
    """Normalize one lichess puzzle CSV row (real format: lichess_db_puzzle.csv)."""
    parsed = next(csv.reader(io.StringIO(line)))
    record = dict(zip(header, parsed, strict=True))
    missing = [f for f in _PUZZLE_FIELDS if f not in record]
    if missing:
        raise ValueError(f"puzzle row missing source fields: {missing}")
    row = {
        "identifiers": {"PuzzleId": record["PuzzleId"], "GameUrl": record["GameUrl"]},
        "puzzle_id": record["PuzzleId"],
        "fen": record["FEN"],
        "moves": record["Moves"].split(),
        "rating": int(record["Rating"]),
        "rating_deviation": int(record["RatingDeviation"]),
        "popularity": int(record["Popularity"]),
        "nb_plays": int(record["NbPlays"]),
        "themes": record["Themes"].split() if record["Themes"] else [],
        "game_url": record["GameUrl"],
        "opening_tags": record["OpeningTags"].split() if record["OpeningTags"] else [],
        "source_file": source_file,
        "license": _check_license(license_value),
    }
    return Normalized("puzzle", SCHEMA_VERSION, row)


def adapt_eval_jsonl(line: str, *, source_file: str, license_value: str) -> Normalized:
    """Normalize one lichess eval JSONL record (real format: lichess_db_eval.jsonl)."""
    record = json.loads(line)
    if "fen" not in record or "evals" not in record:
        raise ValueError("eval record missing fen/evals")
    evals = [
        {
            "knodes": e["knodes"],
            "depth": e["depth"],
            "pvs": [
                {"cp": pv.get("cp"), "mate": pv.get("mate"), "line": pv["line"]}
                for pv in e["pvs"]
            ],
        }
        for e in record["evals"]
    ]
    row = {
        "identifiers": {"fen": record["fen"]},
        "fen": record["fen"],
        "evals": evals,
        "source_file": source_file,
        "license": _check_license(license_value),
    }
    return Normalized("eval", SCHEMA_VERSION, row)
