"""T0027/T0028: deterministic render harness + pixel/a11y gates.

render_svg(spec) -> canonical SVG bytes (same spec, same bytes, always).
render_ppm(spec) -> ASCII PPM (P3) raster of the spec's grid (text
format so goldens diff as text). a11y_findings(spec) -> WCAG-flavored
rules: non-empty title and labels, text/background contrast >= 4.5, and
no color-only emphasis (a toned row must carry a text marker).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CELL = 8
BORDER = 1
MIN_CONTRAST = 4.5
HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text())
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: spec must be a JSON object")
    return spec


def _hex_rgb(color: str) -> tuple[int, int, int]:
    if not HEX_RE.match(color):
        raise ValueError(f"bad color {color!r} (want #rrggbb lowercase)")
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _luminance(color: str) -> float:
    def lin(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _hex_rgb(color)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _palette(spec: dict) -> dict[str, str]:
    pal = spec.get("palette")
    if not isinstance(pal, dict):
        raise ValueError("spec.palette must be an object")
    return {str(k): str(v) for k, v in pal.items()}


def render_svg(spec: dict) -> bytes:
    pal = _palette(spec)
    title = str(spec.get("title", ""))
    rows = spec.get("rows", [])
    grid = spec.get("grid", {})
    bg = pal.get("bg", "#ffffff")
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="240" '
        f'height="{40 + 24 * len(rows)}" role="img" aria-label="{title}">',
        f"  <title>{title}</title>",
        f'  <rect width="100%" height="100%" fill="{bg}"/>',
    ]
    for i, row in enumerate(rows):
        label = str(row.get("label", ""))
        text = str(row.get("text", ""))
        color = pal.get(str(row.get("color", "fg")), "#111111")
        lines.append(
            f'  <text x="16" y="{40 + 24 * i}" fill="{color}" '
            f'aria-label="{label}">{text}</text>')
    if grid:
        cols = int(grid.get("cols", 0))
        lines.append(f'  <g aria-label="grid" data-cols="{cols}"/>')
    lines.append("</svg>")
    return ("\n".join(lines) + "\n").encode()


def render_ppm(spec: dict) -> bytes:
    pal = _palette(spec)
    grid = spec.get("grid") or {}
    rows, cols = int(grid.get("rows", 0)), int(grid.get("cols", 0))
    colors = [str(c) for c in grid.get("colors", ["#ffffff", "#111111"])]
    bg = pal.get("bg", "#ffffff")
    w = cols * CELL + 2 * BORDER
    h = rows * CELL + 2 * BORDER
    px = [[bg for _ in range(w)] for _ in range(h)]
    for r in range(rows):
        for c in range(cols):
            color = colors[(r + c) % len(colors)]
            for dy in range(CELL):
                for dx in range(CELL):
                    px[BORDER + r * CELL + dy][BORDER + c * CELL + dx] = color
    out = [f"P3\n{w} {h}\n255\n"]
    for row in px:
        rgbs = ("{} {} {}".format(*_hex_rgb(c)) for c in row)
        out.append(" ".join(rgbs) + "\n")
    return "".join(out).encode()


def _parse_ppm(raw: bytes) -> tuple[int, int, int, list[tuple[int, ...]]]:
    """Strict P3 parse: magic, width, height, maxval, exactly w*h*3
    channels, every value in range. Anything else is invalid."""
    toks = raw.decode().split()
    if not toks or toks[0] != "P3":
        raise ValueError("not a P3 raster")
    if len(toks) < 4:
        raise ValueError("truncated P3 header")
    try:
        w, h, maxval = int(toks[1]), int(toks[2]), int(toks[3])
    except ValueError as e:
        raise ValueError(f"bad P3 header numbers: {e}") from e
    if w <= 0 or h <= 0 or not (0 < maxval <= 65535):
        raise ValueError(f"implausible P3 header {w}x{h} maxval {maxval}")
    nums = [int(t) for t in toks[4:]]
    if len(nums) != w * h * 3:
        raise ValueError(
            f"P3 channel count {len(nums)} != {w}*{h}*3 = {w * h * 3}")
    if any(v < 0 or v > maxval for v in nums):
        raise ValueError("P3 channel value out of range")
    px = [tuple(nums[i:i + 3]) for i in range(0, len(nums), 3)]
    return w, h, maxval, px


def pixel_diff(a: bytes, b: bytes) -> int:
    """Differing RGB triples between two P3 rasters; -1 when the geometry
    or maxval headers differ (a structural change is never 'no diff')."""
    wa, ha, ma, pa = _parse_ppm(a)
    wb, hb, mb, pb = _parse_ppm(b)
    if (wa, ha, ma) != (wb, hb, mb):
        return -1
    return sum(1 for x, y in zip(pa, pb, strict=True) if x != y)


def a11y_findings(spec: dict) -> list[str]:
    findings = []
    pal = _palette(spec)
    bg = pal.get("bg", "#ffffff")
    if not str(spec.get("title", "")).strip():
        findings.append("label: spec title is empty")
    for i, row in enumerate(spec.get("rows", [])):
        if not str(row.get("label", "")).strip():
            findings.append(f"label: row {i} has no label")
        color = pal.get(str(row.get("color", "fg")), "#111111")
        ratio = contrast(color, bg)
        if ratio < MIN_CONTRAST:
            findings.append(
                f"contrast: row {i} {ratio:.2f} < {MIN_CONTRAST}")
        if row.get("tone") and not str(row.get("marker", "")).strip():
            findings.append(f"color-only: row {i} tone has no text marker")
    return findings


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("svg", "ppm"):
        print("usage: render_harness.py {svg|ppm} SPEC", file=sys.stderr)
        return 2
    spec = load_spec(Path(sys.argv[2]))
    out = render_svg(spec) if sys.argv[1] == "svg" else render_ppm(spec)
    sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
