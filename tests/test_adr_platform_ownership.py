"""T2793: ADR-0005 structural battery - the platform ownership matrix
is STRUCTURED front matter, exactly pinned and evaluated as a truth
table; prose is checked for consistency. Mutations moving, dropping,
adding or sharing a capability fail."""

from pathlib import Path

import yaml

ADR = (Path(__file__).resolve().parent.parent / "docs/adr"
       / "ADR-0005-platform-ownership.md").read_text()

DESKTOP = ["pgn", "index", "stockfish", "models", "delta"]
WEB = ["queue", "diff", "approval", "quiet-week", "drills", "transfer"]
OWNERSHIP = {**{c: "desktop" for c in DESKTOP}, **{c: "web" for c in WEB}}
INVARIANTS = [
    "every-capability-has-exactly-one-owner",
    "desktop-owns-every-heavy-compute-capability",
    "web-owns-every-habit-loop-capability",
    "no-capability-is-shared",
]


def _parse(adr: str):
    assert adr.startswith("---\n"), "YAML front matter required"
    fm = yaml.safe_load(adr.split("---\n", 2)[1])
    body = adr.split("---\n", 2)[2]
    secs: dict[str, list[str]] = {"__header__": []}
    current = "__header__"
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            secs[current] = []
        else:
            secs.setdefault(current, []).append(line)
    return fm, {k: "\n".join(v) for k, v in secs.items()}


def _check(adr: str) -> None:
    fm, secs = _parse(adr)
    assert fm["adr"] == "ADR-0005"
    assert fm["status"] == "proposed"

    # truth table over the structured matrix
    ownership = fm["ownership"]
    assert ownership == OWNERSHIP, "matrix must pin every capability exactly"
    for capability, owner in ownership.items():
        assert owner in {"desktop", "web"}, \
            f"{capability}: owner must be desktop or web"
    for capability in DESKTOP + WEB:
        assert capability in ownership, f"{capability} missing from matrix"
    assert len(ownership) == len(DESKTOP) + len(WEB), \
        "no extra capabilities, no duplicates possible"
    heavy = {"stockfish", "models", "index", "delta", "pgn"}
    habit = {"queue", "diff", "approval", "quiet-week", "drills", "transfer"}
    assert {c for c, o in ownership.items() if o == "desktop"} == heavy
    assert {c for c, o in ownership.items() if o == "web"} == habit
    assert fm["invariants"] == INVARIANTS

    # prose consistency
    assert "Status: proposed" in secs.get("__header__", "")
    decision = secs.get("Decision", "")
    assert "front matter is" in decision and "normative" in decision
    for capability in DESKTOP:
        assert f"**{capability}**" in decision, \
            f"desktop capability {capability} undocumented"
    for capability in WEB:
        assert f"**{capability}**" in decision, \
            f"web capability {capability} undocumented"
    assert "Desktop owns" in decision and "Web owns" in decision
    alts = secs.get("Alternatives considered", "")
    assert "Shared ownership" in alts and "rejected" in alts
    cons = secs.get("Consequences", "")
    assert "exactly one owner" in cons
    assert "no capability is shared" in cons
    assert "every heavy-compute capability" in cons
    assert "every habit-loop capability" in cons


def _bad(mutated: str) -> None:
    try:
        _check(mutated)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR)


def test_mutation_move_capability_fails():
    _bad(ADR.replace("  transfer: web", "  transfer: desktop"))


def test_mutation_drop_capability_fails():
    _bad(ADR.replace("  quiet-week: web\n", ""))


def test_mutation_add_capability_fails():
    _bad(ADR.replace("  transfer: web", "  transfer: web\n  hints: web"))


def test_mutation_shared_capability_fails():
    _bad(ADR.replace("  pgn: desktop", "  pgn: both"))


def test_mutation_invariant_fails():
    _bad(ADR.replace("no-capability-is-shared",
                     "capabilities-may-be-shared"))


def test_mutation_status_fails():
    _bad(ADR.replace("status: proposed", "status: accepted"))


def test_mutation_prose_fails():
    _bad(ADR.replace("- **queue**", "- **backlog**"))
