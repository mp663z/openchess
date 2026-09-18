"""T2237 - Chronological replay harness (2024-2025 window).

Causality contract: at every weekly decision point the scoring inputs are
built ONLY from events with event_time < decision_time. Labels (30-day
forward outcomes) are computed from future events at EXPORT time and never
enter scoring inputs. The harness emits every score, every action, and
every 30-day label in an audit log so completeness is checkable.

Leakage enforcement is structural: the harness owns the event store and
hands the input-builder a read-only past-view; the builder cannot reach
future events because it never receives them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

LABEL_WINDOW_DAYS = 30


@dataclass(frozen=True)
class Event:
    event_time: date
    kind: str  # e.g. "line_game", "repertoire_change", "elite_move"
    line: str
    payload: dict[str, Any]


@dataclass
class DecisionRecord:
    decision_time: date
    line: str
    score: float
    action: str  # "surfaced" | "held"
    inputs: dict[str, Any]


@dataclass
class LabelRecord:
    decision_time: date
    line: str
    label: str  # "useful" | "reverted" | "no_change"
    label_window_days: int = LABEL_WINDOW_DAYS


@dataclass
class ReplayLog:
    decisions: list[DecisionRecord] = field(default_factory=list)
    labels: list[LabelRecord] = field(default_factory=list)


class LeakageError(Exception):
    pass


class ReplayHarness:
    def __init__(self, events: list[Event]):
        for e in events:
            if not isinstance(e.event_time, date):
                raise TypeError("event_time must be a date")
        self._events = sorted(events, key=lambda e: e.event_time)

    def past_view(self, decision_time: date) -> tuple[Event, ...]:
        """Read-only, strictly-past view: event_time < decision_time."""
        return tuple(e for e in self._events if e.event_time < decision_time)

    def future_view(self, decision_time: date, days: int = LABEL_WINDOW_DAYS) -> tuple[Event, ...]:
        """Events in (decision_time, decision_time + days]; labels only."""
        end = decision_time + timedelta(days=days)
        return tuple(e for e in self._events if decision_time < e.event_time <= end)

    def weekly_decision_points(self, start: date, end: date) -> list[date]:
        points = []
        t = start
        while t <= end:
            points.append(t)
            t += timedelta(days=7)
        return points

    def run(
        self,
        start: date,
        end: date,
        lines: list[str],
        build_inputs,
        score_fn,
        surface_fn,
    ) -> ReplayLog:
        """Run the replay. build_inputs(past_view, line, decision_time) ->
        inputs mapping; score_fn(inputs) -> float; surface_fn(score) -> bool.
        Every decision and every 30-day label lands in the audit log."""
        log = ReplayLog()
        for dt in self.weekly_decision_points(start, end):
            past = self.past_view(dt)
            future = self.future_view(dt)
            if future and min(future, key=lambda e: e.event_time).event_time <= dt:
                raise LeakageError("future view contains non-future event")
            if past and max(past, key=lambda e: e.event_time).event_time >= dt:
                raise LeakageError("past view contains non-past event")
            for line in lines:
                inputs = build_inputs(past, line, dt)
                score = float(score_fn(inputs))
                surfaced = bool(surface_fn(score))
                log.decisions.append(
                    DecisionRecord(dt, line, score, "surfaced" if surfaced else "held", inputs)
                )
                if surfaced:
                    log.labels.append(LabelRecord(dt, line, self._label(dt, line)))
        return log

    def _label(self, decision_time: date, line: str) -> str:
        future = self.future_view(decision_time)
        changes = [
            e for e in future
            if e.kind == "repertoire_change" and e.line == line
        ]
        if not changes:
            return "no_change"
        first = min(changes, key=lambda e: e.event_time)
        reverts = [
            e for e in changes
            if e.event_time > first.event_time
            and e.payload.get("reverts") == first.payload.get("id")
        ]
        return "reverted" if reverts else "useful"

    @staticmethod
    def export(log: ReplayLog) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "decisions": [asdict(d) for d in log.decisions],
                "labels": [asdict(lb) for lb in log.labels],
            },
            default=str,
            indent=2,
        )
