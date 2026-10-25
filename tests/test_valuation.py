from datetime import timedelta

import pytest

from agri.valuation import value_trade
from conftest import make_mark, make_trade


def test_future_buy_mtm_and_units(now):
    # (450 − 400) USc/bu × 5,000 bu × 0.01 = USD 2,500
    v = value_trade(make_trade(), make_mark(450, now), now)
    assert v["status"] == "OK"
    assert v["mtm_usd"] == pytest.approx(2500.0)
    assert v["delta_t"] == pytest.approx(5000 * 0.0254012, abs=1e-3)
    assert v["notional_usd"] == pytest.approx(450 * 5000 * 0.01)
    assert v["inputs"][0]["name"] == "Mark price"


def test_sell_reverses_sign(now):
    v = value_trade(make_trade(direction="SELL"), make_mark(450, now), now)
    assert v["mtm_usd"] == pytest.approx(-2500.0)
    assert v["delta_t"] < 0
    assert v["net_notional_usd"] < 0


def test_non_usd_contract_converted_with_demo_fx(now):
    t = make_trade(commodity="ICE_LDN_COCOA", quantity=10.0, quantity_unit="t", price=5000.0,
                   currency="GBP", price_unit="GBP/t")
    v = value_trade(t, make_mark(5100, now, "ICE_LDN_COCOA"), now)
    assert v["mtm_ccy"] == pytest.approx(1000.0)
    assert v["mtm_usd"] == pytest.approx(1270.0)  # GBP→USD 1.27 synthetic
    assert v["delta_t"] == pytest.approx(10.0)


def test_pound_contract_tonnes_conversion(now):
    t = make_trade(commodity="ICE_SUGAR11", quantity=112000.0, price=20.0)
    v = value_trade(t, make_mark(21, now, "ICE_SUGAR11"), now)
    assert v["delta_t"] == pytest.approx(112000 * 0.000453592, abs=1e-3)
    assert v["mtm_usd"] == pytest.approx(1 * 112000 * 0.01)


def test_itm_call_intrinsic_less_premium(now):
    t = make_trade(instrument_type="OPTION", option_type="CALL", strike=400.0, price=20.0)
    v = value_trade(t, make_mark(450, now), now)
    assert v["mtm_usd"] == pytest.approx((50 - 20) * 5000 * 0.01)
    assert v["delta_t"] == pytest.approx(5000 * 0.0254012, abs=1e-3)
    assert any("intrinsic only" in a for a in v["assumptions"])


def test_otm_put_has_zero_delta_and_premium_loss(now):
    t = make_trade(instrument_type="OPTION", option_type="PUT", strike=400.0, price=10.0)
    v = value_trade(t, make_mark(450, now), now)
    assert v["delta_t"] == 0.0
    assert v["mtm_usd"] == pytest.approx(-10 * 5000 * 0.01)


def test_missing_mark_produces_no_value(now):
    v = value_trade(make_trade(), None, now)
    assert v["status"] == "MISSING"
    assert v["mtm_usd"] is None and v["notional_usd"] is None
    assert v["delta_t"] is not None  # linear exposure in tonnes is still known


def test_missing_mark_option_has_no_delta(now):
    t = make_trade(instrument_type="OPTION", option_type="CALL", strike=400.0, price=10.0)
    assert value_trade(t, None, now)["delta_t"] is None


def test_stale_mark_flagged_then_accepted(now):
    old = make_mark(450, now - timedelta(hours=30))
    assert value_trade(make_trade(), old, now, stale_hours=24)["status"] == "STALE"
    assert value_trade(make_trade(), old, now, stale_hours=24, stale_accepted=True)["status"] == "STALE_ACCEPTED"
    assert value_trade(make_trade(), make_mark(450, now - timedelta(hours=23)), now, 24)["status"] == "OK"


@pytest.mark.parametrize("status", ["CANCELLED", "REJECTED"])
def test_inactive_trades_excluded(now, status):
    v = value_trade(make_trade(status=status), make_mark(450, now), now)
    assert v["status"] == "EXCLUDED" and v["mtm_usd"] is None


def test_matured_trade_excluded(now):
    v = value_trade(make_trade(maturity_date="2026-06-01"), make_mark(450, now), now)
    assert v["status"] == "EXCLUDED"
    assert "maturity" in v["status_reason"].lower()


def test_deterministic(now):
    a = value_trade(make_trade(), make_mark(450, now), now)
    b = value_trade(make_trade(), make_mark(450, now), now)
    assert a == b
