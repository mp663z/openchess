"""T2232: useful = accepted change still present after 30 days; proxies separate."""

from datetime import date

from backtest.labels import AcceptedChange, ProxyLabel, RepertoireSnapshot, useful_label


def ch():
    return AcceptedChange(
        change_id="c1", line_id="L1", accepted_at=date(2025, 5, 1), change_payload_hash="h1"
    )


def test_present_after_30_days_is_useful():
    snaps = [RepertoireSnapshot(taken_at=date(2025, 6, 1), present_change_hashes=frozenset({"h1"}))]
    assert useful_label(ch(), snaps) is True


def test_absent_after_30_days_is_not_useful():
    snaps = [RepertoireSnapshot(taken_at=date(2025, 6, 5), present_change_hashes=frozenset())]
    assert useful_label(ch(), snaps) is False


def test_no_snapshot_at_window_is_unknown_not_guessed():
    snaps = [
        RepertoireSnapshot(taken_at=date(2025, 5, 15), present_change_hashes=frozenset({"h1"}))
    ]
    assert useful_label(ch(), snaps) is None


def test_proxy_labels_are_a_separate_kind():
    p = ProxyLabel(kind="engine_reanalysis", item_id="i1", decision_relevant=True)
    assert p.kind != "useful"
