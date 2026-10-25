"""agri/ai_payload.py
Builds the ONLY data an external LLM may receive for an explanation.

Allow-list, not block-list: each topic copies named fields from results the
deterministic engine already produced. Identifying or commercially sensitive
fields (counterparty, portfolio, broker, execution reference, notes, user
names, record IDs, free-text exception details) are never copied. Limits keyed
by counterparty or portfolio have their key replaced by a placeholder.

The user sees this exact payload before anything is sent (/ai/preview).
"""
from datetime import datetime

from agri import reference as ref

TRADE_FIELDS = ("instrument_type", "option_type", "direction", "quantity", "quantity_unit",
                "contract_month", "pricing_type", "price", "price_unit", "currency", "strike", "status")
VALUATION_FIELDS = ("status", "status_reason", "mtm_usd", "delta_t", "notional_usd",
                    "mark_price", "mark_age_hours", "formula", "assumptions")
LIMIT_FIELDS = ("dimension", "metric_label", "unit", "exposure", "limit_value", "utilisation",
                "warn_pct", "status", "data_complete", "formula")
EXCEPTION_FIELDS = ("rule", "title", "severity", "state", "overridable")

NEVER_SENT = ["Trade / exception / limit IDs", "Counterparty", "Portfolio / business unit", "Broker",
              "Execution reference", "Supporting notes", "User names and audit history",
              "Free-text exception details", "Counterparty or portfolio limit keys"]

REDACTED_DIMENSIONS = {"counterparty": "<counterparty>", "portfolio": "<portfolio>"}

TOPICS = ("trade", "exception", "limit")


def _pick(src: dict, fields) -> dict:
    return {k: src.get(k) for k in fields if src.get(k) not in (None, "")}


def _trade_part(trade: dict, valuation: dict, maturity_bucket: str | None) -> dict:
    spec = ref.COMMODITIES.get(trade.get("commodity"), {})
    out = {"commodity": spec.get("name", trade.get("commodity")), **_pick(trade, TRADE_FIELDS)}
    if maturity_bucket:
        out["maturity_bucket"] = maturity_bucket
    out["valuation"] = _pick(valuation, VALUATION_FIELDS)
    return out


def _limit_part(limit: dict) -> dict:
    out = _pick(limit, LIMIT_FIELDS)
    dim = limit.get("dimension")
    if dim == "commodity":
        out["key"] = ref.COMMODITIES.get(limit.get("key"), {}).get("name", limit.get("key"))
    elif dim == "maturity_bucket":
        out["key"] = limit.get("key")
    else:
        out["key"] = REDACTED_DIMENSIONS.get(dim, "<redacted>")
    return out


def build(service, topic: str, entity_id: str) -> dict:
    """Return {"topic", "data"} for the given entity. Raises LookupError / ValueError."""
    if topic not in TOPICS:
        raise ValueError("topic must be trade, exception or limit.")
    if topic == "trade":
        d = service.trade_detail(entity_id)
        data = _trade_part(d["trade"], d["valuation"], d["maturity_bucket"])
        data["open_exceptions"] = [_pick(e, EXCEPTION_FIELDS) for e in d["exceptions"]]
        return {"topic": topic, "data": data}

    s = service.snapshot()
    if topic == "limit":
        lim = next((x for x in s["limits"] if x["id"] == entity_id), None)
        if not lim:
            raise LookupError("Limit not found.")
        return {"topic": topic, "data": _limit_part(lim)}

    exc = next((x for x in s["exceptions"] if x["id"] == entity_id), None)
    if not exc:
        raise LookupError("Exception not found.")
    data = _pick(exc, EXCEPTION_FIELDS)
    if exc.get("trade_id"):
        t = next(t for t in s["trades"] if t["id"] == exc["trade_id"])
        data["trade"] = _trade_part(t, s["valuations"][t["id"]], None)
    elif exc.get("limit_id"):
        lim = next((x for x in s["limits"] if x["id"] == exc["limit_id"]), None)
        if lim:
            data["limit"] = _limit_part(lim)
    elif exc.get("price_key"):
        commodity, month = exc["price_key"].split("|", 1)
        mark = s["prices"].get(exc["price_key"])
        data["market_input"] = {"commodity": ref.COMMODITIES.get(commodity, {}).get("name", commodity),
                                "contract_month": month, "mark_available": bool(mark)}
        if mark:
            age = (s["now"] - datetime.fromisoformat(mark["as_of"])).total_seconds() / 3600
            data["market_input"]["mark_age_hours"] = round(age, 1)
            data["market_input"]["stale_threshold_hours"] = service.stale_hours
    return {"topic": topic, "data": data}
