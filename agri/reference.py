"""agri/reference.py
Static reference data for the prototype.

Contract specifications are simplified approximations of commonly listed
agricultural contracts, used only to drive validation and unit conversion in
the demo. They are NOT an authoritative contract master. FX rates are
synthetic and fixed.
"""

# Tonnes per quantity unit
TONNES_PER_UNIT = {
    "t": 1.0,
    "bu_corn": 0.0254012,   # 56 lb bushel
    "bu_wheat": 0.0272155,  # 60 lb bushel
    "bu_soy": 0.0272155,    # 60 lb bushel
    "lb": 0.000453592,
}

COMMODITIES: dict[str, dict] = {
    "CBOT_CORN": {
        "name": "Corn", "exchange": "CBOT", "code": "ZC",
        "lot_size": 5000, "qty_unit": "bu", "tonnes_key": "bu_corn",
        "price_unit": "USc/bu", "currency": "USD", "price_to_ccy": 0.01,
        "listed_months": [3, 5, 7, 9, 12], "price_band_pct": 0.15,
    },
    "CBOT_WHEAT": {
        "name": "Chicago SRW Wheat", "exchange": "CBOT", "code": "ZW",
        "lot_size": 5000, "qty_unit": "bu", "tonnes_key": "bu_wheat",
        "price_unit": "USc/bu", "currency": "USD", "price_to_ccy": 0.01,
        "listed_months": [3, 5, 7, 9, 12], "price_band_pct": 0.15,
    },
    "CBOT_SOYBEANS": {
        "name": "Soybeans", "exchange": "CBOT", "code": "ZS",
        "lot_size": 5000, "qty_unit": "bu", "tonnes_key": "bu_soy",
        "price_unit": "USc/bu", "currency": "USD", "price_to_ccy": 0.01,
        "listed_months": [1, 3, 5, 7, 8, 9, 11], "price_band_pct": 0.15,
    },
    "ICE_SUGAR11": {
        "name": "Raw Sugar No.11", "exchange": "ICE US", "code": "SB",
        "lot_size": 112000, "qty_unit": "lb", "tonnes_key": "lb",
        "price_unit": "USc/lb", "currency": "USD", "price_to_ccy": 0.01,
        "listed_months": [3, 5, 7, 10], "price_band_pct": 0.20,
    },
    "ICE_COFFEE_C": {
        "name": "Arabica Coffee C", "exchange": "ICE US", "code": "KC",
        "lot_size": 37500, "qty_unit": "lb", "tonnes_key": "lb",
        "price_unit": "USc/lb", "currency": "USD", "price_to_ccy": 0.01,
        "listed_months": [3, 5, 7, 9, 12], "price_band_pct": 0.20,
    },
    "ICE_LDN_COCOA": {
        "name": "London Cocoa", "exchange": "ICE Europe", "code": "C",
        "lot_size": 10, "qty_unit": "t", "tonnes_key": "t",
        "price_unit": "GBP/t", "currency": "GBP", "price_to_ccy": 1.0,
        "listed_months": [3, 5, 7, 9, 12], "price_band_pct": 0.25,
    },
    "EURONEXT_MILLING_WHEAT": {
        "name": "Milling Wheat", "exchange": "Euronext", "code": "EBM",
        "lot_size": 50, "qty_unit": "t", "tonnes_key": "t",
        "price_unit": "EUR/t", "currency": "EUR", "price_to_ccy": 1.0,
        "listed_months": [3, 5, 9, 12], "price_band_pct": 0.15,
    },
}

INSTRUMENT_TYPES = ["FUTURE", "OPTION", "SWAP", "FORWARD"]
EXCHANGE_TRADED = {"FUTURE", "OPTION"}
OPTION_TYPES = ["CALL", "PUT"]
DIRECTIONS = ["BUY", "SELL"]
PRICING_TYPES = ["FIXED", "INDEX_AVERAGE"]
SOURCES = ["MANUAL", "BROKER_FEED_SIMULATED", "IMPORT"]

TRADE_STATUSES = ["PENDING_REVIEW", "VALIDATED", "REJECTED", "CANCELLED"]
# Statuses that count towards open exposure
RISK_STATUSES = {"PENDING_REVIEW", "VALIDATED"}

PORTFOLIOS = [
    "EU Grains Origination",
    "Softs Trading",
    "Oilseeds Hedging",
    "Treasury Overlay",
]

# Synthetic names only — no real client or counterparty data.
COUNTERPARTIES = [
    "Demo Clearing Member A",
    "Demo Clearing Member B",
    "Synthetic Grain Co (Demo)",
    "Example Sugar Mill (Demo)",
    "Sample Roaster Ltd (Demo)",
]

BROKERS = ["Demo Broker Alpha", "Demo Broker Beta", "Direct (OTC)"]

REPORTING_CCY = "USD"
# Synthetic FX: units of USD per 1 unit of currency
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}
FX_AS_OF_LABEL = "Synthetic fixed demo rates"

# Demo identities — explicitly not real authentication
DEMO_USERS = {
    "demo.trader": "Demo Trader (front office)",
    "demo.risk-reviewer": "Demo Risk Reviewer (middle office)",
}
DEFAULT_DEMO_USER = "demo.trader"


def tonnes_per_unit(commodity_key: str) -> float:
    return TONNES_PER_UNIT[COMMODITIES[commodity_key]["tonnes_key"]]


def reference_payload() -> dict:
    """Serialisable reference data for the UI."""
    return {
        "commodities": {k: {**v, "tonnes_per_unit": tonnes_per_unit(k)} for k, v in COMMODITIES.items()},
        "instrument_types": INSTRUMENT_TYPES,
        "exchange_traded": sorted(EXCHANGE_TRADED),
        "option_types": OPTION_TYPES,
        "directions": DIRECTIONS,
        "pricing_types": PRICING_TYPES,
        "sources": SOURCES,
        "statuses": TRADE_STATUSES,
        "portfolios": PORTFOLIOS,
        "counterparties": COUNTERPARTIES,
        "brokers": BROKERS,
        "reporting_ccy": REPORTING_CCY,
        "fx_to_usd": FX_TO_USD,
        "fx_label": FX_AS_OF_LABEL,
        "demo_users": DEMO_USERS,
        "valuation_methodology": _methodology(),
    }


def _methodology() -> list:
    from agri.valuation import METHODOLOGY   # local import: valuation imports this module
    return METHODOLOGY
