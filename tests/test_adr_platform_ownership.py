"""T2793: ADR-0005 structural battery v3 - the platform ownership matrix
is a STRUCTURED refinement of ADR-0004: per-dimension ownership
(compute / presentation / authoritative state) consistent with
ADR-0004's operation owners (whose owner_meaning is pinned as
implementation-and-execution ownership), a normative crosswalk from
every capability to ADR-0004 operations, and a phase-split transfer.
Ownership prose lives only in a table generated from the front matter;
free-form ownership claims elsewhere fail. Mutations of dimensions,
crosswalks, the generated table, the chain, or the prose fail."""

import re
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
DESKTOP_CAPS = ["pgn", "index", "stockfish", "models", "delta", "export"]
WEB_CAPS = ["queue", "diff", "approval", "quiet-week", "drills",
            "transfer"]
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
CLAIM_RE = re.compile(
    r"(?i)\b(desktop|web|server|byom|provider|hosted byom)\b"
    r"[^.\n]{0,80}?(?<!-)\b(owns|owned|ownership|controls?|jointly|"
    r"decrypts?)\b[^.\n]{0,80}"
)
NEGATION_GUARDS = (
    "no capability", "never capability", "never decrypt",
    "decrypts never", "nothing", "never re-own",
)


def _parse(adr: str):
    assert adr.startswith("---\n"), "YAML front matter required"
    fm = yaml.safe_load(adr.split("---\n", 2)[1])
    body = adr.split("---\n", 2)[2]
    secs: dict[str, list[str]] = {"__header__": []}
    current = "__header__"
    for line in body.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            current = line.lstrip("#").strip()
            secs[current] = []
        else:
            secs.setdefault(current, []).append(line)
    return fm, body, {k: "\n".join(v) for k, v in secs.items()}


def _gen_table(caps) -> str:
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
    return "\n".join(lines)


def _check(adr: str, adr4: str) -> None:
    fm, body, secs = _parse(adr)
    fm4, _, _ = _parse(adr4)
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
            # Mechanical chain: per-dimension owners equal the owning
            # platform of every ADR-0004 operation realized.
            for op in crosswalk:
                assert op in ops4, (cap, op)
                assert entry["compute_owner"] == ops4[op], (cap, op)
                assert entry["presentation_owner"] == ops4[op], (cap, op)
    assert realized == set(ops4), "crosswalk coverage must match ADR-0004"
    desktop_sec = secs["Desktop capabilities"]
    web_sec = secs["Web capabilities"]
    for cap in DESKTOP_CAPS:
        assert f"**{cap}**" in desktop_sec, cap
    for cap in WEB_CAPS:
        assert f"**{cap}**" in web_sec, cap
    # The ownership table in the prose must equal the table generated
    # from the front matter, cell for cell.
    table = _gen_table(caps)
    assert table in body
    # No ownership claims outside the generated table, except
    # negation-guarded statements.
    for match in CLAIM_RE.finditer(body.replace(table, "")):
        window = match.group(0).lower()
        assert any(g in window for g in NEGATION_GUARDS), window


def _bad(mutated: str, mutated4: str | None = None) -> None:
    try:
        _check(mutated, mutated4 if mutated4 is not None else ADR4)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR, ADR4)


def test_crosswalk_mutations_fail():
    # sync-encrypt phase moved to web
    _bad(ADR.replace("sync-encrypt: {actor: desktop",
                     "sync-encrypt: {actor: web"))
    # export omitted from the matrix
    _bad(ADR.replace("  export:\n    compute_owner: desktop\n"
                     "    presentation_owner: desktop\n"
                     "    authoritative_state_owner: desktop\n"
                     "    consumes: []\n    crosswalk: [export]\n", ""))
    # models moved to web
    _bad(ADR.replace("  models:\n    compute_owner: desktop\n"
                     "    presentation_owner: desktop",
                     "  models:\n    compute_owner: desktop\n"
                     "    presentation_owner: web"))
    # crosswalk target renamed away from ADR-0004 operations
    _bad(ADR.replace("crosswalk: [model-inference]",
                     "crosswalk: [llm-inference]"))


def test_table_cell_mutations_fail():
    # a compute cell contradicting the front matter
    _bad(ADR.replace("| queue | web | web | desktop | delta, index |"
                     " queue |",
                     "| queue | desktop | web | desktop | delta, index |"
                     " queue |"))
    # a presentation cell contradicting the front matter
    _bad(ADR.replace("| pgn | desktop | desktop | desktop | - | import |",
                     "| pgn | desktop | web | desktop | - | import |"))
    # a consumes cell emptied
    _bad(ADR.replace("| approval | web | web | desktop | delta |"
                     " approval |",
                     "| approval | web | web | desktop | - | approval |"))


def test_dimension_mutations_fail():
    # queue compute owner contradicts ADR-0004's queue owner
    _bad(ADR.replace("  queue:\n    compute_owner: web\n"
                     "    presentation_owner: web",
                     "  queue:\n    compute_owner: desktop\n"
                     "    presentation_owner: web"))
    # drills compute owner contradicts ADR-0004's training owner
    _bad(ADR.replace("  drills:\n    compute_owner: web",
                     "  drills:\n    compute_owner: desktop"))
    # server gains an ownership dimension
    _bad(ADR.replace("  pgn:\n    compute_owner: desktop",
                     "  pgn:\n    compute_owner: server"))
    # a capability's authoritative state leaves the desktop
    _bad(ADR.replace("  queue:\n    compute_owner: web\n"
                     "    presentation_owner: web\n"
                     "    authoritative_state_owner: desktop",
                     "  queue:\n    compute_owner: web\n"
                     "    presentation_owner: web\n"
                     "    authoritative_state_owner: web"))
    # relay may decrypt
    _bad(ADR.replace("decrypts: never", "decrypts: allowed"))
    # relay actor leaves the server
    _bad(ADR.replace("{actor: server", "{actor: desktop"))
    # invariant weakened
    _bad(ADR.replace(
        "compute-and-presentation-match-adr0004-operation-owner",
        "compute-and-presentation-usually-match"))


def test_prose_contradiction_mutations_fail():
    originals = [
        "The desktop also owns queue and approval.",
        "The server owns transfer and may decrypt it.",
        "Hosted BYOM owns model inference by default.",
    ]
    refined = [
        "Desktop additionally owns queue and approval.",
        "Web jointly owns pgn.",
        "Desktop has ownership of queue.",
        "The server controls transfer and can decrypt it.",
    ]
    for clause in originals + refined:
        _bad(ADR.replace("co-presented.", "co-presented. " + clause))
    _bad(ADR.replace("co-presented.",
                     "co-presented. " + " ".join(originals)))


def test_chain_consistency_with_adr0004_mutations_fail():
    # an ADR-0004 operation disappearing breaks full coverage
    _bad(ADR, ADR4.replace("  sync-decrypt: web\n", ""))
    # an ADR-0004 owner flip breaks owner equality
    _bad(ADR, ADR4.replace("  queue: web\n", "  queue: desktop\n"))
    # owner_meaning removed: the chain's dimension semantics are gone
    _bad(ADR, ADR4.replace(
        "owner_meaning: implementation-and-execution-ownership\n", ""))
    # owner_meaning weakened
    _bad(ADR, ADR4.replace(
        "owner_meaning: implementation-and-execution-ownership",
        "owner_meaning: presentation-ownership"))


def test_mutation_status_fails():
    _bad(ADR.replace("status: proposed", "status: accepted"))


def test_mutation_prose_rename_fails():
    _bad(ADR.replace("- **queue**", "- **backlog**"))
