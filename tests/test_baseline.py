"""T2231: the naive baseline is exactly ECO-membership + 2500+ + recency."""

from datetime import date

from backtest.baseline import BaselineGame, naive_rank


def g(gid, eco, we, be, d):
    return BaselineGame(game_id=gid, eco=eco, white_elo=we, black_elo=be, played_at=d)


def test_rule_is_exact():
    rep = {"C45", "B90"}
    games = [
        g("a", "C45", 2600, 2400, date(2025, 3, 1)),  # in
        g("b", "C45", 2499, 2400, date(2025, 3, 2)),  # out: elo
        g("c", "A00", 2700, 2600, date(2025, 3, 3)),  # out: eco
        g("d", "B90", 2400, 2500, date(2025, 3, 4)),  # in: black 2500
        g("e", "C45", 2600, 2600, date(2025, 3, 5)),  # in, newest
    ]
    assert [x.game_id for x in naive_rank(games, rep)] == ["e", "d", "a"]


def test_sorted_by_recency_and_top_k():
    rep = {"C45"}
    games = [g(str(i), "C45", 2500, 2400, date(2025, 1, i)) for i in range(1, 10)]
    out = naive_rank(games, rep, top_k=3)
    assert [x.game_id for x in out] == ["9", "8", "7"]


def test_no_hidden_feature():
    """Fields outside the rule cannot change the output."""
    rep = {"C45"}
    base = [
        g("a", "C45", 2600, 2400, date(2025, 3, 1)),
        g("b", "A00", 2700, 2600, date(2025, 3, 3)),
    ]
    out1 = naive_rank(base, rep)
    padded = base + [g(f"x{i}", "A00", 2800, 2800, date(2025, 1, 1)) for i in range(50)]
    out2 = naive_rank(padded, rep)
    assert [x.game_id for x in out1] == [x.game_id for x in out2]
