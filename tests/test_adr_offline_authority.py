"""T2795: ADR-0003 fixes the offline authority model - desktop core is
sole authority, the habit surface has an explicit three-state offline
machine, and reconnect conflicts are surfaced, never silently resolved."""

from pathlib import Path

ADR = (Path(__file__).resolve().parent.parent / "docs/adr"
      / "ADR-0003-offline-authority.md").read_text()


def test_three_authority_options_compared():
    assert "Option A: Desktop core is the sole authority" in ADR
    assert "Option B: Server as authority" in ADR
    assert "Option C: Peer-to-peer authority with CRDT merge" in ADR


def test_all_required_axes_present_for_each_option():
    axes = ["Offline desktop", "Offline web state", "Reconnect conflicts"]
    a, rest = ADR.split("### Option B")
    b, c = rest.split("### Option C")
    for axis in axes:
        assert f"{axis}:" in a, f"axis {axis} missing for option A"
        assert f"{axis}:" in b, f"axis {axis} missing for option B"
        assert f"{axis}:" in c, f"axis {axis} missing for option C"


def test_acceptance_language_mirrored():
    """Acceptance: offline desktop remains complete; offline web state
    and reconnect conflicts are explicit."""
    assert "complete" in ADR
    assert "OFFLINE-CACHED" in ADR
    assert "OFFLINE-QUEUED" in ADR
    assert "ONLINE" in ADR
    assert "never silent" in ADR or "never picks" in ADR


def test_zero_knowledge_and_no_silent_writes_are_drivers():
    assert "zero-knowledge" in ADR
    assert "no-silent-writes" in ADR


def test_decision_recorded():
    assert "Proposed: Option A" in ADR
    assert "sole authority for the document of record" in ADR
    assert "Consequences" in ADR
