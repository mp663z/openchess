"""T2793: ADR-0005 structural battery v2 - the platform ownership matrix
is a STRUCTURED refinement of ADR-0004: per-dimension ownership
(compute / presentation / authoritative state), a normative crosswalk
from every capability to ADR-0004 operations, and a phase-split
transfer. The battery parses BOTH ADR front matters and proves chain
consistency; prose ownership claims are validated against the matrix,
not just name-checked. Mutations moving owners, omitting capabilities,
contradicting the matrix in prose, or giving the server or hosted BYOM
ownership fail."""

import re
from pathlib import Path

import yaml

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"
ADR = (ADR_DIR / "ADR-0005-platform-ownership.md").read_text()
ADR4 = (ADR_DIR / "ADR-0004-asymmetric-architecture.md").read_text()

DIMENSIONS = ["compute_owner", "presentation_owner",
              "authoritative_state_owner"]
DESKTOP_CAPS = ["pgn", "index", "stockfish", "models", "delta", "export"]
WEB_CAPS = ["queue", "diff", "approval", "quiet-week", "drills",
            "transfer"]
CROSSWALK = {
    "pgn": ["import"], "index": ["index"], "stockfish": ["stockfish"],
    "models": ["model-inference"], "delta": ["delta"],
    "export": ["export"], "queue": ["queue"], "diff": ["queue"],
    "approval": ["approval"], "quiet-week": ["training"],
    "drills": ["training"],
    "transfer": ["sync-encrypt", "sync-decrypt"],
}
SUB_CAPABILITY_OF = {"diff": "queue", "quiet-week": "training"}
TRANSFER_PHASES = {
    "sync-encrypt": {"actor": "desktop", "sends": "ciphertext"},
    "relay": {"actor": "server", "stores": "ciphertext-only",
              "decrypts": "never"},
    "sync-decrypt": {"actor": "web", "decrypts": "local-only"},
}
INVARIANTS = [
    "every-capability-has-exactly-one-presentation-owner",
    "compute-owner-is-desktop-for-every-non-transfer-capability",
    "authoritative-state-is-desktop-for-every-capability",
    "server-owns-no-capability",
    "transfer-splits-encrypt-desktop-decrypt-web",
    "hosted-byom-is-invocation-exception-never-ownership",
]
DENIED = [
    "desktop also owns",
    "server may decrypt",
    "may decrypt it",
    "BYOM owns",
    "provider owns",
    "inference by default",
]


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


def _matrix(fm) -> dict:
    return fm["capabilities"]


def _check(adr: str, adr4: str) -> None:
    fm, body, secs = _parse(adr)
    fm4, _, _ = _parse(adr4)
    assert fm["adr"] == "ADR-0005"
    assert fm["status"] == "proposed"
    assert fm["scope"] == "presentation-and-implementation-ownership"
    assert fm["dimensions"] == DIMENSIONS
    assert fm["invariants"] == INVARIANTS
    ops4 = fm4["operations"]

    caps = _matrix(fm)
    # exact capability set, incl. export accounted explicitly
    assert set(caps) == set(DESKTOP_CAPS) | set(WEB_CAPS)
    assert "export" in caps, "export must be accounted for explicitly"

    for cap, spec in caps.items():
        # dimensions complete, presentation owner exactly one actor
        for dim in DIMENSIONS:
            assert dim in spec, f"{cap}: missing {dim}"
        assert spec["presentation_owner"] in {"desktop", "web"}
        # authoritative state is desktop for every capability
        assert spec["authoritative_state_owner"] == "desktop", cap
        # the server owns no capability, on any dimension
        for dim in DIMENSIONS:
            assert spec[dim] != "server", f"{cap}: server {dim}"
        # compute is desktop for every non-transfer capability
        if cap != "transfer":
            assert spec["compute_owner"] == "desktop", cap
        # crosswalk pinned and targets exist in ADR-0004
        assert spec["crosswalk"] == CROSSWALK[cap], cap
        for op in spec["crosswalk"]:
            assert op in ops4, f"{cap}: {op} not an ADR-0004 operation"
        if cap != "transfer":
            # owner equality per mapped operation
            for op in spec["crosswalk"]:
                assert ops4[op] == spec["presentation_owner"], (
                    f"{cap}: ADR-0004 {op} owner {ops4[op]} != "
                    f"presentation owner {spec['presentation_owner']}")
        # sub-capability pins
        if cap in SUB_CAPABILITY_OF:
            assert spec.get("sub_capability_of") == SUB_CAPABILITY_OF[cap]
        else:
            assert "sub_capability_of" not in spec, cap

    # transfer phase split is normative and matches ADR-0004 owners
    phases = caps["transfer"]["phases"]
    assert phases == TRANSFER_PHASES
    assert caps["transfer"]["compute_owner"] == "split"
    assert phases["sync-encrypt"]["actor"] == ops4["sync-encrypt"]
    assert phases["sync-decrypt"]["actor"] == ops4["sync-decrypt"]
    assert phases["relay"]["actor"] == "server"
    assert phases["relay"]["decrypts"] == "never"
    assert phases["relay"]["stores"] == "ciphertext-only"

    # full coverage: every ADR-0004 operation is realized by a
    # capability; no operation maps to capabilities with conflicting
    # presentation owners (no ambiguous many-owner mapping)
    covered: dict[str, set] = {}
    for spec in caps.values():
        for op in spec["crosswalk"]:
            covered.setdefault(op, set()).add(spec["presentation_owner"])
    assert set(covered) == set(ops4), (
        f"uncovered ADR-0004 operations: {set(ops4) - set(covered)}")
    for op, owners in covered.items():
        assert len(owners) == 1, f"{op}: ambiguous owners {owners}"

    # prose: ownership sections mirror the matrix exactly
    assert "Status: proposed" in secs.get("__header__", "")
    desktop_sec = secs.get("Desktop owns (heavy compute, authoritative "
                           "data)", "")
    web_sec = secs.get("Web owns (review and training habit loop)", "")
    for cap in DESKTOP_CAPS:
        assert f"**{cap}**" in desktop_sec, f"{cap} missing from desktop"
        assert f"**{cap}**" not in web_sec, f"{cap} doubly presented"
    for cap in WEB_CAPS:
        assert f"**{cap}**" in web_sec, f"{cap} missing from web"
        assert f"**{cap}**" not in desktop_sec, f"{cap} doubly presented"
    # every prose ownership claim must match the matrix: scan for
    # "<actor> ... owns" assertions and validate the claimed capability
    claim_re = re.compile(
        r"(?i)\b(desktop|web|server|byom|provider|hosted byom)"
        r"[^.\n]{0,60}\bowns\b([^\n.]*)")
    for match in claim_re.finditer(body):
        actor = match.group(1).lower()
        object_text = match.group(2)
        # non-ownership statements ("owns no capability", "owns nothing")
        # are not ownership claims
        if re.match(r"\s*no\b", object_text) or "nothing" in object_text:
            continue
        claimed = set(re.findall(r"\*\*([a-z-]+)\*\*", object_text))
        assert actor not in {"server", "byom", "provider",
                             "hosted byom"}, (
            f"invalid ownership actor {actor}: {match.group(0)!r}")
        for cap in claimed:
            assert cap in caps and caps[cap]["presentation_owner"] == \
                actor, f"prose claim {actor} owns {cap} contradicts matrix"
    for clause in DENIED:
        assert clause not in body, f"contradiction clause: {clause}"
    # decision/scope/consequences consistency
    decision = secs.get("Decision", "")
    assert "front matter is" in decision and "normative" in decision
    ctx = secs.get("Context", "")
    assert "presentation-and-implementation ownership only" in ctx
    assert "ADR-0004 retains" in ctx
    alts = secs.get("Alternatives considered", "")
    assert "Shared ownership" in alts and "rejected" in alts
    assert "single web owner for transfer" in alts
    cons = secs.get("Consequences", "")
    assert "exactly one presentation owner" in cons
    assert "compute owner is desktop" in cons
    assert "Authoritative state is desktop" in cons
    assert "server owns no capability" in cons
    assert "never capability" in cons and "never a default" in cons
    assert "sync-encrypt on desktop" in web_sec
    assert "never decrypts" in web_sec


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
                     "    crosswalk: [export]\n", ""))
    # models moved to web
    _bad(ADR.replace("  models:\n    compute_owner: desktop\n"
                     "    presentation_owner: desktop",
                     "  models:\n    compute_owner: desktop\n"
                     "    presentation_owner: web"))
    # crosswalk target renamed away from ADR-0004 operations
    _bad(ADR.replace("crosswalk: [model-inference]",
                     "crosswalk: [llm-inference]"))


def test_dimension_mutations_fail():
    # a capability's authoritative state leaves the desktop
    _bad(ADR.replace("  queue:\n    compute_owner: desktop\n"
                     "    presentation_owner: web\n"
                     "    authoritative_state_owner: desktop",
                     "  queue:\n    compute_owner: desktop\n"
                     "    presentation_owner: web\n"
                     "    authoritative_state_owner: web"))
    # compute owner non-desktop on a non-transfer capability
    _bad(ADR.replace("  drills:\n    compute_owner: desktop",
                     "  drills:\n    compute_owner: web"))
    # server gains an ownership dimension
    _bad(ADR.replace("  pgn:\n    compute_owner: desktop",
                     "  pgn:\n    compute_owner: server"))
    # relay may decrypt
    _bad(ADR.replace("decrypts: never", "decrypts: allowed"))
    # invariant weakened
    _bad(ADR.replace("server-owns-no-capability",
                     "server-owns-few-capabilities"))


def test_prose_contradiction_mutations_fail():
    c1 = "\nThe desktop also owns queue and approval.\n"
    c2 = "\nThe server owns transfer and may decrypt it.\n"
    c3 = "\nHosted BYOM owns model inference by default.\n"
    for clause in (c1, c2, c3):
        _bad(ADR + clause)
    _bad(ADR + c1 + c2 + c3)


def test_chain_consistency_with_adr0004_mutations_fail():
    # an ADR-0004 operation disappearing breaks full coverage
    _bad(ADR, ADR4.replace("  sync-decrypt: web\n", ""))
    # an ADR-0004 owner flip breaks owner equality
    _bad(ADR, ADR4.replace("  queue: web\n", "  queue: desktop\n"))


def test_mutation_status_fails():
    _bad(ADR.replace("status: proposed", "status: accepted"))


def test_mutation_prose_rename_fails():
    _bad(ADR.replace("- **queue**", "- **backlog**"))
