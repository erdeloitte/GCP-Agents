"""agri/valuation.py
Deterministic, explainable trade valuation for the prototype.

Every result carries its inputs, units, formula, assumptions and a status:
  OK              – current mark available, value included in totals
  STALE           – mark older than the staleness threshold; value shown but
                    EXCLUDED from totals until the exception is overridden
  STALE_ACCEPTED  – stale mark accepted via a recorded manual override
  MISSING         – no mark available; no MtM is produced
  EXCLUDED        – trade not part of open exposure (cancelled, rejected,
                    or past maturity)

PROTOTYPE ONLY: simplified methodology, synthetic market data. Not suitable
for live trading, financial reporting or regulatory use.
"""
from datetime import datetime, date

from agri import reference as ref

INCLUDED_STATUSES = {"OK", "STALE_ACCEPTED"}
DEFAULT_STALE_HOURS = 24.0

GENERAL_ASSUMPTIONS = [
    "Synthetic demo marks; not sourced from an approved market-data provider.",
    "No discounting, margining or funding adjustments.",
    "FX converted to USD at fixed synthetic demo rates.",
]
OPTION_ASSUMPTIONS = [
    "Option value is intrinsic only — time value is ignored, so out-of-the-money "
    "options show zero value and total value is understated.",
    "Option delta approximated as 1 if in-the-money, otherwise 0.",
]


# Short form shown next to every valuation figure in the UI.
METHODOLOGY = [
    "Options at intrinsic value only (no time value)",
    "No discounting, margining or funding adjustments",
    "Fixed synthetic FX rates to USD",
    "Synthetic demo marks",
]


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def is_matured(trade: dict, today: date) -> bool:
    return date.fromisoformat(trade["maturity_date"]) < today


def value_trade(trade: dict, mark: dict | None, as_of: datetime,
                stale_hours: float = DEFAULT_STALE_HOURS,
                stale_accepted: bool = False) -> dict:
    """Value a single trade against a market mark.

    Args:
        trade: trade dict (see models.Trade).
        mark: market price dict for the trade's commodity/contract month, or None.
        as_of: valuation timestamp (timezone-aware).
        stale_hours: age after which a mark is considered stale.
        stale_accepted: True if a stale-price override has been recorded.
    """
    spec = ref.COMMODITIES[trade["commodity"]]
    ccy = spec["currency"]
    fx = ref.FX_TO_USD[ccy]
    factor = spec["price_to_ccy"]
    tpu = ref.tonnes_per_unit(trade["commodity"])
    sign = 1 if trade["direction"] == "BUY" else -1
    qty = float(trade["quantity"])
    qty_t = qty * tpu
    is_option = trade["instrument_type"] == "OPTION"

    out = {
        "trade_id": trade["id"],
        "status": "OK",
        "status_reason": "",
        "currency": ccy,
        "reporting_ccy": ref.REPORTING_CCY,
        "mtm_ccy": None,
        "mtm_usd": None,
        "quantity_t": round(qty_t, 3),
        "delta_t": None,
        "notional_usd": None,
        "net_notional_usd": None,
        "mark_price": None,
        "mark_as_of": None,
        "mark_age_hours": None,
        "formula": "",
        "inputs": [
            {"name": "Trade price" if not is_option else "Premium paid/received",
             "value": trade["price"], "unit": spec["price_unit"]},
            {"name": "Quantity", "value": qty, "unit": spec["qty_unit"]},
            {"name": "Direction", "value": trade["direction"], "unit": f"sign {sign:+d}"},
            {"name": "Tonnes per unit", "value": tpu, "unit": f"t/{spec['qty_unit']}"},
            {"name": "Price unit → currency", "value": factor, "unit": f"{ccy} per ({spec['price_unit']} × {spec['qty_unit']})"},
            {"name": f"FX {ccy}→USD", "value": fx, "unit": ref.FX_AS_OF_LABEL},
        ],
        "assumptions": list(GENERAL_ASSUMPTIONS) + (OPTION_ASSUMPTIONS if is_option else []),
        "valued_at": as_of.isoformat(),
    }
    if is_option:
        out["inputs"].insert(1, {"name": f"Strike ({trade.get('option_type')})",
                                 "value": trade.get("strike"), "unit": spec["price_unit"]})

    if trade["status"] not in ref.RISK_STATUSES:
        out.update(status="EXCLUDED", status_reason=f"Trade status {trade['status']}")
        return out
    if is_matured(trade, as_of.date()):
        out.update(status="EXCLUDED", status_reason="Past maturity — awaiting close-out")
        return out

    # Delta in tonnes does not depend on price for linear instruments.
    if not is_option:
        out["delta_t"] = round(sign * qty_t, 3)

    if mark is None:
        out.update(status="MISSING",
                   status_reason=f"No mark for {trade['commodity']} {trade['contract_month']}")
        out["formula"] = "Not calculated — market input missing."
        return out

    m = float(mark["price"])
    age_h = (as_of - _parse_ts(mark["as_of"])).total_seconds() / 3600.0
    out.update(mark_price=m, mark_as_of=mark["as_of"], mark_age_hours=round(age_h, 1))
    out["inputs"].insert(0, {"name": "Mark price", "value": m, "unit": spec["price_unit"],
                             "as_of": mark["as_of"], "source": mark.get("source", "")})

    if is_option:
        k = float(trade["strike"])
        intrinsic = max(m - k, 0.0) if trade["option_type"] == "CALL" else max(k - m, 0.0)
        delta = 1.0 if intrinsic > 0 else 0.0
        mtm_ccy = (intrinsic - float(trade["price"])) * qty * factor * sign
        out["delta_t"] = round(sign * qty_t * delta, 3) + 0.0
        out["formula"] = ("MtM = (intrinsic − premium) × qty × unit factor × sign; "
                          f"intrinsic = {'max(mark − strike, 0)' if trade['option_type'] == 'CALL' else 'max(strike − mark, 0)'}"
                          f" = {intrinsic:g} {spec['price_unit']}")
        notional_ccy = qty * m * factor
        net_notional_ccy = sign * notional_ccy * delta
    else:
        mtm_ccy = (m - float(trade["price"])) * qty * factor * sign
        out["formula"] = "MtM = (mark − trade price) × qty × unit factor × sign"
        notional_ccy = qty * m * factor
        net_notional_ccy = sign * notional_ccy
    out["formula"] += f"; USD = {ccy} × FX {fx:g}"

    # "+ 0.0" normalises -0.0 to 0.0 for display
    out["mtm_ccy"] = round(mtm_ccy, 2) + 0.0
    out["mtm_usd"] = round(mtm_ccy * fx, 2) + 0.0
    out["notional_usd"] = round(notional_ccy * fx, 2)
    out["net_notional_usd"] = round(net_notional_ccy * fx, 2)

    if age_h > stale_hours:
        if stale_accepted:
            out.update(status="STALE_ACCEPTED",
                       status_reason=f"Mark is {age_h:.0f}h old; stale price accepted by recorded override")
        else:
            out.update(status="STALE",
                       status_reason=f"Mark is {age_h:.0f}h old (threshold {stale_hours:g}h); excluded from totals")
    return out
