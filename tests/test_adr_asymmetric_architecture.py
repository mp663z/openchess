"""T2792: ADR-0004 structural battery - the asymmetric-split roles and
invariants are STRUCTURED front matter, exactly pinned; prose is
checked for consistency. Mutations weakening any role or invariant
fail."""

from pathlib import Path

import yaml

ADR = (Path(__file__).resolve().parent.parent / "docs/adr"
       / "ADR-0004-asymmetric-architecture.md").read_text()

ROLES = {
    "desktop_core": "authoritative-compute-and-data-engine",
    "web_pwa": "review-and-training-habit-surface",
    "server": "zero-knowledge-ciphertext-relay",
    "export": "interoperability-feature",
}
INVARIANTS = [
    "desktop-runs-full-loop-offline",
    "web-carries-no-heavy-compute",
    "server-stores-no-plaintext",
    "export-is-a-feature-not-the-phone-story",
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
    assert fm["adr"] == "ADR-0004"
    assert fm["status"] == "proposed"
    assert fm["roles"] == ROLES
    assert fm["invariants"] == INVARIANTS

    assert "Status: proposed" in secs.get("__header__", "")
    assert "Option A: Asymmetric split" in adr
    assert "Option B: Symmetric full stack" in adr
    assert "Option C: Server-centric" in adr
    a, rest = adr.split("### Option B")
    b, c = rest.split("### Option C")
    for axis in ("Authority", "Cost", "Privacy", "Habit", "Interoperability"):
        assert f"{axis}:" in a, f"axis {axis} missing for option A"
        assert f"{axis}:" in b, f"axis {axis} missing for option B"
        assert f"{axis}:" in c, f"axis {axis} missing for option C"
    decision = secs.get("Decision drivers and proposed choice", "")
    assert "Proposed: Option A" in decision
    assert "authoritative compute and data engine" in decision
    assert "export is interoperability" in decision
    assert "front matter are normative" in decision
    cons = secs.get("Consequences", "")
    assert "full loop offline" in cons
    assert "no heavy compute" in cons
    assert "ciphertext only" in cons
    assert "never the phone story" in cons


def _bad(mutated: str) -> None:
    try:
        _check(mutated)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR)


def test_role_mutations_fail():
    _bad(ADR.replace("desktop_core: authoritative-compute-and-data-engine",
                     "desktop_core: one-engine-among-peers"))
    _bad(ADR.replace("web_pwa: review-and-training-habit-surface",
                     "web_pwa: full-compute-surface"))
    _bad(ADR.replace("server: zero-knowledge-ciphertext-relay",
                     "server: hosted-compute-authority"))
    _bad(ADR.replace("export: interoperability-feature",
                     "export: the-phone-story"))
    _bad(ADR.replace("  web_pwa: review-and-training-habit-surface\n", ""))


def test_invariant_mutations_fail():
    _bad(ADR.replace("desktop-runs-full-loop-offline",
                     "desktop-needs-network-for-import"))
    _bad(ADR.replace("web-carries-no-heavy-compute",
                     "web-carries-engine-compute"))
    _bad(ADR.replace("server-stores-no-plaintext", "server-may-read-content"))
    _bad(ADR.replace("export-is-a-feature-not-the-phone-story",
                     "export-is-the-phone-story"))
    _bad(ADR.replace("  - server-stores-no-plaintext\n", ""))
    _bad(ADR.replace("invariants:\n", "invariants: []\n_gone:\n"))


def test_status_and_decision_mutations_fail():
    _bad(ADR.replace("status: proposed", "status: accepted"))
    _bad(ADR.replace("Status: proposed", "Status: accepted"))
    _bad(ADR.replace("Proposed: Option A", "Proposed: Option C"))
    _bad(ADR.replace("front matter are normative", "prose is normative"))


def test_front_matter_deleted_fails():
    end = ADR.index("---\n", 4) + 4
    _bad(ADR[end:])
