from datetime import datetime, timezone

import pytest

from agri.repository import InMemoryRepository
from agri.service import AgriService

FIXED_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now():
    return FIXED_NOW


@pytest.fixture
def service(now):
    return AgriService(repo=InMemoryRepository(now), clock=lambda: now)


def make_trade(**overrides) -> dict:
    """A minimal valid, open corn future (5,000 bu = 1 lot)."""
    t = {
        "id": "T-1", "counterparty": "Demo Clearing Member A", "portfolio": "EU Grains Origination",
        "commodity": "CBOT_CORN", "instrument_type": "FUTURE", "direction": "BUY",
        "quantity": 5000.0, "quantity_unit": "bu", "contract_month": "2026-12",
        "pricing_type": "FIXED", "reference_market": "CBOT ZC 2026-12", "price": 400.0,
        "currency": "USD", "price_unit": "USc/bu", "trade_date": "2026-06-10",
        "effective_date": "2026-06-10", "maturity_date": "2026-12-14", "broker": "Demo Broker Alpha",
        "execution_ref": "EX-1", "option_type": None, "strike": None, "source": "MANUAL",
        "notes": "", "status": "VALIDATED", "version": 1,
    }
    t.update(overrides)
    return t


def make_mark(price: float, as_of: datetime, commodity="CBOT_CORN", month="2026-12") -> dict:
    return {"commodity": commodity, "contract_month": month, "price": price,
            "price_unit": "", "as_of": as_of.isoformat(), "source": "TEST"}
