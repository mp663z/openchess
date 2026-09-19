"""T2793: ADR-0005 structural battery v4 - the platform ownership matrix
is a STRUCTURED refinement of ADR-0004: per-dimension ownership
(compute / presentation / authoritative state) consistent with
ADR-0004's operation owners (owner_meaning pinned as
implementation-and-execution ownership), a normative crosswalk, and a
phase-split transfer. Policy prose is not validated by vocabulary
regex: the policy block is GENERATED from the front matter and the
entire body - generated block plus pinned explanatory sections - is
compared byte-for-byte. Any inserted or edited sentence fails because
the text changed, never because a denylist matched. Mutations of
dimensions, crosswalks, the chain, the table, or the prose fail."""

from pathlib import Path

import yaml

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"
ADR = (ADR_DIR / "ADR-0005-platform-ownership.md").read_text()
ADR4 = (ADR_DIR / "ADR-0004-asymmetric-architecture.md").read_text()

EXPECTED_CROSSWALK = {
    "pgn": ["import"], "index": ["index"], "stockfish": ["stockfish"],
    "models": ["model-inference"], "delta": ["delta"],
    "export": ["export"], "queue": ["queue"], "diff": ["queue"],
    "approval": ["approval"], "quiet-week": ["training"],
    "drills": ["training"],
    "transfer": ["sync-encrypt", "sync-decrypt"],
}
EXPECTED_CONSUMES = {
    "queue": ["delta", "index"], "diff": ["delta"],
    "approval": ["delta"], "quiet-week": ["model-inference", "delta"],
    "drills": ["model-inference"],
}
INVARIANTS = [
    "every-capability-has-exactly-one-presentation-owner",
    "compute-and-presentation-match-adr0004-operation-owner",
    "authoritative-state-is-desktop-for-every-capability",
    "server-owns-no-capability",
    "transfer-splits-encrypt-desktop-decrypt-web",
    "hosted-byom-is-invocation-exception-never-ownership",
]

HEADER = """
# ADR-0005: Platform ownership (T2793)

Status: proposed
Date: 2026-09-20
"""
CONTEXT = """
## Context

ADR-0004 fixes the asymmetric split and its operation/owner matrix,
where operation ownership means implementation-and-execution
ownership. This ADR refines it: it assigns every product capability a
concrete home per ownership dimension and pins a normative crosswalk
from each capability to the ADR-0004 operations it realizes, so no
second vocabulary drifts from the first. Scope: this ADR decides
presentation-and-implementation ownership only; ADR-0004 retains
architecture authority and data-flow ownership. The split follows
ADR-0004's deciding axes: heavy compute and private data stay on
hardware the user owns; the daily habit loop lives on the surface
the user actually carries.
"""
DECISION_INTRO = """
## Decision

The capability matrix in this document's YAML front matter is
normative. Ownership and security statements live only in the front
matter and the generated policy block below. The block is produced
mechanically from the front matter and the contract battery compares
it byte-for-byte; explanatory prose outside the block is pinned
byte-for-byte as approved text and carries no ownership or security
policy of its own.
"""
BLOCK_BEGIN = """
<!-- BEGIN GENERATED POLICY: battery-generated; never edited by hand -->

"""
BLOCK_END = """
<!-- END GENERATED POLICY -->
"""
NOTES = """
### Capability notes

Desktop capabilities:

- **pgn** - PGN import, parse and storage.
- **index** - the local game/position index.
- **stockfish** - engine analysis.
- **models** - local model weights and inference.
- **delta** - the delta engine.
- **export** - Anki/Chessable export generation.

Web capabilities:

- **queue** - the review queue.
- **diff** - the review diff presentation.
- **approval** - the approval gate.
- **quiet-week** - the quiet-week screen.
- **drills** - training drills.
- **transfer** - movement between surfaces over the encrypted relay.
"""
ALTERNATIVES = """
## Alternatives considered

- **Shared compute for analysis results**: rejected - two writers for
  one capability recreates the conflict and divergence costs the
  asymmetric split exists to avoid; transfer is the sync story, not
  shared production.
- **Hosted drills execution**: rejected - drills execute in the PWA
  over artifacts produced by the desktop model-inference capability;
  nothing in the loop requires hosted compute.
- **A single web home for transfer**: rejected - it obscures the
  security boundary ADR-0004 draws between sync-encrypt (desktop) and
  sync-decrypt (web); the phase split is the normative shape.
"""
CONSEQUENCES = """
## Consequences

The normative consequences of this decision are exactly the policy
statements in the generated block above. Operationally: new
capabilities must join this matrix with a crosswalk in a future ADR
revision before implementation tasks may claim them.
"""

def _gen_policy_block(caps) -> str:
    lines = [
        "| capability | compute | presentation | authoritative_state"
        " | consumes | realizes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for cap, spec in caps.items():
        consumes = ", ".join(spec["consumes"]) if spec["consumes"] else "-"
        realizes = ", ".join(spec["crosswalk"])
        if "sub_capability_of" in spec:
            realizes += f" (sub-capability of {spec['sub_capability_of']})"
        if "phases" in spec:
            parts = []
            for ph_name, ph_spec in spec["phases"].items():
                seg = f"{ph_name} {ph_spec['actor']}"
                for key, val in ph_spec.items():
                    if key != "actor":
                        seg += f" {key} {val}"
                parts.append(seg)
            realizes += "; phases: " + ", ".join(parts)
        lines.append(
            f"| {cap} | {spec['compute_owner']} | "
            f"{spec['presentation_owner']} | "
            f"{spec['authoritative_state_owner']} | {consumes} | "
            f"{realizes} |")
    desktop = [c for c, s in caps.items()
               if s["compute_owner"] == "desktop"]
    web = [c for c, s in caps.items() if s["compute_owner"] == "web"]
    states = {s["authoritative_state_owner"] for s in caps.values()}
    assert len(states) == 1
    state = next(iter(states))
    ph = caps["transfer"]["phases"]
    lines.append("")
    lines.append("Policy statements:")
    lines.append(
        "- Compute and presentation: desktop implements and runs "
        + ", ".join(desktop) + "; web implements and runs "
        + ", ".join(web) + ".")
    lines.append(
        "- Transfer is split by phase: sync-encrypt is implemented on "
        f"{ph['sync-encrypt']['actor']} and sends "
        f"{ph['sync-encrypt']['sends']}; the relay "
        f"({ph['relay']['actor']}) stores {ph['relay']['stores']} and "
        f"decrypts {ph['relay']['decrypts']}; sync-decrypt is "
        f"implemented on {ph['sync-decrypt']['actor']} and decrypts "
        f"{ph['sync-decrypt']['decrypts']}.")
    lines.append(
        f"- Authoritative state owner is {state} for every capability.")
    consumes_parts = [
        f"{c} consumes " + ", ".join(s["consumes"])
        for c, s in caps.items() if s["consumes"]]
    lines.append(
        "- Web capabilities consume desktop-produced artifacts: "
        + "; ".join(consumes_parts) + ".")
    subs = [f"{c} is a sub-capability of {s['sub_capability_of']}"
            for c, s in caps.items() if "sub_capability_of" in s]
    lines.append("- " + "; ".join(subs) + ".")
    lines.append(
        "- The relay stores only ciphertext and never decrypts; no "
        "capability is owned, implemented or executed by the server.")
    lines.append(
        "- Hosted BYOM is an optional invocation exception, never "
        "capability ownership and never a default.")
    return "\n".join(lines)

def _gen_body(fm) -> str:
    return (HEADER + CONTEXT + DECISION_INTRO + BLOCK_BEGIN
            + _gen_policy_block(fm["capabilities"]) + BLOCK_END
            + NOTES + ALTERNATIVES + CONSEQUENCES)


def _parse(adr: str):
    assert adr.startswith("---\n"), "YAML front matter required"
    fm = yaml.safe_load(adr.split("---\n", 2)[1])
    body = adr.split("---\n", 2)[2]
    return fm, body


def _check(adr: str, adr4: str) -> None:
    fm, body = _parse(adr)
    fm4, _ = _parse(adr4)
    assert fm["adr"] == "ADR-0005"
    assert fm["status"] == "proposed"
    assert fm["scope"] == "presentation-and-implementation-ownership"
    assert fm4.get("owner_meaning") == (
        "implementation-and-execution-ownership")
    ops4 = fm4["operations"]
    caps = fm["capabilities"]
    assert sorted(caps) == sorted(EXPECTED_CROSSWALK)
    for inv in INVARIANTS:
        assert inv in fm["invariants"], inv
    realized = set()
    for cap, crosswalk in EXPECTED_CROSSWALK.items():
        entry = caps[cap]
        assert entry["crosswalk"] == crosswalk, cap
        assert entry["authoritative_state_owner"] == "desktop", cap
        realized.update(crosswalk)
        if cap == "transfer":
            assert entry["compute_owner"] == "split"
            assert entry["presentation_owner"] == "web"
            assert entry["consumes"] == []
            ph = entry["phases"]
            assert ph["sync-encrypt"] == {
                "actor": "desktop", "sends": "ciphertext"}
            assert ph["relay"] == {
                "actor": "server", "stores": "ciphertext-only",
                "decrypts": "never"}
            assert ph["sync-decrypt"] == {
                "actor": "web", "decrypts": "local-only"}
        else:
            assert "phases" not in entry, cap
            assert entry["consumes"] == EXPECTED_CONSUMES.get(cap, []), cap
            for op in crosswalk:
                assert op in ops4, (cap, op)
                assert entry["compute_owner"] == ops4[op], (cap, op)
                assert entry["presentation_owner"] == ops4[op], (cap, op)
    assert realized == set(ops4), "crosswalk coverage must match ADR-0004"
    # Byte-for-byte policy enforcement: the body must equal the
    # pinned explanatory sections plus the policy block generated
    # from the front matter. Any inserted or edited sentence - a
    # responsibility claim, a "belongs to", a decoder relay, an
    # exception smuggled behind a negation - changes the bytes and
    # fails here. No vocabulary matching is involved.
    assert body == _gen_body(fm), "body differs from pinned + generated"


def _bad(mutated: str, mutated4: str | None = None) -> None:
    try:
        _check(mutated, mutated4 if mutated4 is not None else ADR4)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR, ADR4)


def test_crosswalk_mutations_fail():
    _bad(ADR.replace("sync-encrypt: {actor: desktop",
                     "sync-encrypt: {actor: web"))
    _bad(ADR.replace("  export:\n    compute_owner: desktop\n"
                     "    presentation_owner: desktop\n"
                     "    authoritative_state_owner: desktop\n"
                     "    consumes: []\n    crosswalk: [export]\n", ""))
    _bad(ADR.replace("  models:\n    compute_owner: desktop\n"
                     "    presentation_owner: desktop",
                     "  models:\n    compute_owner: desktop\n"
                     "    presentation_owner: web"))
    _bad(ADR.replace("crosswalk: [model-inference]",
                     "crosswalk: [llm-inference]"))


def test_table_cell_mutations_fail():
    _bad(ADR.replace("| queue | web | web | desktop | delta, index |"
                     " queue |",
                     "| queue | desktop | web | desktop | delta, index |"
                     " queue |"))
    _bad(ADR.replace("| pgn | desktop | desktop | desktop | - | import |",
                     "| pgn | desktop | web | desktop | - | import |"))
    _bad(ADR.replace("| approval | web | web | desktop | delta |"
                     " approval |",
                     "| approval | web | web | desktop | - | approval |"))


def test_dimension_mutations_fail():
    _bad(ADR.replace("  queue:\n    compute_owner: web\n"
                     "    presentation_owner: web",
                     "  queue:\n    compute_owner: desktop\n"
                     "    presentation_owner: web"))
    _bad(ADR.replace("  drills:\n    compute_owner: web",
                     "  drills:\n    compute_owner: desktop"))
    _bad(ADR.replace("  pgn:\n    compute_owner: desktop",
                     "  pgn:\n    compute_owner: server"))
    _bad(ADR.replace("  queue:\n    compute_owner: web\n"
                     "    presentation_owner: web\n"
                     "    authoritative_state_owner: desktop",
                     "  queue:\n    compute_owner: web\n"
                     "    presentation_owner: web\n"
                     "    authoritative_state_owner: web"))
    _bad(ADR.replace("decrypts: never", "decrypts: allowed"))
    _bad(ADR.replace("{actor: server", "{actor: desktop"))
    _bad(ADR.replace(
        "compute-and-presentation-match-adr0004-operation-owner",
        "compute-and-presentation-usually-match"))


def test_prose_contradiction_mutations_fail():
    # Every inserted ownership/security sentence fails because the
    # pinned/generated bytes changed - never via vocabulary matching.
    # The last six are verifier #2's v3 escapees, including the
    # negation-guard exploit.
    anchor = "implementation tasks may claim them."
    clauses = [
        "The desktop also owns queue and approval.",
        "The server owns transfer and may decrypt it.",
        "Hosted BYOM owns model inference by default.",
        "Desktop additionally owns queue and approval.",
        "Web jointly owns pgn.",
        "Desktop has ownership of queue.",
        "The server controls transfer and can decrypt it.",
        "Desktop is responsible for queue and approval.",
        "Queue belongs to desktop.",
        "Transfer is controlled by the server, which can decode"
        " ciphertext.",
        "The relay reads plaintext.",
        "Hosted BYOM handles model inference automatically.",
        "Web owns nothing except pgn.",
    ]
    for clause in clauses:
        _bad(ADR.replace(anchor, anchor + " " + clause))
    _bad(ADR.replace(anchor, anchor + " " + " ".join(clauses[:3])))
    # Insertions in other pinned sections fail the same way.
    _bad(ADR.replace("- **pgn** - PGN import, parse and storage.",
                     "- **pgn** - PGN import, parse and storage."
                     " Desktop runs queue too."))
    _bad(ADR.replace("the daily habit loop lives on the surface",
                     "the desktop also approves reviews; the daily"
                     " habit loop lives on the surface"))
    # Editing the generated policy statements fails.
    _bad(ADR.replace("the relay (server) stores ciphertext-only and"
                     " decrypts never",
                     "the relay (server) stores ciphertext-only and"
                     " decrypts when asked"))
    _bad(ADR.replace("decrypts never; sync-decrypt is implemented on"
                     " web",
                     "decrypts never; sync-decrypt is implemented on"
                     " the server"))


def test_chain_consistency_with_adr0004_mutations_fail():
    _bad(ADR, ADR4.replace("  sync-decrypt: web\n", ""))
    _bad(ADR, ADR4.replace("  queue: web\n", "  queue: desktop\n"))
    _bad(ADR, ADR4.replace(
        "owner_meaning: implementation-and-execution-ownership\n", ""))
    _bad(ADR, ADR4.replace(
        "owner_meaning: implementation-and-execution-ownership",
        "owner_meaning: presentation-ownership"))


def test_mutation_status_fails():
    _bad(ADR.replace("status: proposed", "status: accepted"))


def test_mutation_prose_rename_fails():
    _bad(ADR.replace("- **queue**", "- **backlog**"))
