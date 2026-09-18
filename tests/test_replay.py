"""T2237: chronological replay - no future leakage, every score/action/label emitted."""

from datetime import date, timedelta

import pytest

from backtest.replay import Event, LabelRecord, LeakageError, ReplayHarness

START = date(2024, 1, 1)
END = date(2024, 2, 1)  # 5 weekly decision points
LINES = ["e4-mainline", "d4-london"]


def game_events():
    evs = []
    t = START - timedelta(days=60)
    while t < END + timedelta(days=40):
        evs.append(Event(t, "line_game", "e4-mainline", {"n": 1}))
        if t.day % 2:
            evs.append(Event(t, "line_game", "d4-london", {"n": 1}))
        t += timedelta(days=1)
    return evs


def build_inputs(past, line, dt):
    return {"game_count": sum(1 for e in past if e.kind == "line_game" and e.line == line)}


def score_fn(inputs):
    return min(1.0, inputs["game_count"] / 100.0)


def surface_fn(score):
    return score >= 0.5


def test_every_score_and_action_emitted():
    h = ReplayHarness(game_events())
    log = h.run(START, END, LINES, build_inputs, score_fn, surface_fn)
    points = h.weekly_decision_points(START, END)
    assert len(log.decisions) == len(points) * len(LINES)
    assert all(d.action in ("surfaced", "held") for d in log.decisions)
    assert all(0.0 <= d.score <= 1.0 for d in log.decisions)


def test_no_future_leakage_structural():
    h = ReplayHarness(game_events())
    for dt in h.weekly_decision_points(START, END):
        past = h.past_view(dt)
        assert all(e.event_time < dt for e in past)
        future = h.future_view(dt)
        assert all(dt < e.event_time <= dt + timedelta(days=30) for e in future)


def test_future_events_never_reach_scoring():
    h = ReplayHarness(game_events())
    seen_counts = []

    def spying_builder(past, line, dt):
        seen_counts.append((dt, line, len(past)))
        return build_inputs(past, line, dt)

    h.run(START, END, LINES, spying_builder, score_fn, surface_fn)
    # Past view must grow monotonically and never include same-day/future events.
    for dt, _line, _n in seen_counts:
        assert all(e.event_time < dt for e in h.past_view(dt))
    # And shuffling future events must not change any score.
    log1 = h.run(START, END, LINES, build_inputs, score_fn, surface_fn)
    evs = game_events()
    future_first = sorted(evs, key=lambda e: -e.event_time.toordinal())
    h2 = ReplayHarness(future_first)
    log2 = h2.run(START, END, LINES, build_inputs, score_fn, surface_fn)
    assert [d.score for d in log1.decisions] == [d.score for d in log2.decisions]
    assert [d.action for d in log1.decisions] == [d.action for d in log2.decisions]


def test_labels_emitted_for_every_surfaced_action():
    evs = game_events()
    # Force surfacing from the start and add a repertoire change + revert.
    evs.append(Event(START + timedelta(days=3), "repertoire_change", "e4-mainline",
                     {"id": "c1", "reverts": None}))
    evs.append(Event(START + timedelta(days=10), "repertoire_change", "e4-mainline",
                     {"id": "c2", "reverts": "c1"}))
    h = ReplayHarness(evs)
    log = h.run(START, END, LINES, build_inputs, score_fn, lambda s: True)
    surfaced = [d for d in log.decisions if d.action == "surfaced"]
    assert len(log.labels) == len(surfaced)  # a label for EVERY surfaced action
    assert all(isinstance(lb, LabelRecord) and lb.label_window_days == 30 for lb in log.labels)
    labels_first_week = [
        lb for lb in log.labels if lb.decision_time == START and lb.line == "e4-mainline"
    ]
    assert labels_first_week[0].label == "reverted"
    labels_london = [
        lb for lb in log.labels if lb.decision_time == START and lb.line == "d4-london"
    ]
    assert labels_london[0].label == "no_change"


def test_useful_label_when_change_sticks():
    evs = [Event(START + timedelta(days=5), "repertoire_change", "e4-mainline",
                 {"id": "c1", "reverts": None})]
    h = ReplayHarness(evs)
    log = h.run(START, START, ["e4-mainline"], build_inputs, score_fn, lambda s: True)
    assert log.labels[0].label == "useful"


def test_export_schema_and_determinism():
    h = ReplayHarness(game_events())
    out1 = ReplayHarness.export(h.run(START, END, LINES, build_inputs, score_fn, surface_fn))
    out2 = ReplayHarness.export(h.run(START, END, LINES, build_inputs, score_fn, surface_fn))
    assert out1 == out2
    assert '"schema_version": 1' in out1


def test_leakage_error_type_raised_on_bad_view():
    class CorruptHarness(ReplayHarness):
        def future_view(self, dt, days=30):
            return (Event(dt - timedelta(days=1), "line_game", "x", {}),)

    bad = CorruptHarness(game_events())
    with pytest.raises(LeakageError):
        bad.run(START, START, ["e4-mainline"], build_inputs, score_fn, surface_fn)
