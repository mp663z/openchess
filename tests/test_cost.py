"""T2233: precision-recall-cost frontier from review/mute/next-open outcomes."""

from backtest.cost import SurfacedOutcome, frontier, frontier_point


def mk(rel, minutes=10.0, **kw):
    return SurfacedOutcome(item_id="x", decision_relevant=rel, review_minutes=minutes, **kw)


def test_frontier_point_math():
    scored = [
        (0.9, mk(True)),
        (0.8, mk(True, muted=True, next_open_delay_days=6.0)),
        (0.7, mk(False, minutes=5.0)),
        (0.1, mk(True)),  # below threshold: counts toward recall denominator only
    ]
    p = frontier_point(0.7, scored)
    assert abs(p.precision - 2 / 3) < 1e-9
    assert abs(p.recall - 2 / 3) < 1e-9
    assert abs(p.minutes_per_relevant - 12.5) < 1e-9
    assert abs(p.mute_rate - 1 / 3) < 1e-9
    assert abs(p.mean_next_open_delay - 2.0) < 1e-9


def test_empty_shown_is_safe():
    p = frontier_point(0.99, [(0.1, mk(True))])
    assert p.precision == 1.0 and p.recall == 0.0


def test_frontier_is_ordered():
    pts = frontier([0.5, 0.9], [(0.6, mk(True)), (0.95, mk(False))])
    assert [p.threshold for p in pts] == [0.5, 0.9]
