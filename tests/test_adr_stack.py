"""T0004: ADR compares two viable stacks on all required axes."""

from pathlib import Path

ADR = (Path(__file__).resolve().parent.parent / "docs/adr/ADR-0001-stack.md").read_text()


def test_two_viable_stacks_compared():
    assert "Option A: Rust core + Tauri shell" in ADR
    assert "Option B: Python core + Qt" in ADR


def test_all_required_axes_present_for_both_options():
    axes = ["Local-first", "Crash safety", "UI", "a11y", "Packaging", "Speed", "Contributor cost"]
    a, b = ADR.split("### Option B")
    for axis in axes:
        assert f"{axis}:" in a, f"axis {axis} missing for option A"
        assert f"{axis}:" in b, f"axis {axis} missing for option B"


def test_decision_recorded():
    assert "Proposed: Option A" in ADR
    assert "Consequences" in ADR
