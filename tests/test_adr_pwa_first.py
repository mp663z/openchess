"""T2794: ADR-0002 compares viable mobile shells on all required axes and
records the responsive-PWA baseline with an explicit native-reopen gate."""

from pathlib import Path

ADR = (Path(__file__).resolve().parent.parent / "docs/adr/ADR-0002-pwa-first.md").read_text()


def test_three_shell_options_compared():
    assert "Option A: Responsive PWA" in ADR
    assert "Option B: Native iOS + Android" in ADR
    assert "Option C: WebView wrapper" in ADR


def test_all_required_axes_present_for_each_option():
    axes = ["Cost", "Offline", "Distribution"]
    a, rest = ADR.split("### Option B")
    b, c = rest.split("### Option C")
    for axis in axes:
        assert f"{axis}:" in a, f"axis {axis} missing for option A"
        assert f"{axis}:" in b, f"axis {axis} missing for option B"
        assert f"{axis}:" in c, f"axis {axis} missing for option C"


def test_agpl_store_conflict_is_a_named_driver():
    assert "AGPL" in ADR
    assert "store" in ADR.lower()


def test_decision_and_reopen_gate_recorded():
    assert "Proposed: Option A" in ADR
    assert "responsive PWA is the mobile baseline" in ADR
    assert "deferred until later evidence" in ADR
    assert "Consequences" in ADR


def test_ui_neutrality_preserved():
    """A later native shell must be an addition, not a rewrite."""
    assert "addition, not a rewrite" in ADR
