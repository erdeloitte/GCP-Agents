"""agri/monitoring.py
Deterministic exception rules. Exceptions are re-derived from current state on
every evaluation; IDs are stable ("RULE:entity") so recorded manual actions
(acknowledge / override) stay attached while the condition persists and drop
away once the underlying condition is resolved.
"""
from datetime import date

from agri import reference as ref

RULES = {
    "MISSING_PRICE":     {"label": "Missing market input", "severity": "CRITICAL", "overridable": False},
    "LIMIT_BREACH":      {"label": "Limit breach", "severity": "CRITICAL", "overridable": False},
    "STALE_PRICE":       {"label": "Stale market input", "severity": "HIGH", "overridable": True},
    "LIMIT_WARNING":     {"label": "Limit warning", "severity": "HIGH", "overridable": False},
    "CONFIRM_MISMATCH":  {"label": "Broker confirmation mismatch", "severity": "HIGH", "overridable": False},
    "MATURED_OPEN":      {"label": "Matured — not closed out", "severity": "HIGH", "overridable": False},
    "PRICE_OUT_OF_BAND": {"label": "Valuation exception — price outside tolerance", "severity": "MEDIUM", "overridable": True},
    "UNCONFIRMED":       {"label": "Unmatched — no broker confirmation", "severity": "MEDIUM", "overridable": False},
    "MISSING_DATA":      {"label": "Missing trade data", "severity": "MEDIUM", "overridable": False},
    "MATURITY_SOON":     {"label": "Approaching maturity", "severity": "MEDIUM", "overridable": False},
    "REJECTED_TRADE":    {"label": "Rejected trade awaiting correction", "severity": "MEDIUM", "overridable": False},
    "PENDING_REVIEW":    {"label": "Manual review required", "severity": "LOW", "overridable": False},
}
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
MATURITY_WARNING_DAYS = 10


def _exc(rule: str, entity_type: str, entity_id: str, detail: str, **extra) -> dict:
    meta = RULES[rule]
    return {
        "id": f"{rule}:{entity_id}", "rule": rule, "title": meta["label"],
        "severity": meta["severity"], "overridable": meta["overridable"],
        "entity_type": entity_type, "entity_id": entity_id, "detail": detail, **extra,
    }


def price_key(commodity: str, month: str) -> str:
    return f"{commodity}|{month}"


def detect_exceptions(trades: list[dict], valuations: dict, confirmations: dict,
                      limit_results: list[dict], today: date,
                      maturity_days: int = MATURITY_WARNING_DAYS) -> list[dict]:
    out: list[dict] = []
    seen_price_keys: set[str] = set()

    for t in trades:
        v = valuations[t["id"]]
        spec = ref.COMMODITIES[t["commodity"]]
        tid = t["id"]

        if t["status"] == "PENDING_REVIEW":
            out.append(_exc("PENDING_REVIEW", "trade", tid,
                            f"Version {t['version']} captured by {t.get('updated_by') or t.get('created_by')} "
                            "requires independent review.", trade_id=tid))
        if t["status"] == "REJECTED":
            out.append(_exc("REJECTED_TRADE", "trade", tid, "Trade was rejected at review; amend or cancel.",
                            trade_id=tid))
        if t["status"] not in ref.RISK_STATUSES:
            continue

        days = (date.fromisoformat(t["maturity_date"]) - today).days
        if days < 0:
            out.append(_exc("MATURED_OPEN", "trade", tid,
                            f"Matured {-days} day(s) ago ({t['maturity_date']}) and still open.", trade_id=tid))
            continue
        if days <= maturity_days:
            out.append(_exc("MATURITY_SOON", "trade", tid,
                            f"Matures in {days} day(s) on {t['maturity_date']}.", trade_id=tid))

        missing = []
        if t["instrument_type"] in ref.EXCHANGE_TRADED:
            if not t.get("broker"):
                missing.append("broker")
            if not t.get("execution_ref"):
                missing.append("execution reference")
        if not t.get("counterparty"):
            missing.append("counterparty")
        if missing:
            out.append(_exc("MISSING_DATA", "trade", tid, f"Missing: {', '.join(missing)}.", trade_id=tid))

        # Broker matching (exchange-traded with an execution reference)
        ref_id = t.get("execution_ref")
        if t["instrument_type"] in ref.EXCHANGE_TRADED and ref_id:
            conf = confirmations.get(ref_id)
            if conf is None:
                out.append(_exc("UNCONFIRMED", "trade", tid,
                                f"No broker confirmation received for {ref_id}.", trade_id=tid))
            else:
                diffs = []
                if abs(float(conf["quantity"]) - float(t["quantity"])) > 1e-9:
                    diffs.append(f"quantity {conf['quantity']:g} vs {t['quantity']:g}")
                if abs(float(conf["price"]) - float(t["price"])) > 1e-9:
                    diffs.append(f"price {conf['price']:g} vs {t['price']:g}")
                if diffs:
                    out.append(_exc("CONFIRM_MISMATCH", "trade", tid,
                                    f"Broker confirmation {ref_id} differs: {'; '.join(diffs)}.", trade_id=tid))

        pk = price_key(t["commodity"], t["contract_month"])
        if v["status"] == "MISSING" and pk not in seen_price_keys:
            seen_price_keys.add(pk)
            out.append(_exc("MISSING_PRICE", "market_price", pk,
                            f"No mark for {spec['name']} {t['contract_month']}; dependent trades are not valued.",
                            price_key=pk))
        if v["status"] in ("STALE", "STALE_ACCEPTED") and pk not in seen_price_keys:
            seen_price_keys.add(pk)
            out.append(_exc("STALE_PRICE", "market_price", pk,
                            f"Mark for {spec['name']} {t['contract_month']} is {v['mark_age_hours']:.0f}h old "
                            f"(as of {v['mark_as_of']}). Override to accept it for valuation.",
                            price_key=pk))

        if (t["instrument_type"] != "OPTION" and v["mark_price"]
                and abs(float(t["price"]) - v["mark_price"]) / v["mark_price"] > spec["price_band_pct"]):
            dev = abs(float(t["price"]) - v["mark_price"]) / v["mark_price"]
            out.append(_exc("PRICE_OUT_OF_BAND", "trade", tid,
                            f"Trade price {t['price']:g} deviates {dev:.0%} from mark {v['mark_price']:g} "
                            f"{spec['price_unit']} (tolerance {spec['price_band_pct']:.0%}).", trade_id=tid))

    for lr in limit_results:
        if lr["status"] in ("BREACH", "WARN"):
            rule = "LIMIT_BREACH" if lr["status"] == "BREACH" else "LIMIT_WARNING"
            out.append(_exc(rule, "limit", lr["id"],
                            f"{lr['dimension']} '{lr['key']}': {lr['metric_label']} "
                            f"{lr['exposure']:,.0f} {lr['unit']} vs limit {lr['limit_value']:,.0f} "
                            f"({lr['utilisation']:.0%}).", limit_id=lr["id"]))

    out.sort(key=lambda x: (SEVERITY_ORDER[x["severity"]], x["id"]))
    return out
