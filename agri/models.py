"""agri/models.py
Plain data models for the prototype. Dates are ISO strings (YYYY-MM-DD),
timestamps are ISO-8601 UTC strings, contract months are "YYYY-MM".
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Business fields a user may capture or amend (system fields are excluded).
TRADE_EDITABLE_FIELDS = [
    "counterparty", "portfolio", "commodity", "instrument_type", "option_type",
    "strike", "direction", "quantity", "contract_month", "pricing_type",
    "reference_market", "price", "trade_date", "effective_date",
    "maturity_date", "broker", "execution_ref", "source", "notes",
    "strategy", "delivery_location",
]


@dataclass
class Trade:
    id: str
    counterparty: str
    portfolio: str
    commodity: str
    instrument_type: str
    direction: str
    quantity: float
    quantity_unit: str
    contract_month: str
    pricing_type: str
    reference_market: str
    price: float                 # trade price, or premium for options
    currency: str
    price_unit: str
    trade_date: str
    effective_date: str
    maturity_date: str
    broker: str = ""
    execution_ref: str = ""
    option_type: str | None = None
    strike: float | None = None
    source: str = "MANUAL"
    notes: str = ""
    strategy: str = ""              # Data Setup strategy code (optional)
    delivery_location: str = ""     # Data Setup location code (forwards only)
    status: str = "PENDING_REVIEW"
    version: int = 1
    created_by: str = ""
    created_at: str = ""
    updated_by: str = ""
    updated_at: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarketPrice:
    commodity: str
    contract_month: str
    price: float
    price_unit: str
    as_of: str
    source: str = "SYNTHETIC DEMO DATA"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Limit:
    id: str
    dimension: str      # commodity | portfolio | counterparty | maturity_bucket
    key: str
    metric: str         # net_tonnes | gross_notional_usd | mtm_loss_usd
    limit_value: float
    unit: str
    warn_pct: float = 0.80
    owner: str = "Risk (demo)"
    updated_by: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BrokerConfirmation:
    """Simulated broker confirmation used for matching."""
    execution_ref: str
    quantity: float
    price: float
    received_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditEvent:
    id: str
    timestamp: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    reason: str = ""
    before: dict | None = None
    after: dict | None = None
    changes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExceptionAction:
    """A controlled manual action recorded against an exception."""
    exception_id: str
    action: str         # ACKNOWLEDGE | OVERRIDE
    actor: str
    reason: str
    timestamp: str
    context: str = ""   # e.g. the mark timestamp a stale-price override applies to

    def to_dict(self) -> dict:
        return asdict(self)
