"""agri/risk.py
Deterministic exposure aggregation and limit utilisation.

Each aggregate keeps the trade IDs that contributed and those that could not
be included (missing / stale inputs), so any displayed figure can be traced
back to trades and market inputs.
"""
from datetime import date

from agri.valuation import INCLUDED_STATUSES

DIMENSIONS = ["commodity", "maturity_bucket", "portfolio", "counterparty"]
BUCKETS = ["0-3m", "3-6m", "6-12m", "12m+"]

METRICS = {
    "net_tonnes": {"label": "Net delta-equivalent tonnes (absolute)", "unit": "t"},
    "gross_notional_usd": {"label": "Gross notional", "unit": "USD"},
    "mtm_loss_usd": {"label": "Unrealised MtM loss", "unit": "USD"},
}


def maturity_bucket(maturity_date: str, today: date) -> str:
    days = (date.fromisoformat(maturity_date) - today).days
    if days <= 91:
        return "0-3m"
    if days <= 182:
        return "3-6m"
    if days <= 365:
        return "6-12m"
    return "12m+"


def dimension_key(trade: dict, dimension: str, today: date) -> str:
    if dimension == "maturity_bucket":
        return maturity_bucket(trade["maturity_date"], today)
    return trade[dimension]


def _empty(key: str) -> dict:
    return {
        "key": key, "net_tonnes": 0.0, "gross_tonnes": 0.0,
        "gross_notional_usd": 0.0, "net_notional_usd": 0.0, "mtm_usd": 0.0,
        "trade_ids": [], "incomplete": [], "data_complete": True,
    }


def aggregate(trades: list[dict], valuations: dict, dimension: str, today: date) -> dict:
    """Aggregate open exposure by a dimension. Returns {key: aggregate}."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"Unknown dimension '{dimension}'")
    groups: dict[str, dict] = {}
    for t in trades:
        v = valuations[t["id"]]
        if v["status"] == "EXCLUDED":
            continue
        key = dimension_key(t, dimension, today)
        g = groups.setdefault(key, _empty(key))
        g["trade_ids"].append(t["id"])
        if v["delta_t"] is not None:
            g["net_tonnes"] += v["delta_t"]
            g["gross_tonnes"] += abs(v["delta_t"])
        if v["status"] in INCLUDED_STATUSES:
            g["gross_notional_usd"] += v["notional_usd"]
            g["net_notional_usd"] += v["net_notional_usd"]
            g["mtm_usd"] += v["mtm_usd"]
        else:
            g["incomplete"].append({"trade_id": t["id"], "status": v["status"],
                                    "reason": v["status_reason"]})
            g["data_complete"] = False
    for g in groups.values():
        for f in ("net_tonnes", "gross_tonnes"):
            g[f] = round(g[f], 1)
        for f in ("gross_notional_usd", "net_notional_usd", "mtm_usd"):
            g[f] = round(g[f], 2)
    if dimension == "maturity_bucket":
        return {b: groups[b] for b in BUCKETS if b in groups}
    return dict(sorted(groups.items()))


def positions(trades: list[dict], valuations: dict) -> list[dict]:
    """Position table by commodity: long / short / net delta-equivalent tonnes,
    valued notional and MtM, and how many trades have a usable valuation."""
    rows: dict[str, dict] = {}
    for t in trades:
        v = valuations[t["id"]]
        if v["status"] == "EXCLUDED":
            continue
        r = rows.setdefault(t["commodity"], {
            "commodity": t["commodity"], "long_t": 0.0, "short_t": 0.0, "net_t": 0.0,
            "gross_notional_usd": 0.0, "mtm_usd": 0.0, "trades": 0, "valued": 0,
            "contract_months": set(), "statuses": {}})
        r["trades"] += 1
        r["contract_months"].add(t["contract_month"])
        r["statuses"][v["status"]] = r["statuses"].get(v["status"], 0) + 1
        d = v["delta_t"] or 0.0
        r["long_t" if d > 0 else "short_t"] += abs(d) if d else 0.0
        r["net_t"] += d
        if v["status"] in INCLUDED_STATUSES:
            r["valued"] += 1
            r["gross_notional_usd"] += v["notional_usd"]
            r["mtm_usd"] += v["mtm_usd"]
    out = []
    for r in rows.values():
        r["contract_months"] = sorted(r["contract_months"])
        for f in ("long_t", "short_t", "net_t"):
            r[f] = round(r[f], 1)
        for f in ("gross_notional_usd", "mtm_usd"):
            r[f] = round(r[f], 2)
        r["data_complete"] = r["valued"] == r["trades"]
        out.append(r)
    return sorted(out, key=lambda r: -abs(r["net_t"]))


def metric_value(agg: dict | None, metric: str) -> float:
    if agg is None:
        return 0.0
    if metric == "net_tonnes":
        return abs(agg["net_tonnes"])
    if metric == "gross_notional_usd":
        return agg["gross_notional_usd"]
    if metric == "mtm_loss_usd":
        return max(0.0, -agg["mtm_usd"])
    raise ValueError(f"Unknown metric '{metric}'")


def limit_status(utilisation: float, warn_pct: float) -> str:
    if utilisation >= 1.0:
        return "BREACH"
    if utilisation >= warn_pct:
        return "WARN"
    return "OK"


def evaluate_limits(limits: list[dict], aggregates_by_dim: dict) -> list[dict]:
    """Compute utilisation for each configured limit."""
    out = []
    for lim in limits:
        agg = aggregates_by_dim.get(lim["dimension"], {}).get(lim["key"])
        exposure = metric_value(agg, lim["metric"])
        util = exposure / lim["limit_value"] if lim["limit_value"] > 0 else 0.0
        out.append({
            **lim,
            "metric_label": METRICS[lim["metric"]]["label"],
            "exposure": round(exposure, 2),
            "utilisation": round(util, 4),
            "status": limit_status(util, lim["warn_pct"]),
            "data_complete": agg["data_complete"] if agg else True,
            "trade_ids": agg["trade_ids"] if agg else [],
            "incomplete": agg["incomplete"] if agg else [],
            "formula": _limit_formula(lim["metric"]),
        })
    return out


def _limit_formula(metric: str) -> str:
    return {
        "net_tonnes": "Utilisation = |Σ delta-equivalent tonnes| ÷ limit",
        "gross_notional_usd": "Utilisation = Σ |qty × mark × unit factor × FX| ÷ limit (included valuations only)",
        "mtm_loss_usd": "Utilisation = max(0, −Σ MtM USD) ÷ limit (included valuations only)",
    }[metric]


def derivation(trades: list[dict], valuations: dict, dimension: str, key: str,
               today: date) -> dict:
    """Trade-level breakdown behind one aggregate figure."""
    agg = aggregate(trades, valuations, dimension, today).get(key)
    by_id = {t["id"]: t for t in trades}
    rows = []
    for tid in (agg["trade_ids"] if agg else []):
        t, v = by_id[tid], valuations[tid]
        rows.append({
            "trade_id": tid, "commodity": t["commodity"], "instrument_type": t["instrument_type"],
            "direction": t["direction"], "quantity": t["quantity"], "quantity_unit": t["quantity_unit"],
            "contract_month": t["contract_month"], "trade_price": t["price"], "price_unit": t["price_unit"],
            "mark_price": v["mark_price"], "mark_as_of": v["mark_as_of"],
            "delta_t": v["delta_t"], "notional_usd": v["notional_usd"], "mtm_usd": v["mtm_usd"],
            "valuation_status": v["status"], "included_in_totals": v["status"] in INCLUDED_STATUSES,
        })
    return {"dimension": dimension, "key": key, "aggregate": agg, "contributions": rows}
