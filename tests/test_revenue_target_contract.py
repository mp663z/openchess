"""T3761: revenue target contract - values, definitions, internal consistency."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = yaml.safe_load((ROOT / "data/contracts/revenue-target.yaml").read_text())


def test_target_values_exact():
    t = CONTRACT["target"]
    assert t["active_paying_users"] == 1000
    assert t["target_date"] == "2027-12-31T23:59:59Z"
    assert t["measurement"]


def test_price_exact():
    prices = {p["currency"]: p["amount"] for p in CONTRACT["price"]["monthly"]}
    assert prices == {"EUR": 8, "USD": 10}


def test_currency_definitions_explicit():
    c = CONTRACT["currency"]
    assert c["presentment_currencies"] == ["EUR", "USD"]
    assert c["reporting_currency"] == "EUR"
    assert "ECB" in c["fx_rule"]


def test_tax_and_refund_definitions_explicit():
    assert "VAT" in CONTRACT["tax"]["rule"]
    assert "NEVER revenue" in CONTRACT["tax"]["rule"]
    assert "14-day" in CONTRACT["refunds"]["policy"]
    assert "netted from net revenue" in CONTRACT["refunds"]["policy"]


def test_net_revenue_formula_and_arr_consistency():
    nr = CONTRACT["net_revenue"]
    assert nr["definition"] == (
        "gross_charges - refunds - taxes_remitted - payment_processing_fees"
    )
    # 1000 x EUR 8 x 12 = EUR 96,000 ARR - the v5 "~EUR96k ARR" figure.
    assert CONTRACT["target"]["active_paying_users"] * 8 * 12 == 96000
    assert "96,000" in nr["notes"]


def test_funnel_internally_consistent():
    f = CONTRACT["funnel"]
    assert f["visitors_during_2027"] * f["visitor_to_free"] == f["active_free_users"]
    assert f["active_free_users"] * f["free_to_paid"] == (
        CONTRACT["target"]["active_paying_users"]
    )


def test_provenance_and_checkpoint():
    assert any("report v5" in p for p in CONTRACT["provenance"])
    assert CONTRACT["checkpoint"] == "GROWTH"
