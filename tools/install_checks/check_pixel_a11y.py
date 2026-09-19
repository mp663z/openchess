"""T0028: pixel/a11y check - pixel-exact comparison and accessibility
rules must both bite.

Good mode: the good spec is pixel-exact against its golden and passes
every a11y rule (labels, contrast, no color-only emphasis).
Violation mode: a single flipped pixel in the raster is caught with an
exact diff count; each a11y violation fixture is caught by its intended
rule (contrast / label / color-only). A comparator with a tolerance or a
dead rule lets them through.
"""

from __future__ import annotations

from pathlib import Path

from tools import render_harness as rh
from tools.install_checks import CheckError

CHECK_ID = "T0028"
SCREENS = Path(__file__).resolve().parent / "fixtures" / "screens"
A11Y = Path(__file__).resolve().parent / "fixtures" / "a11y"
GOOD = "good_card.json"
VIOLATIONS = {
    "low_contrast.json": "contrast",
    "missing_label.json": "label",
    "color_only.json": "color-only",
}


def _flip_one_pixel(ppm: bytes) -> bytes:
    toks = ppm.decode().split("\n")
    header, rows = toks[:4], toks[4:]
    cells = rows[0].split(" ")
    cells[0], cells[1], cells[2] = ("0", "0", "0") if cells[0] != "0" \
        else ("255", "255", "255")
    rows[0] = " ".join(cells)
    return "\n".join(header + rows).encode()


def run(mode: str) -> None:
    if mode == "good":
        spec = rh.load_spec(SCREENS / GOOD)
        golden = (SCREENS / "good_card.ppm").read_bytes()
        diff = rh.pixel_diff(rh.render_ppm(spec), golden)
        if diff != 0:
            raise CheckError(f"good render differs from golden: {diff} pixels")
        findings = rh.a11y_findings(spec)
        if findings:
            raise CheckError(f"good spec fails a11y: {findings}")
        return
    problems = []
    golden = (SCREENS / "good_card.ppm").read_bytes()
    flipped = _flip_one_pixel(golden)
    if rh.pixel_diff(flipped, golden) != 1:
        problems.append("single flipped pixel not caught exactly")
    for name, rule in VIOLATIONS.items():
        findings = rh.a11y_findings(rh.load_spec(A11Y / name))
        rules = {f.split(":")[0] for f in findings}
        if rule not in rules:
            problems.append(f"{name}: rule {rule} did not fire")
        elif not findings:
            problems.append(f"{name}: no findings at all")
    if problems:
        return  # harness FAILS: a seeded violation escaped
    raise CheckError("1-pixel diff and every a11y rule violation caught")
