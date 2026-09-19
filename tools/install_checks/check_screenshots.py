"""T0027: screenshots check - renders must be deterministic and goldens
must match exactly.

Good mode: every spec renders to bytes identical to its tracked golden
(svg and ppm), and rendering twice is byte-identical (no clocks, no
randomness, no environment bleed).
Violation mode: the tampered spec must NOT match the good golden, with
the mismatch named; a missing golden is caught; a nondeterministic render
is caught.
"""

from __future__ import annotations

from pathlib import Path

from tools import render_harness as rh
from tools.install_checks import CheckError

CHECK_ID = "T0027"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "screens"
SPECS = ("good_card.json",)
TAMPERED = "tampered_card.json"


def _golden_pair(stem: str) -> tuple[Path, Path]:
    return FIXTURES / f"{stem}.svg", FIXTURES / f"{stem}.ppm"


def run(mode: str) -> None:
    if mode == "good":
        problems = []
        for name in SPECS:
            spec = rh.load_spec(FIXTURES / name)
            stem = name[:-5]
            svg_g, ppm_g = _golden_pair(stem)
            if not svg_g.exists() or not ppm_g.exists():
                problems.append(f"{stem}: golden missing")
                continue
            svg, ppm = rh.render_svg(spec), rh.render_ppm(spec)
            if svg != rh.render_svg(spec) or ppm != rh.render_ppm(spec):
                problems.append(f"{stem}: render not deterministic")
            if svg != svg_g.read_bytes():
                problems.append(f"{stem}: svg differs from golden")
            if ppm != ppm_g.read_bytes():
                problems.append(f"{stem}: ppm differs from golden")
        if problems:
            raise CheckError("; ".join(problems))
        return
    problems = []
    good = rh.load_spec(FIXTURES / SPECS[0])
    tampered = rh.load_spec(FIXTURES / TAMPERED)
    stem = SPECS[0][:-5]
    svg_g, ppm_g = _golden_pair(stem)
    if rh.render_svg(tampered) == svg_g.read_bytes():
        problems.append("tampered spec rendered identical svg to golden")
    if rh.render_ppm(tampered) == ppm_g.read_bytes():
        problems.append("tampered spec rendered identical ppm to golden")
    try:
        diff = rh.pixel_diff(rh.render_ppm(tampered), ppm_g.read_bytes())
    except ValueError:
        diff = -1  # invalid/structural: a difference, never "no diff"
    if diff == 0:
        problems.append("pixel_diff saw no difference in tampered render")
    if rh.render_svg(good) != svg_g.read_bytes():
        problems.append("control: good spec no longer matches its golden")
    if problems:
        return  # harness FAILS: a seeded tampering escaped
    raise CheckError(f"tampered render caught ({diff} pixels differ)")
