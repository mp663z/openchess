"""T3761 v2: revenue target contract - semantic tests over structured values.

Every policy value is a typed field with an explicit source; tests assert the
exact typed values, so mutating prose cannot flip semantics silently.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = yaml.safe_load((ROOT / "data/contracts/revenue-target.yaml").read_text())
SCHEDULE = yaml.safe_load((ROOT / "data/contracts/schedule.yaml").read_text())


def test_target_values_exact_and_user_approved():
    t = CONTRACT["target"]
    assert t["active_paying_users"] == {"value": 1000, "source": "report-v5"}
    assert t["target_instant"]["value"] == "2026-12-31T23:59:59Z"
    assert t["target_instant"]["source"] == "user-decision"
    assert t["target_instant"]["approved"] == "2026-09-19"
    assert t["measurement_window_days"] == {
        "value": 35, "source": "user-decision", "approved": "2026-09-19",
    }


def test_price_exact():
    prices = {p["currency"]: (p["amount"], p["source"]) for p in CONTRACT["price"]["monthly"]}
    assert prices == {"EUR": (8, "report-v5"), "USD": (10, "report-v5")}


def test_currency_policy_semantics():
    c = CONTRACT["currency"]
    assert c["presentment_currencies"] == ["EUR", "USD"]
    assert c["reporting_currency"] == {
        "value": "EUR", "source": "user-decision", "approved": "2026-09-19",
    }
    assert c["fx_source"]["value"] == "ECB"


def test_tax_is_never_revenue_semantics():
    # typed boolean, not prose: collected tax must be exactly false-as-revenue
    assert CONTRACT["tax"]["collected_tax_is_revenue"]["value"] is False


def test_refund_policy_semantics():
    r = CONTRACT["refunds"]
    assert r["window_days"]["value"] == 14
    assert r["netted_from"]["value"] == "month_issued"


def test_net_revenue_formula_and_all_eur_reference():
    nr = CONTRACT["net_revenue"]
    assert nr["definition"] == (
        "gross_charges - refunds - taxes_remitted - payment_processing_fees"
    )
    # all-EUR reference: 1000 x EUR 8 x 12 = EUR 96,000 gross ARR
    assert CONTRACT["target"]["active_paying_users"]["value"] * 8 * 12 == 96000


def test_mixed_currency_arr_example_is_arithmetically_exact():
    ex = CONTRACT["net_revenue"]["mixed_currency_example"]
    eur_part = ex["eur_users"] * 8 * 12
    usd_part_in_eur = ex["usd_users"] * 10 * 12 / ex["usd_per_eur_rate"]
    assert abs(eur_part + usd_part_in_eur - ex["arr_eur"]) < 0.01
    # USD 10 at 1.08 USD/EUR is above EUR 8: the mix beats the 96k reference
    assert ex["arr_eur"] > 96000


def test_funnel_internally_consistent():
    f = CONTRACT["funnel"]
    assert f["visitors_during_target_window"] * f["visitor_to_free"] == (
        f["active_free_users"]
    )
    assert f["active_free_users"] * f["free_to_paid"] == (
        CONTRACT["target"]["active_paying_users"]["value"]
    )
    assert f["source"] == "report-v5"


def test_provenance_distinguishes_report_from_user_decisions():
    prov = CONTRACT["provenance"]
    assert any("1000" in p for p in prov["report_verbatim"])
    assert "user_approved_2026_09_19" not in prov  # v2 claim removed in v3


def test_policy_bundle_provenance_is_proposal_plus_reply():
    od = CONTRACT["provenance"]["owner_policy_decision"]
    prop, reply = od["proposal"], od["reply"]
    assert prop["wamid"] == (
        "wamid.HBgMOTE4MTIxNzk4Mjg1FQIAERgSMkY5NjUyM0VDQ0MwRkVBQ0JFAA=="
    )
    assert prop["at"] == "2026-09-19T03:20:26+05:30"
    assert len(prop["items"]) == 5
    assert "2027" in prop["items"][0]  # proposal said 2027
    assert reply["wamid"] == (
        "wamid.HBgMOTE4MTIxNzk4Mjg1FQIAEhgUM0I4MzQ3RDlBN0VGQ0Y3MDA1QjcA"
    )
    assert reply["at"] == "2026-09-19T03:21:01+05:30"
    assert "2026" in reply["effect"]
    assert "2026-12-31T23:59:59Z" in reply["effect"]
    # the terse schedule messages must not be cited as policy approval
    sched = CONTRACT["provenance"]["schedule_decisions"]
    assert sched["launch"]["wamid"] != reply["wamid"]
    assert "do not by themselves approve" in od["note"]


def test_schedule_provenance_wamids():
    sched = CONTRACT["provenance"]["schedule_decisions"]
    assert sched["launch"]["value"] == "2026-10"
    assert sched["oss_release"]["value"] == "2026-09-30"
    assert sched["launch"]["at"] == "2026-09-19T03:21:11+05:30"
    assert sched["oss_release"]["at"] == "2026-09-19T03:21:17+05:30"
    sp = SCHEDULE["provenance"]
    assert sp["product_launch"]["wamid"] == sched["launch"]["wamid"]
    assert sp["oss_release"]["wamid"] == sched["oss_release"]["wamid"]
    assert sp["revenue_target"]["reply_wamid"] == (
        "wamid.HBgMOTE4MTIxNzk4Mjg1FQIAEhgUM0I4MzQ3RDlBN0VGQ0Y3MDA1QjcA"
    )


def test_schedule_contract_records_owner_milestones():
    m = SCHEDULE["milestones"]
    assert m["oss_release"]["date"] == "2026-09-30"
    assert m["product_launch"]["window"] == "2026-10"
    assert m["revenue_target_instant"]["date"] == "2026-12-31"
    for milestone in m.values():
        assert milestone["source"] == "user-decision"
        assert milestone["approved"] == "2026-09-19"


def test_checkpoint_growth():
    assert CONTRACT["checkpoint"] == "GROWTH"
