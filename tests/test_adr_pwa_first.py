"""T2794: ADR-0002 structural battery - the native-reopen gate is
STRUCTURED DATA (YAML front matter), evaluated with a truth table, and
the prose is checked for exact consistency with it. Mutation replays
prove semantic changes (ALL->ANY, flipped operator structure, dropped
prohibition, dropped subgroup floors) all fail."""

from pathlib import Path

import yaml

ADR = (Path(__file__).resolve().parent.parent / "docs/adr/ADR-0002-pwa-first.md").read_text()


def _parse(adr: str):
    assert adr.startswith("---\n"), "YAML front matter required"
    fm = yaml.safe_load(adr.split("---\n", 2)[1])
    body = adr.split("---\n", 2)[2]
    secs: dict[str, str] = {}
    current = "__header__"
    secs[current] = []
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            secs[current] = []
        else:
            secs.setdefault(current, []).append(line)
    return fm, {k: "\n".join(v) for k, v in secs.items()}


def _eval(node, facts: dict) -> bool:
    """Evaluate the gate's reopen expression tree against scenario facts."""
    if isinstance(node, str):
        return facts[node]
    if isinstance(node, dict):
        if "and" in node:
            return all(_eval(c, facts) for c in node["and"])
        if "or" in node:
            return any(_eval(c, facts) for c in node["or"])
    raise AssertionError(f"unknown expression node {node!r}")


def _reopen(fm: dict, install_pct: float, delivery_pct: float,
            gap_points: float, cohort: int = 30, weeks: int = 8,
            affected: int = 15, unaffected: int = 15):
    """Full gate evaluation: sample evaluability, then the expression
    over facts derived from the pinned thresholds."""
    g = fm["gate"]
    sample = g["sample"]
    if cohort < sample["cohort_min_users"] or weeks < sample["window_min_weeks"]:
        return "unevaluable"
    if (affected < sample["subgroup_min_users"]
            or unaffected < sample["subgroup_min_users"]):
        assert sample["subgroup_fallback"] == "gate-unevaluable-cannot-reopen-native"
        return "unevaluable"
    t = g["thresholds"]
    facts = {
        "install_completion_below_threshold": install_pct < t["install_completion_pct"],
        "notification_delivery_below_threshold": delivery_pct < t["notification_delivery_pct"],
        "retention_gap_at_least_threshold": gap_points >= t["retention_gap_points"],
    }
    return bool(_eval(g["reopen_expression"], facts))


def _check(adr: str) -> None:
    fm, secs = _parse(adr)
    g = fm["gate"]

    # front matter, exactly pinned
    assert fm["adr"] == "ADR-0002"
    assert fm["status"] == "proposed"
    assert g["combiner"] == "ALL"
    assert g["metrics"] == {
        "install_completion": "install-event-in-client-telemetry",
        "notification_delivery": "client-delivery-receipt",
        "retention": "week-8-active-usage-completed-review-session",
    }
    assert g["sample"] == {
        "cohort_min_users": 30,
        "window_min_weeks": 8,
        "subgroup_min_users": 10,
        "subgroup_fallback": "gate-unevaluable-cannot-reopen-native",
    }
    assert g["reopen_expression"] == {
        "and": [
            {"or": ["install_completion_below_threshold",
                    "notification_delivery_below_threshold"]},
            "retention_gap_at_least_threshold",
        ]
    }
    assert g["thresholds"] == {"install_completion_pct": 70,
                               "notification_delivery_pct": 80,
                               "retention_gap_points": 15}
    assert g["prohibition"] == "anecdotes-never-satisfy"

    # truth table over the structured gate
    assert _reopen(fm, 69, 90, 20) is True      # install-only failure
    assert _reopen(fm, 90, 79, 20) is True      # delivery-only failure
    assert _reopen(fm, 69, 79, 20) is True      # both metrics fail
    assert _reopen(fm, 90, 90, 20) is False     # retention gap alone
    assert _reopen(fm, 69, 90, 14) is False     # gap under threshold
    assert _reopen(fm, 69, 90, 15) is True      # gap exactly at threshold
    assert _reopen(fm, 70, 90, 20) is False     # install exactly at threshold
    assert _reopen(fm, 90, 80, 20) is False     # delivery exactly at threshold
    assert _reopen(fm, 69, 90, 20, cohort=29) == "unevaluable"
    assert _reopen(fm, 69, 90, 20, weeks=7) == "unevaluable"
    assert _reopen(fm, 0, 90, 20, unaffected=0) == "unevaluable"
    assert _reopen(fm, 69, 90, 20, affected=9) == "unevaluable"

    # prose consistency: the gate section mirrors the structured data
    gate_prose = secs.get("Native-reopen gate", "")
    assert "only when ALL" in gate_prose
    grouped = ("(PWA install completion is below 70% OR opted-in "
               "notification delivery is below 80%) AND")
    assert grouped in gate_prose.replace("\n  ", " ")
    assert "at least 15 percentage points below" in gate_prose
    assert "at least 10 users" in gate_prose
    assert "unevaluable and cannot reopen native" in gate_prose
    assert "never\nsatisfy this gate" in gate_prose or "never satisfy this gate" in gate_prose
    assert "N = 30 users" in gate_prose
    assert "W = 8 weeks" in gate_prose
    assert "install event in client telemetry" in gate_prose
    assert "client receipt" in gate_prose
    assert "completed review session" in gate_prose
    assert "recorded in the decision evidence" in gate_prose
    assert "front matter is normative" in gate_prose

    # document structure from v2 (approved): status, options, axes, decision
    assert "Status: proposed" in secs.get("__header__", "")
    assert "Option A: Responsive PWA" in adr
    assert "Option B: Native iOS + Android" in adr
    assert "Option C: WebView wrapper" in adr
    a, rest = adr.split("### Option B")
    b, c = rest.split("### Option C")
    for axis in ("Cost", "Offline", "Distribution"):
        assert f"{axis}:" in a, f"axis {axis} missing for option A"
        assert f"{axis}:" in b, f"axis {axis} missing for option B"
        assert f"{axis}:" in c, f"axis {axis} missing for option C"
    decision = secs.get("Decision drivers and proposed choice", "")
    assert "Proposed: Option A" in decision
    assert "responsive PWA is the mobile baseline" in decision
    cons = secs.get("Consequences", "")
    assert "no native store binaries are built or promised" in cons
    assert "addition, not a rewrite" in cons
    assert "native-reopen gate" in cons


def _bad(mutated: str) -> None:
    try:
        _check(mutated)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR)


def test_gate_truth_table_standalone():
    """The shipped gate evaluates correctly (also covers finding 1's
    required cases directly against the real front matter)."""
    fm, _ = _parse(ADR)
    assert _reopen(fm, 69, 90, 20) is True
    assert _reopen(fm, 90, 79, 20) is True
    assert _reopen(fm, 90, 90, 20) is False
    assert _reopen(fm, 69, 79, 20) is True


def test_semantic_mutations_fail():
    """The verifier's exact semantic mutations must fail."""
    _bad(ADR.replace("combiner: ALL", "combiner: ANY"))
    _bad(ADR.replace("reopens only when ALL", "reopens when ANY"))
    _bad(ADR.replace(
        "      - or: [install_completion_below_threshold, notification_delivery_below_threshold]\n",
        "      - install_completion_below_threshold\n"))
    _bad(ADR.replace("  reopen_expression:\n    and:",
                     "  reopen_expression:\n    or:"))
    _bad(ADR.replace(
        "or: [install_completion_below_threshold, "
        "notification_delivery_below_threshold]",
        "and: [install_completion_below_threshold, "
        "notification_delivery_below_threshold]"))
    _bad(ADR.replace("prohibition: anecdotes-never-satisfy",
                     "prohibition: anecdotes-satisfy"))
    _bad(ADR.replace("unmeasured impressions never\nsatisfy this gate",
                     "unmeasured impressions satisfy this gate"))


def test_threshold_and_sample_mutations_fail():
    _bad(ADR.replace("install_completion_pct: 70", "install_completion_pct: 95"))
    _bad(ADR.replace("notification_delivery_pct: 80", "notification_delivery_pct: 50"))
    _bad(ADR.replace("retention_gap_points: 15", "retention_gap_points: 5"))
    _bad(ADR.replace("cohort_min_users: 30", "cohort_min_users: 3"))
    _bad(ADR.replace("window_min_weeks: 8", "window_min_weeks: 1"))
    _bad(ADR.replace("subgroup_min_users: 10", "subgroup_min_users: 0"))
    _bad(ADR.replace("subgroup_fallback: gate-unevaluable-cannot-reopen-native",
                     "subgroup_fallback: reopen-anyway"))
    _bad(ADR.replace("    subgroup_min_users: 10\n", ""))
    _bad(ADR.replace("    subgroup_fallback: gate-unevaluable-cannot-reopen-native\n", ""))


def test_expression_structure_mutations_fail():
    # dropping the retention conjunct from the expression
    _bad(ADR.replace("      - retention_gap_at_least_threshold\n", ""))
    # swapping AND to OR at the top of the expression
    _bad(ADR.replace("  reopen_expression:\n    and:", "  reopen_expression:\n    or:"))
    # deleting the whole expression
    expr = ("  reopen_expression:\n    and:\n      - or: "
            "[install_completion_below_threshold, notification_delivery_below_threshold]"
            "\n      - retention_gap_at_least_threshold\n")
    _bad(ADR.replace(expr, ""))


def test_status_and_decision_mutations_fail():
    _bad(ADR.replace("status: proposed", "status: accepted"))
    _bad(ADR.replace("Status: proposed", "Status: accepted"))
    _bad(ADR.replace("Proposed: Option A", "Proposed: Option B"))
    _bad(ADR.replace("no native store binaries are built or promised",
                     "native store binaries ship alongside"))


def test_gate_section_deleted_fails():
    start = ADR.index("## Native-reopen gate")
    end = ADR.index("## Consequences")
    _bad(ADR[:start] + ADR[end:])
    fm_start = ADR.index("gate:\n")
    fm_end = ADR.index("---", fm_start)
    gutted = ADR[:fm_start] + ADR[fm_end:]
    _bad(gutted)
