"""T2235 - Delta scoring equation (v5 section 7 / Appendix D).

S(line) = (wT*T + wE*E + wD*D + wR*R + wA*A) * F * G, components in [0,1].
Band attenuation: T and E scaled down below 2200, R and D renormalised so
effective weights still sum to 1. Every input is versioned (spec YAML).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

SPEC_PATH = Path(__file__).resolve().parent / "spec" / "scoring-v0.1.yaml"

BANDS = ("1800-1999", "2000-2199", "2200-2399", "2400+")


@dataclass(frozen=True)
class ScoringInputs:
    game_count: int  # quality-weighted new games in the line this window
    trailing_year_baseline: float  # line's own trailing-year mean of game_count
    adopter_strength: float  # net elite move-switching at the tabiya, [-1, 1]
    elite_n: int  # elite sample size behind adopter_strength
    engine_drift: float  # pawn units at the user's tabiya since last review
    personal_score: float | None  # user's line score, trailing 90d, [0,1]; None if <5 games
    overall_score: float  # user's overall score, trailing 90d, [0,1]
    review_age_days: int
    user_rating: int
    line_frequency: float  # own-game occurrences, trailing year
    repertoire_median_frequency: float
    evidence_quality: float = 1.0  # [0,1] quality weight on the window's games


def band_of(rating: int) -> str:
    if rating < 2000:
        return "1800-1999"
    if rating < 2200:
        return "2000-2199"
    if rating < 2400:
        return "2200-2399"
    return "2400+"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def norm_theory_activity(game_count: int, baseline: float, evidence_quality: float) -> float:
    """Window games vs the line's own trailing-year baseline, quality-weighted."""
    quality = game_count * _clamp(evidence_quality)
    return _clamp(quality / (4.0 * baseline)) if baseline > 0 else _clamp(quality / 4.0)


def norm_elite_adoption(adopter_strength: float, elite_n: int) -> float:
    """Net switching mapped [-1,1]->[0,1]; significance-guarded at low n."""
    if elite_n < 5:
        return 0.5  # uninformative midpoint below significance floor
    return _clamp((adopter_strength + 1.0) / 2.0)


def norm_engine_drift(drift_pawns: float) -> float:
    return _clamp(abs(drift_pawns) / 0.5)  # capped at 0.5 pawn per Appendix D


def norm_own_results(personal_score: float | None, overall_score: float) -> float:
    """Underperformance maps high. Suppressed (0) below the 5-game floor."""
    if personal_score is None:
        return 0.0
    return _clamp(0.5 + (overall_score - personal_score))


def norm_review_age(days: int) -> float:
    return _clamp(days / 365.0)


def f_line_frequency(line_frequency: float, median_frequency: float) -> float:
    if median_frequency <= 0:
        return 1.0
    return max(0.25, min(2.0, line_frequency / median_frequency))


def g_attenuation(band: str, weights: dict[str, float]) -> dict[str, float]:
    """Apply the band's T/E factor; renormalise R and D upward to sum 1."""
    factor = {"2000-2199": 0.6, "1800-1999": 0.4}.get(band, 1.0)
    w = dict(weights)
    w["T_theory_activity"] *= factor
    w["E_elite_adoption"] *= factor
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def load_spec(path: Path = SPEC_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def score(inputs: ScoringInputs, spec: dict | None = None) -> dict:
    """Compute S with full arithmetic exposed (the 'show me the arithmetic' contract)."""
    spec = spec or load_spec()
    w_ref = {k: float(v) for k, v in spec["weights_reference_band_2200plus"].items()}
    band = band_of(inputs.user_rating)
    w = g_attenuation(band, w_ref)
    components = {
        "T_theory_activity": norm_theory_activity(
            inputs.game_count, inputs.trailing_year_baseline, inputs.evidence_quality
        ),
        "E_elite_adoption": norm_elite_adoption(inputs.adopter_strength, inputs.elite_n),
        "D_engine_drift": norm_engine_drift(inputs.engine_drift),
        "R_own_results": norm_own_results(inputs.personal_score, inputs.overall_score),
        "A_review_age": norm_review_age(inputs.review_age_days),
    }
    base = sum(w[k] * components[k] for k in w)
    f = f_line_frequency(inputs.line_frequency, inputs.repertoire_median_frequency)
    s = base * f  # G is already folded into the renormalised weights
    return {
        "score": s,
        "band": band,
        "weights": w,
        "components": components,
        "F": f,
        "base": base,
        "spec_version": spec["version"],
        "arithmetic": "S = ("
        + " + ".join(f"{w[k]:.4f}*{components[k]:.4f}" for k in w)
        + f") * {f:.4f} = {s:.6f}",
    }


def cold_start_threshold(scores: list[float]) -> float:
    """75th percentile of the score distribution (Appendix D)."""
    if not scores:
        return math.inf
    ordered = sorted(scores)
    idx = math.ceil(0.75 * len(ordered)) - 1
    return ordered[max(0, idx)]
