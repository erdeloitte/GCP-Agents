"""liquidity_model_agent.py
Liquidity Modelling Agent — 3-week (21-day) cashflow projection.

Ingests one or more operational files (account receivables, payables, taxes,
maturity of financial swaps, margin requirements, deposits, trades, interest
rates) and projects the next 3 weeks of cashflow with a daily balance series
and a weekly summary. A deterministic engine does the arithmetic; the LLM only
writes a concise (<=6 sentence) treasury commentary.

Expected (flexible, case-insensitive) input columns:
  date | due_date | maturity_date | value_date   -> the cashflow date
  amount | value | amount_usd | notional         -> the cashflow amount
  category | type | line_item | flow_type        -> e.g. Receivable, Payable, Tax, Swap, Margin
  direction | flow                               -> inflow / outflow (optional; inferred if absent)
  description | memo                              -> free text (optional)
"""
import csv
import io
import logging
from datetime import datetime, timedelta, timezone, date

from agent_base import call_llm, summarize_to_sentences, STANDARD_REPORT_INSTRUCTION

logger = logging.getLogger(__name__)

HORIZON_DAYS = 21  # 3 weeks

# ── Column alias mapping ────────────────────────────────────────────────────────
_DATE_KEYS   = {"date", "due_date", "duedate", "maturity_date", "maturitydate",
                "value_date", "valuedate", "settlement_date", "settlementdate", "payment_date"}
_AMOUNT_KEYS = {"amount", "value", "amount_usd", "amountusd", "notional", "cashflow",
                "cash_flow", "usd", "amount_usd_m"}
_CAT_KEYS    = {"category", "type", "line_item", "lineitem", "flow_type", "flowtype",
                "instrument", "item"}
_DIR_KEYS    = {"direction", "flow", "inflow_outflow", "sign"}
_DESC_KEYS   = {"description", "memo", "notes", "detail", "counterparty"}

# Category → direction inference (used when no explicit direction column exists).
_INFLOW_HINTS  = ("receivable", "deposit", "interest income", "coupon receive",
                  "collection", "inflow", "credit", "maturity in", "swap receive",
                  "dividend", "revenue", "trade in", "settlement in")
_OUTFLOW_HINTS = ("payable", "tax", "margin", "swap pay", "interest expense",
                  "outflow", "debit", "payment", "fee", "trade out", "settlement out",
                  "principal", "redemption")


def _norm_key(k: str) -> str:
    return (k or "").strip().lower().replace(" ", "_")


def _to_float(v) -> float:
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    if s in ("", "-", "n/a", "na", "none"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")  # accounting negatives
    s = s.strip("()")
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return 0.0


def _parse_date(v) -> date | None:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    # Try common formats
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d",
                "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    # pandas Timestamp / Excel serial fallback
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None


def _infer_direction(category: str, amount: float, explicit: str) -> str:
    """Return 'inflow' or 'outflow'."""
    e = (explicit or "").strip().lower()
    if e in ("in", "inflow", "credit", "+", "receive"):
        return "inflow"
    if e in ("out", "outflow", "debit", "-", "pay"):
        return "outflow"
    c = (category or "").lower()
    if any(h in c for h in _INFLOW_HINTS):
        return "inflow"
    if any(h in c for h in _OUTFLOW_HINTS):
        return "outflow"
    # Fall back to the sign of the amount: positive = inflow.
    return "inflow" if amount >= 0 else "outflow"


def _extract_rows(records: list[dict]) -> list[dict]:
    """Normalize raw dict rows into {date, amount, category, direction, description}."""
    out = []
    for raw in records:
        row = {_norm_key(k): v for k, v in raw.items()}
        d = next((row[k] for k in _DATE_KEYS if k in row and row[k] not in (None, "")), None)
        a = next((row[k] for k in _AMOUNT_KEYS if k in row and row[k] not in (None, "")), None)
        cat = next((row[k] for k in _CAT_KEYS if k in row and row[k] not in (None, "")), "Uncategorized")
        direction = next((row[k] for k in _DIR_KEYS if k in row and row[k] not in (None, "")), "")
        desc = next((row[k] for k in _DESC_KEYS if k in row and row[k] not in (None, "")), "")

        parsed_date = _parse_date(d)
        amount = abs(_to_float(a))
        if parsed_date is None or amount == 0:
            continue
        flow = _infer_direction(str(cat), _to_float(a), str(direction))
        out.append({
            "date": parsed_date,
            "amount": amount,
            "category": str(cat).strip().title(),
            "direction": flow,
            "description": str(desc).strip(),
        })
    return out


def parse_file(content: bytes, filename: str) -> list[dict]:
    """Parse a single uploaded CSV/XLSX file into normalized cashflow rows."""
    name = (filename or "").lower()
    records: list[dict] = []

    if name.endswith(".xlsx") or name.endswith(".xls"):
        try:
            import pandas as pd
            df = pd.read_excel(io.BytesIO(content))
            records = df.to_dict(orient="records")
        except Exception as e:
            logger.warning(f"[LIQ_MODEL] XLSX parse failed for {filename}: {e}")
            return []
    else:
        # CSV / TSV — sniff the delimiter.
        try:
            text = content.decode("utf-8-sig", errors="replace")
            sample = text[:2048]
            delim = ","
            for cand in (",", ";", "\t", "|"):
                if sample.count(cand) >= 1:
                    delim = cand
                    break
            reader = csv.DictReader(io.StringIO(text), delimiter=delim)
            records = list(reader)
        except Exception as e:
            logger.warning(f"[LIQ_MODEL] CSV parse failed for {filename}: {e}")
            return []

    rows = _extract_rows(records)
    logger.info(f"[LIQ_MODEL] Parsed {len(rows)} cashflow rows from {filename}")
    return rows


def project_cashflow(rows: list[dict], opening_balance: float = 0.0,
                     horizon_days: int = HORIZON_DAYS) -> dict:
    """Build a daily + weekly cashflow projection over the horizon."""
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=horizon_days - 1)

    # Daily buckets initialised to zero.
    daily = {today + timedelta(days=i): {"inflow": 0.0, "outflow": 0.0} for i in range(horizon_days)}
    by_category: dict[str, float] = {}
    out_of_window = 0

    for r in rows:
        d = r["date"]
        if d < today or d > end:
            out_of_window += 1
            continue
        signed = r["amount"] if r["direction"] == "inflow" else -r["amount"]
        if r["direction"] == "inflow":
            daily[d]["inflow"] += r["amount"]
        else:
            daily[d]["outflow"] += r["amount"]
        by_category[r["category"]] = by_category.get(r["category"], 0.0) + signed

    # Build the daily running-balance series.
    daily_series = []
    balance = opening_balance
    for i in range(horizon_days):
        d = today + timedelta(days=i)
        inflow = round(daily[d]["inflow"], 2)
        outflow = round(daily[d]["outflow"], 2)
        net = round(inflow - outflow, 2)
        balance = round(balance + net, 2)
        daily_series.append({
            "date": d.isoformat(),
            "inflow": inflow,
            "outflow": outflow,
            "net": net,
            "balance": balance,
        })

    # Weekly summary (3 weeks of 7 days).
    weeks = []
    running = opening_balance
    for w in range(3):
        chunk = daily_series[w * 7:(w + 1) * 7]
        if not chunk:
            continue
        inflow = round(sum(c["inflow"] for c in chunk), 2)
        outflow = round(sum(c["outflow"] for c in chunk), 2)
        net = round(inflow - outflow, 2)
        running = round(running + net, 2)
        start_d = chunk[0]["date"]
        end_d = chunk[-1]["date"]
        weeks.append({
            "week": f"Week {w + 1}",
            "period": f"{start_d} → {end_d}",
            "inflows": inflow,
            "outflows": outflow,
            "net": net,
            "closing_balance": running,
        })

    min_balance = min((d["balance"] for d in daily_series), default=opening_balance)
    return {
        "opening_balance": round(opening_balance, 2),
        "horizon_days": horizon_days,
        "weeks": weeks,
        "daily": daily_series,
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items())},
        "min_balance": min_balance,
        "closing_balance": daily_series[-1]["balance"] if daily_series else opening_balance,
        "rows_used": len(rows) - out_of_window,
        "rows_out_of_window": out_of_window,
    }


def _build_commentary(projection: dict, counterparty: str | None) -> str:
    """Ask the LLM for a concise (<=6 sentence) treasury commentary."""
    weeks_txt = "\n".join(
        f"  {w['week']} ({w['period']}): inflows {w['inflows']:,.0f}, "
        f"outflows {w['outflows']:,.0f}, net {w['net']:,.0f}, closing {w['closing_balance']:,.0f}"
        for w in projection["weeks"]
    )
    cats_txt = "\n".join(f"  {k}: {v:,.0f}" for k, v in projection["by_category"].items())
    who = f" for {counterparty}" if counterparty else ""

    prompt = (
        "You are a corporate treasury liquidity manager. Based on the 3-week cashflow "
        f"projection{who} below, summarize the liquidity outlook.\n\n"
        f"Opening balance: {projection['opening_balance']:,.0f}\n"
        f"Projected closing balance (day 21): {projection['closing_balance']:,.0f}\n"
        f"Lowest projected balance in the period: {projection['min_balance']:,.0f}\n\n"
        f"WEEKLY SUMMARY:\n{weeks_txt}\n\n"
        f"NET BY CATEGORY:\n{cats_txt}\n\n"
        f"{STANDARD_REPORT_INSTRUCTION}\n"
        "Flag any week where the closing or intra-period balance turns negative, identify "
        "the largest cash drains, and give one actionable funding or hedging recommendation."
    )
    raw = call_llm(prompt, temperature=0.3, trace_label="LIQ_MODEL")
    return summarize_to_sentences(raw, max_sentences=6)


def run(files: list[tuple[bytes, str]], opening_balance: float = 0.0,
        counterparty: str | None = None) -> dict:
    """Entry point: parse files, project cashflow, attach LLM commentary.

    Args:
        files: list of (content_bytes, filename) tuples.
        opening_balance: starting cash balance.
        counterparty: optional name (also used to pull BQ deposits as inflows).
    """
    logger.info(f"[LIQ_MODEL] Starting projection: {len(files)} file(s), "
                f"opening_balance={opening_balance}, counterparty={counterparty}")

    all_rows: list[dict] = []
    parsed_files = []
    for content, filename in files:
        rows = parse_file(content, filename)
        parsed_files.append({"filename": filename, "rows": len(rows)})
        all_rows.extend(rows)

    # Pull deposits on file from BigQuery as additional inflows context (best-effort).
    if counterparty:
        try:
            from bigquery_helper import get_deposits_by_counterparty
            deposits = get_deposits_by_counterparty(counterparty)
            for dep in deposits:
                d = _parse_date(dep.get("deposit_date") or dep.get("maturity_date"))
                amt = _to_float(dep.get("amount_usd") or dep.get("amount"))
                if d and amt:
                    all_rows.append({
                        "date": d, "amount": abs(amt), "category": "Deposit (BQ)",
                        "direction": "inflow", "description": "From deposits table",
                    })
            logger.info(f"[LIQ_MODEL] Added {len(deposits)} BQ deposit rows")
        except Exception as e:
            logger.warning(f"[LIQ_MODEL] BQ deposits lookup skipped: {e}")

    projection = project_cashflow(all_rows, opening_balance=opening_balance)

    if not all_rows:
        projection["commentary"] = (
            "No valid cashflow rows were found in the uploaded file(s). Ensure the files "
            "include a date column and an amount column with a recognizable category."
        )
    else:
        projection["commentary"] = _build_commentary(projection, counterparty)

    projection["files"] = parsed_files
    projection["counterparty"] = counterparty
    logger.info(f"[LIQ_MODEL] Projection complete: {projection['rows_used']} rows used, "
                f"closing balance {projection['closing_balance']:,.0f}")
    return projection
