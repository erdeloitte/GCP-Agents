"""agri/demo_data.py
Synthetic demo data set. Every price, trade, limit and confirmation here is
fabricated for demonstration and is labelled as such in the UI. Dates are
generated relative to `now` so the demo never ages out.

The set deliberately contains edge cases so every monitoring rule fires:
a stale mark, a missing mark, a limit breach and warning, a broker
mismatch, an unconfirmed trade, an imported trade with missing data, a
matured-but-open forward, a trade near maturity, an off-market price,
pending reviews, a rejection and a cancellation.
"""
import calendar
from datetime import date, datetime, timedelta, timezone

from agri import reference as ref
from agri import refdata
from agri.audit import make_event
from agri.models import BrokerConfirmation, Limit, MarketPrice, Trade

SEED_ACTOR = "demo.seed (synthetic data loader)"

# Synthetic front-month levels in each contract's own price unit.
BASE_PRICE = {
    "CBOT_CORN": 445.0, "CBOT_WHEAT": 560.0, "CBOT_SOYBEANS": 1040.0,
    "ICE_SUGAR11": 19.5, "ICE_COFFEE_C": 245.0, "ICE_LDN_COCOA": 5200.0,
    "EURONEXT_MILLING_WHEAT": 215.0,
}
CURVE_STEP = 0.012   # synthetic contango per listed month
FORWARD_LOCATION = {"CBOT_SOYBEANS": "DEMO_PORT_SA", "ICE_LDN_COCOA": "DEMO_WH_WA",
                    "EURONEXT_MILLING_WHEAT": "DEMO_SILO_EU"}
STALE_MARK = ("ICE_COFFEE_C", 1)      # (commodity, month index) published 60h ago
MISSING_MARK = ("ICE_LDN_COCOA", 2)   # no mark published


def upcoming_months(commodity: str, today: date, n: int = 3) -> list[str]:
    """Next `n` listed months, starting after the month that is 31 days out.

    This keeps futures (14th of the month) and options (25th of the prior
    month) at least a month from expiry whatever the current date.
    """
    listed = ref.COMMODITIES[commodity]["listed_months"]
    cutoff = today + timedelta(days=31)
    y, m = cutoff.year, cutoff.month
    out = []
    while len(out) < n:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        if m in listed:
            out.append(f"{y:04d}-{m:02d}")
    return out


def _listed_index(commodity: str, front: str, month: str) -> int:
    """Listed months from `front` up to (not including) `month`; 0 if not after front."""
    listed = ref.COMMODITIES[commodity]["listed_months"]
    y, m = int(front[:4]), int(front[5:])
    n = 0
    while f"{y:04d}-{m:02d}" < month:
        if m in listed:
            n += 1
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return n


def synthetic_marks(now: datetime, trades=()) -> list[dict]:
    """One synthetic feed snapshot published at `now`: marks for the next listed
    months plus any later month still held by `trades` (so stored trades keep a
    mark as the calendar rolls). One mark is stale and one missing by design."""
    today = now.date()
    current = today.strftime("%Y-%m")
    out = []
    for c in ref.COMMODITIES:
        ups = upcoming_months(c, today)
        held = {t["contract_month"] for t in trades if t["commodity"] == c and t["contract_month"] >= current}
        for month in sorted(set(ups) | held):
            idx = ups.index(month) if month in ups else _listed_index(c, ups[0], month)
            if month in ups and (c, idx) == MISSING_MARK:
                continue
            age = timedelta(hours=60) if month in ups and (c, idx) == STALE_MARK else timedelta(hours=2)
            out.append(MarketPrice(
                commodity=c, contract_month=month, price=mark_level(c, idx),
                price_unit=ref.COMMODITIES[c]["price_unit"],
                as_of=(now - age).replace(microsecond=0).isoformat(),
            ).to_dict())
    return out


def mark_level(commodity: str, idx: int) -> float:
    return round(BASE_PRICE[commodity] * (1 + CURVE_STEP * idx), 2)


def _maturity(instrument: str, month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    if instrument == "FUTURE":
        return date(y, m, 14).isoformat()
    if instrument == "OPTION":
        py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
        return date(py, pm, 25).isoformat()
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


# (portfolio, counterparty, commodity, instrument, direction, lots, month_idx,
#  price_mult, status, extra)
_SEED = [
    ("EU Grains Origination", "Demo Clearing Member A", "CBOT_CORN", "FUTURE", "BUY", 40, 0, 0.98, "VALIDATED", {}),
    ("EU Grains Origination", "Demo Clearing Member A", "CBOT_CORN", "FUTURE", "BUY", 30, 1, 1.01, "VALIDATED", {}),
    ("Treasury Overlay", "Demo Clearing Member B", "CBOT_CORN", "FUTURE", "SELL", 10, 0, 1.02, "VALIDATED", {}),
    ("EU Grains Origination", "Synthetic Grain Co (Demo)", "CBOT_CORN", "SWAP", "BUY", 20, 1, 0.97, "VALIDATED",
     {"pricing_type": "INDEX_AVERAGE"}),
    ("EU Grains Origination", "Demo Clearing Member A", "CBOT_CORN", "OPTION", "BUY", 20, 1, 0.03, "VALIDATED",
     {"option_type": "CALL", "strike_mult": 0.95}),
    ("EU Grains Origination", "Demo Clearing Member B", "CBOT_WHEAT", "FUTURE", "SELL", 25, 0, 1.03, "VALIDATED", {}),
    ("Treasury Overlay", "Demo Clearing Member B", "CBOT_WHEAT", "OPTION", "BUY", 10, 1, 0.02, "VALIDATED",
     {"option_type": "PUT", "strike_mult": 0.90}),
    ("EU Grains Origination", "Demo Clearing Member A", "CBOT_WHEAT", "FUTURE", "BUY", 5, 2, 1.00, "PENDING_REVIEW", {}),
    ("Oilseeds Hedging", "Demo Clearing Member A", "CBOT_SOYBEANS", "FUTURE", "BUY", 30, 0, 0.99, "VALIDATED", {}),
    ("Oilseeds Hedging", "Demo Clearing Member A", "CBOT_SOYBEANS", "FUTURE", "SELL", 12, 1, 1.00, "VALIDATED",
     {"confirm_qty_delta": -5000}),
    ("Oilseeds Hedging", "Synthetic Grain Co (Demo)", "CBOT_SOYBEANS", "FORWARD", "BUY", 15, 0, 1.00, "VALIDATED",
     {"maturity_days": -3}),
    ("Oilseeds Hedging", "Demo Clearing Member B", "CBOT_SOYBEANS", "FUTURE", "BUY", 8, 2, 1.00, "PENDING_REVIEW",
     {"source": "IMPORT", "broker": "", "execution_ref": "", "created_by": "demo.import (batch file)"}),
    ("Softs Trading", "Demo Clearing Member A", "ICE_SUGAR11", "FUTURE", "SELL", 50, 0, 1.00, "VALIDATED", {}),
    ("Softs Trading", "Example Sugar Mill (Demo)", "ICE_SUGAR11", "SWAP", "BUY", 40, 1, 1.26, "VALIDATED", {}),
    ("Softs Trading", "Demo Clearing Member B", "ICE_SUGAR11", "OPTION", "SELL", 20, 0, 0.03, "VALIDATED",
     {"option_type": "CALL", "strike_mult": 1.05}),
    ("Softs Trading", "Demo Clearing Member A", "ICE_SUGAR11", "FUTURE", "BUY", 10, 2, 0.99, "VALIDATED",
     {"unconfirmed": True}),
    ("Softs Trading", "Sample Roaster Ltd (Demo)", "ICE_COFFEE_C", "SWAP", "BUY", 30, 1, 0.96, "VALIDATED", {}),
    ("Softs Trading", "Demo Clearing Member A", "ICE_COFFEE_C", "FUTURE", "SELL", 20, 1, 1.02, "VALIDATED", {}),
    ("Softs Trading", "Demo Clearing Member B", "ICE_COFFEE_C", "FUTURE", "BUY", 15, 0, 1.00, "VALIDATED", {}),
    ("Softs Trading", "Demo Clearing Member B", "ICE_LDN_COCOA", "FUTURE", "BUY", 30, 0, 0.95, "VALIDATED", {}),
    ("Softs Trading", "Demo Clearing Member B", "ICE_LDN_COCOA", "FUTURE", "BUY", 20, 2, 1.00, "VALIDATED", {}),
    ("Softs Trading", "Sample Roaster Ltd (Demo)", "ICE_LDN_COCOA", "FORWARD", "SELL", 25, 0, 1.04, "VALIDATED",
     {"maturity_days": 6}),
    ("EU Grains Origination", "Synthetic Grain Co (Demo)", "EURONEXT_MILLING_WHEAT", "FORWARD", "SELL", 60, 0, 1.02,
     "VALIDATED", {}),
    ("EU Grains Origination", "Demo Clearing Member A", "EURONEXT_MILLING_WHEAT", "FUTURE", "BUY", 40, 0, 0.99,
     "VALIDATED", {}),
    ("EU Grains Origination", "Demo Clearing Member A", "EURONEXT_MILLING_WHEAT", "FUTURE", "BUY", 20, 1, 1.00,
     "PENDING_REVIEW", {}),
    ("EU Grains Origination", "Demo Clearing Member A", "EURONEXT_MILLING_WHEAT", "FUTURE", "SELL", 10, 1, 1.01,
     "REJECTED", {"reject_reason": "Booked to wrong portfolio — should be Treasury Overlay."}),
    ("Treasury Overlay", "Demo Clearing Member B", "CBOT_CORN", "FUTURE", "BUY", 5, 2, 1.00, "CANCELLED",
     {"cancel_reason": "Duplicate of broker-fed ticket."}),
]

_LIMITS = [
    ("commodity", "CBOT_CORN", "net_tonnes", 12000),
    ("commodity", "CBOT_WHEAT", "net_tonnes", 5000),
    ("commodity", "CBOT_SOYBEANS", "net_tonnes", 4000),
    ("commodity", "ICE_SUGAR11", "net_tonnes", 8000),
    ("commodity", "ICE_COFFEE_C", "net_tonnes", 1500),
    ("commodity", "ICE_LDN_COCOA", "net_tonnes", 1000),
    ("commodity", "EURONEXT_MILLING_WHEAT", "net_tonnes", 6000),
    ("portfolio", "Softs Trading", "mtm_loss_usd", 60_000),
    ("portfolio", "EU Grains Origination", "gross_notional_usd", 8_000_000),
    ("counterparty", "Demo Clearing Member A", "gross_notional_usd", 10_000_000),
    ("maturity_bucket", "0-3m", "net_tonnes", 10000),
]


def build(now: datetime) -> dict:
    """Return a fresh synthetic data set relative to `now` (timezone-aware)."""
    today = now.date()
    months = {c: upcoming_months(c, today) for c in ref.COMMODITIES}

    prices = synthetic_marks(now)

    trades, confirmations, audit = [], [], []
    for n, (pf, cp, c, inst, dirn, lots, idx, mult, status, extra) in enumerate(_SEED, start=1):
        spec = ref.COMMODITIES[c]
        month = months[c][idx]
        level = mark_level(c, idx)
        trade_date = today - timedelta(days=60 if extra.get("maturity_days", 0) < 0 else 2 + (n * 3) % 40)
        maturity = (today + timedelta(days=extra["maturity_days"])).isoformat() \
            if "maturity_days" in extra else _maturity(inst, month)
        exchange = inst in ref.EXCHANGE_TRADED
        tid = f"AGD-{n:04d}"
        created_by = extra.get("created_by", "demo.trader")
        created_at = f"{trade_date.isoformat()}T09:30:00+00:00"
        t = Trade(
            id=tid, counterparty=cp, portfolio=pf, commodity=c, instrument_type=inst,
            direction=dirn, quantity=float(lots * spec["lot_size"]), quantity_unit=spec["qty_unit"],
            contract_month=month, pricing_type=extra.get("pricing_type", "FIXED"),
            reference_market=f"{spec['exchange']} {spec['code']} {month}",
            price=round(level * mult, 2), currency=spec["currency"], price_unit=spec["price_unit"],
            trade_date=trade_date.isoformat(), effective_date=trade_date.isoformat(),
            maturity_date=maturity,
            broker=extra.get("broker", "Demo Broker Alpha" if exchange and n % 2 else
                             "Demo Broker Beta" if exchange else "Direct (OTC)"),
            execution_ref=extra.get("execution_ref", f"EX-{spec['code']}-{1000 + n}" if exchange else
                                    f"OTC-{1000 + n}"),
            option_type=extra.get("option_type"),
            strike=round(level * extra["strike_mult"], 2) if "strike_mult" in extra else None,
            source=extra.get("source", "BROKER_FEED_SIMULATED" if exchange else "MANUAL"),
            notes="Synthetic demo trade.", status=status, version=1,
            strategy="" if extra.get("source") == "IMPORT" else refdata.PORTFOLIO_STRATEGY.get(pf, ""),
            delivery_location=FORWARD_LOCATION.get(c, "") if inst == "FORWARD" else "",
            created_by=created_by, created_at=created_at, updated_by=created_by, updated_at=created_at,
        ).to_dict()
        audit.append(make_event(created_by, "TRADE_CREATED", "trade", tid, "Seeded synthetic demo trade",
                                timestamp=created_at))
        if status in ("VALIDATED", "REJECTED", "CANCELLED"):
            t["reviewed_by"] = "demo.risk-reviewer"
            t["reviewed_at"] = f"{trade_date.isoformat()}T15:00:00+00:00"
            reason = extra.get("reject_reason", "Terms checked against confirmation (seed).")
            audit.append(make_event("demo.risk-reviewer",
                                    "TRADE_REJECTED" if status == "REJECTED" else "TRADE_APPROVED",
                                    "trade", tid, reason, timestamp=t["reviewed_at"]))
        if status == "CANCELLED":
            audit.append(make_event("demo.trader", "TRADE_CANCELLED", "trade", tid, extra["cancel_reason"],
                                    timestamp=f"{trade_date.isoformat()}T16:00:00+00:00"))
        if exchange and t["execution_ref"] and not extra.get("unconfirmed"):
            confirmations.append(BrokerConfirmation(
                execution_ref=t["execution_ref"],
                quantity=t["quantity"] + extra.get("confirm_qty_delta", 0),
                price=t["price"], received_at=f"{trade_date.isoformat()}T18:00:00+00:00",
            ).to_dict())
        trades.append(t)

    limits = [Limit(id=f"LIM-{i:03d}", dimension=d, key=k, metric=m, limit_value=float(v),
                    unit="t" if m == "net_tonnes" else "USD",
                    updated_by=SEED_ACTOR, updated_at=now.replace(microsecond=0).isoformat()).to_dict()
              for i, (d, k, m, v) in enumerate(_LIMITS, start=1)]

    return {"trades": trades, "prices": prices, "limits": limits,
            "confirmations": confirmations, "audit": audit,
            "reference": refdata.default_records(now.replace(microsecond=0).isoformat())}


if __name__ == "__main__":  # quick look at the generated set
    import json
    print(json.dumps(build(datetime.now(timezone.utc)), indent=2)[:3000])
