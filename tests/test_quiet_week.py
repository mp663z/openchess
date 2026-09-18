"""T2234: quiet week shows the prepared state; no padding ever."""

from backtest.quiet_week import PREPARED_STATE, ScoredDelta, surface


def d(lid, score, conf):
    return ScoredDelta(line_id=lid, score=score, confidence=conf)


def test_zero_strong_deltas_gives_prepared_state():
    out = surface(
        [d("a", 0.1, 0.9), d("b", 0.2, 0.2)], score_threshold=0.5, confidence_threshold=0.5
    )
    assert out == PREPARED_STATE


def test_weak_items_never_pad():
    out = surface([d("a", 0.49, 0.99)], score_threshold=0.5, confidence_threshold=0.5, max_items=3)
    assert out == PREPARED_STATE


def test_strong_deltas_ranked_and_capped():
    ds = [d("a", 0.9, 0.9), d("b", 0.7, 0.9), d("c", 0.8, 0.9), d("d", 0.6, 0.9)]
    out = surface(ds, score_threshold=0.5, confidence_threshold=0.5, max_items=2)
    assert [x.line_id for x in out] == ["a", "c"]
