"""T2794: ADR-0002 structural battery - the ADR is parsed into sections
and fields, and every decision-relevant field is asserted exactly: the
selected option, the PWA-only baseline, the no-native-binaries
constraint, the Status field, and every operational gate field
(metric definitions, cohort/window, baseline, numeric thresholds).
Mutation replays prove deleting or weakening any of them fails."""

from pathlib import Path

ADR = (Path(__file__).resolve().parent.parent / "docs/adr/ADR-0002-pwa-first.md").read_text()


def _sections(adr: str) -> dict[str, str]:
    """Parse the ADR into {section-title: body} on '## ' boundaries."""
    out: dict[str, str] = {}
    current = "__header__"
    out[current] = []
    for line in adr.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        else:
            out.setdefault(current, []).append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _check(adr: str) -> None:
    secs = _sections(adr)

    # Status field, exactly pinned in the document header
    assert "Status: proposed" in secs.get("__header__", "")

    # Options compared on the axes, per section (not whole-document search)
    assert "Option A: Responsive PWA" in adr
    assert "Option B: Native iOS + Android" in adr
    assert "Option C: WebView wrapper" in adr
    a, rest = adr.split("### Option B")
    b, c = rest.split("### Option C")
    for axis in ("Cost", "Offline", "Distribution"):
        assert f"{axis}:" in a, f"axis {axis} missing for option A"
        assert f"{axis}:" in b, f"axis {axis} missing for option B"
        assert f"{axis}:" in c, f"axis {axis} missing for option C"

    # Decision section: exact selected option and normative baseline
    decision = secs.get("Decision drivers and proposed choice", "")
    assert "Proposed: Option A" in decision
    assert "responsive PWA is the mobile baseline" in decision

    # Consequences: PWA-only baseline, no native binaries, addition-not-rewrite
    cons = secs.get("Consequences", "")
    assert "no native store binaries are built or promised" in cons
    assert "addition, not a rewrite" in cons
    assert "native-reopen gate" in cons

    # The operational gate: every field and threshold, exactly
    gate = secs.get("Native-reopen gate", "")
    assert "(a) Measurement definitions" in gate
    assert "install event in client telemetry" in gate
    assert "confirmed delivered by the client receipt" in gate
    assert "week-8 active usage" in gate
    assert "(b) Cohort and window" in gate
    assert "N = 30 users" in gate
    assert "W = 8 weeks" in gate
    assert "(c) Baseline" in gate
    assert "unaffected sub-cohort" in gate
    assert "(d) Reopen condition" in gate
    assert "install-completion below 70%" in gate
    assert "notification delivery below 80%" in gate
    assert "15 percentage points below" in gate
    assert "recorded in the decision evidence" in gate


def _bad(mutated: str) -> None:
    try:
        _check(mutated)
    except AssertionError:
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR)


def test_agpl_store_conflict_is_a_named_driver():
    assert "AGPL" in ADR
    assert "store" in ADR.lower()


def test_status_mutations_fail():
    _bad(ADR.replace("Status: proposed", "Status: accepted"))
    _bad(ADR.replace("Status: proposed", "Status: draft"))
    _bad(ADR.replace("Status: proposed\n", ""))


def test_decision_mutations_fail():
    _bad(ADR.replace("Proposed: Option A", "Proposed: Option B"))
    _bad(ADR.replace("responsive PWA is the mobile baseline",
                     "native app is the mobile baseline"))
    _bad(ADR.replace("no native store binaries are built or promised",
                     "native store binaries ship alongside"))
    _bad(ADR.replace("addition, not a rewrite", "rewrite, not an addition"))


def test_gate_threshold_mutations_fail():
    _bad(ADR.replace("install-completion below 70%", "install-completion below 95%"))
    _bad(ADR.replace("install-completion below 70%", "install-completion below 60%"))
    _bad(ADR.replace("notification delivery below 80%", "notification delivery below 50%"))
    _bad(ADR.replace("15 percentage points below", "5 percentage points below"))
    _bad(ADR.replace("15 percentage points below", "any amount below"))
    _bad(ADR.replace("N = 30 users", "N = 3 users"))
    _bad(ADR.replace("W = 8 weeks", "W = 1 week"))


def test_gate_field_deletions_fail():
    _bad(ADR.replace("(a) Measurement definitions", "(a) Vibes"))
    _bad(ADR.replace("install event in client telemetry", "user says so"))
    _bad(ADR.replace("(b) Cohort and window", "(b) Whenever"))
    _bad(ADR.replace("(c) Baseline", "(c) No comparison"))
    _bad(ADR.replace("unaffected sub-cohort", "whole cohort"))
    _bad(ADR.replace("(d) Reopen condition", "(d) Whenever it feels right"))
    _bad(ADR.replace("recorded in the decision evidence", "remembered informally"))


def test_anecdote_gate_fails():
    """The verifier's mutation: replacing the substantive gate with
    'any anecdote whatsoever' must fail every gate assertion."""
    start = ADR.index("## Native-reopen gate")
    end = ADR.index("## Consequences")
    gutted = (ADR[:start]
              + "## Native-reopen gate\n\nAny anecdote whatsoever reopens native.\n\n"
              + ADR[end:])
    assert "week-8" not in _sections(gutted)["Native-reopen gate"]
    _bad(gutted)


def test_gate_section_deleted_fails():
    start = ADR.index("## Native-reopen gate")
    end = ADR.index("## Consequences")
    _bad(ADR[:start] + ADR[end:])
