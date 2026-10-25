from datetime import date, timedelta

import pytest

from agri import risk
from agri.valuation import value_trade
from conftest import make_mark, make_trade

TODAY = date(2026, 6, 15)


@pytest.mark.parametrize("days,bucket", [(0, "0-3m"), (91, "0-3m"), (92, "3-6m"), (182, "3-6m"),
                                         (183, "6-12m"), (365, "6-12m"), (366, "12m+")])
def test_maturity_buckets(days, bucket):
    assert risk.maturity_bucket((TODAY + timedelta(days=days)).isoformat(), TODAY) == bucket


def _book(now):
    trades = [
        make_trade(id="A", direction="BUY", quantity=10000.0, price=400.0),
        make_trade(id="B", direction="SELL", quantity=5000.0, price=460.0),
        make_trade(id="C", direction="BUY", quantity=5000.0, contract_month="2027-03",
                   maturity_date="2027-03-14"),                                      # stale mark
        make_trade(id="D", direction="BUY", quantity=5000.0, status="CANCELLED"),     # excluded
    ]
    marks = {"2026-12": make_mark(450, now), "2027-03": make_mark(450, now - timedelta(hours=48), month="2027-03")}
    vals = {t["id"]: value_trade(t, marks[t["contract_month"]], now) for t in trades}
    return trades, vals


def test_aggregate_by_commodity(now):
    trades, vals = _book(now)
    agg = risk.aggregate(trades, vals, "commodity", now.date())["CBOT_CORN"]
    tpu = 0.0254012
    assert agg["trade_ids"] == ["A", "B", "C"]                       # cancelled D excluded
    assert agg["net_tonnes"] == pytest.approx((10000 - 5000 + 5000) * tpu, abs=0.1)
    assert agg["gross_tonnes"] == pytest.approx(20000 * tpu, abs=0.1)
    # MtM excludes stale C: A = 50×10000×0.01, B = −(450−460)×5000×0.01
    assert agg["mtm_usd"] == pytest.approx(5000 + 500)
    assert agg["data_complete"] is False
    assert agg["incomplete"][0]["trade_id"] == "C"


def test_unknown_dimension_rejected(now):
    trades, vals = _book(now)
    with pytest.raises(ValueError):
        risk.aggregate(trades, vals, "colour", now.date())


@pytest.mark.parametrize("util,status", [(0.5, "OK"), (0.8, "WARN"), (0.99, "WARN"), (1.0, "BREACH"), (1.5, "BREACH")])
def test_limit_status_thresholds(util, status):
    assert risk.limit_status(util, 0.8) == status


def test_evaluate_limits(now):
    trades, vals = _book(now)
    aggs = {d: risk.aggregate(trades, vals, d, now.date()) for d in risk.DIMENSIONS}
    limits = [
        {"id": "L1", "dimension": "commodity", "key": "CBOT_CORN", "metric": "net_tonnes",
         "limit_value": 250.0, "unit": "t", "warn_pct": 0.8},
        {"id": "L2", "dimension": "portfolio", "key": "EU Grains Origination", "metric": "mtm_loss_usd",
         "limit_value": 1000.0, "unit": "USD", "warn_pct": 0.8},
        {"id": "L3", "dimension": "counterparty", "key": "Nobody", "metric": "gross_notional_usd",
         "limit_value": 1.0, "unit": "USD", "warn_pct": 0.8},
    ]
    res = {r["id"]: r for r in risk.evaluate_limits(limits, aggs)}
    assert res["L1"]["status"] == "BREACH"              # ~254 t vs 250 t
    assert res["L1"]["trade_ids"] == ["A", "B", "C"]
    assert res["L2"]["exposure"] == 0.0                 # MtM positive → no loss
    assert res["L3"]["status"] == "OK" and res["L3"]["exposure"] == 0.0


def test_derivation_lists_contributions(now):
    trades, vals = _book(now)
    d = risk.derivation(trades, vals, "commodity", "CBOT_CORN", now.date())
    ids = [c["trade_id"] for c in d["contributions"]]
    assert ids == ["A", "B", "C"]
    c = next(c for c in d["contributions"] if c["trade_id"] == "C")
    assert c["included_in_totals"] is False and c["valuation_status"] == "STALE"


def test_positions_split_long_short_and_count_valued(now):
    trades, vals = _book(now)
    [corn] = risk.positions(trades, vals)
    t_per_bu = 0.0254012
    assert corn["trades"] == 3 and corn["valued"] == 2 and corn["data_complete"] is False   # C stale, D excluded
    assert corn["long_t"] == round(15000 * t_per_bu, 1)
    assert corn["short_t"] == round(5000 * t_per_bu, 1)
    assert corn["net_t"] == round(10000 * t_per_bu, 1)
    assert corn["mtm_usd"] == round(vals["A"]["mtm_usd"] + vals["B"]["mtm_usd"], 2)       # stale C not in MtM
    assert corn["contract_months"] == ["2026-12", "2027-03"]
    assert corn["statuses"] == {"OK": 2, "STALE": 1}
