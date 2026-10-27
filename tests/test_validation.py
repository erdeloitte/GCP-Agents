from datetime import date

import pytest

from agri.validation import validate_trade

TODAY = date(2026, 6, 15)


def form(**overrides) -> dict:
    d = {
        "counterparty": "Demo Clearing Member A", "portfolio": "EU Grains Origination",
        "commodity": "CBOT_CORN", "instrument_type": "FUTURE", "direction": "buy",
        "quantity": "10000", "contract_month": "2026-12", "pricing_type": "FIXED",
        "price": "445.25", "trade_date": "2026-06-15", "effective_date": "",
        "maturity_date": "2026-12-14", "broker": "Demo Broker Alpha", "execution_ref": "EX-9",
        "source": "", "notes": "", "strategy": "ORIG_HEDGE",
    }
    d.update(overrides)
    return d


def errors(**overrides):
    return validate_trade(form(**overrides), TODAY).errors


def test_valid_trade_normalised_with_defaults():
    r = validate_trade(form(), TODAY)
    assert r.ok, r.errors
    c = r.clean
    assert c["direction"] == "BUY"
    assert c["effective_date"] == "2026-06-15"       # defaults to trade date
    assert c["source"] == "MANUAL"
    assert c["quantity_unit"] == "bu" and c["price_unit"] == "USc/bu" and c["currency"] == "USD"
    assert c["reference_market"] == "CBOT ZC 2026-12"
    assert c["quantity"] == 10000.0


@pytest.mark.parametrize("field", ["counterparty", "portfolio", "commodity", "instrument_type",
                                   "direction", "quantity", "price", "contract_month",
                                   "trade_date", "maturity_date"])
def test_required_fields(field):
    assert field in errors(**{field: ""})


def test_unknown_counterparty_rejected():
    assert "Data Setup" in errors(counterparty="Real Company Inc")["counterparty"]


def test_quantity_must_be_positive_and_numeric():
    assert "quantity" in errors(quantity="-5000")
    assert "quantity" in errors(quantity="abc")


def test_exchange_traded_lot_multiple():
    assert "lot size" in errors(quantity="7500")["quantity"]
    # OTC swaps may trade odd quantities
    assert "quantity" not in errors(instrument_type="SWAP", quantity="7500", maturity_date="2026-12-31")


def test_contract_month_must_be_listed():
    assert "lists months" in errors(contract_month="2026-11")["contract_month"]
    assert "YYYY-MM" in errors(contract_month="Dec-26")["contract_month"]


def test_date_ordering():
    assert "trade_date" in errors(trade_date="2026-06-20")                   # future trade date
    assert "effective_date" in errors(effective_date="2026-06-01")           # before trade date
    assert "maturity_date" in errors(trade_date="2026-06-01", maturity_date="2026-05-30")
    assert "past" in errors(trade_date="2026-01-05", maturity_date="2026-03-14",
                            contract_month="2026-03")["maturity_date"]


def test_past_maturity_allowed_on_amendment():
    r = validate_trade(form(trade_date="2026-01-05", maturity_date="2026-03-14", contract_month="2026-03"),
                       TODAY, is_new=False)
    assert "maturity_date" not in r.errors


def test_exchange_maturity_must_align_with_contract_month():
    assert "contract month" in errors(maturity_date="2026-09-14")["maturity_date"]


def test_option_requires_type_and_strike():
    e = errors(instrument_type="OPTION", maturity_date="2026-11-25")
    assert "option_type" in e and "strike" in e
    assert validate_trade(form(instrument_type="OPTION", option_type="CALL", strike="450",
                               price="12", maturity_date="2026-11-25"), TODAY).ok


def test_option_fields_rejected_on_non_options():
    e = errors(option_type="CALL", strike="450")
    assert "option_type" in e and "strike" in e


def test_index_average_only_for_swaps():
    assert "pricing_type" in errors(pricing_type="INDEX_AVERAGE")


def test_exchange_traded_requires_execution_details():
    e = errors(broker="", execution_ref="")
    assert "broker" in e and "execution_ref" in e


def test_inconsistent_units_rejected():
    assert "currency" in errors(currency="EUR")
    assert "price_unit" in errors(price_unit="USD/t")


def test_price_band_is_warning_not_error():
    r = validate_trade(form(price="600"), TODAY, reference_price=445.0)
    assert r.ok
    assert r.warnings and "deviates" in r.warnings[0]


def test_text_length_limits():
    assert "notes" in errors(notes="x" * 1001)


# ── Data Setup lookups ──────────────────────────────────────────────────────

def test_inactive_or_unknown_reference_values_rejected():
    from agri import refdata
    lk = refdata.default_lookups()
    lk["counterparty"] = lk["counterparty"] - {"Demo Clearing Member A"}
    assert "Data Setup" in validate_trade(form(), TODAY, lookups=lk).errors["counterparty"]
    assert "strategy" in errors(strategy="CAL_SPREAD")          # seeded as INACTIVE
    assert "broker" in errors(broker="Unknown Broker")
    lk = refdata.default_lookups()
    lk["commodity"] = set()
    assert "inactive" in validate_trade(form(), TODAY, lookups=lk).errors["commodity"]


def test_strategy_optional_with_warning():
    r = validate_trade(form(strategy=""), TODAY)
    assert r.ok and any("strategy" in w for w in r.warnings)


def test_delivery_location_forwards_only():
    assert errors(delivery_location="DEMO_PORT_NS")["delivery_location"].startswith("Delivery location applies")
    fwd = form(instrument_type="FORWARD", broker="", execution_ref="", maturity_date="2026-12-31")
    assert validate_trade({**fwd, "delivery_location": "DEMO_PORT_NS"}, TODAY).ok
    assert "delivery_location" in validate_trade({**fwd, "delivery_location": "NOWHERE"}, TODAY).errors
    assert any("delivery location" in w for w in validate_trade(fwd, TODAY).warnings)
