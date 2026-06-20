"""bigquery_helper.py
BigQuery client for the Treasury & Commodity Counterparty Analytics platform.

Tables:
  treasury_analytics.counterparties  – financial snapshots per counterparty
  treasury_analytics.agent_memos     – AI agent memos and exposure proposals
"""
import os
from google.cloud import bigquery

_client = None


def _dataset() -> str:
    return os.getenv("BQ_DATASET", "treasury_analytics")

def _cp_table() -> str:
    return f"{_dataset()}.counterparties"

def _memo_table() -> str:
    return f"{_dataset()}.agent_memos"

def _deposits_table() -> str:
    return f"{_dataset()}.deposits"

# Keep DATASET as a convenience alias (read lazily via property-like helper)
DATASET = property(_dataset)


def get_bq_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client()
    return _client


# ── Counterparties ────────────────────────────────────────────────────────────

def insert_counterparty(record: dict) -> None:
    """Stream a single counterparty financial record into BigQuery."""
    client = get_bq_client()
    errors = client.insert_rows_json(_cp_table(), [record])
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")


def get_counterparties(search: str = None, sector: str = None) -> list[dict]:
    """Fetch counterparty records, optionally filtered by name or sector."""
    client = get_bq_client()
    query  = f"SELECT * FROM `{_cp_table()}`"
    params, conditions = [], []

    if search:
        conditions.append("LOWER(company_name) LIKE @search")
        params.append(bigquery.ScalarQueryParameter("search", "STRING", f"%{search.lower()}%"))
    if sector:
        conditions.append("sector = @sector")
        params.append(bigquery.ScalarQueryParameter("sector", "STRING", sector))

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY upload_date DESC"

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    return [dict(row) for row in client.query(query, job_config=job_config)]


def get_counterparty_detail(counterparty_name: str) -> dict | None:
    """Return the most recent financial record for a specific counterparty."""
    client = get_bq_client()
    query  = f"""
        SELECT * FROM `{_cp_table()}`
        WHERE LOWER(company_name) = LOWER(@name)
        ORDER BY upload_date DESC
        LIMIT 1
    """
    params = [bigquery.ScalarQueryParameter("name", "STRING", counterparty_name)]
    rows   = list(client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)))
    return dict(rows[0]) if rows else None


def get_summary_stats() -> dict:
    """Return aggregate KPIs across all counterparties for the dashboard header."""
    client = get_bq_client()
    query  = f"""
        SELECT
            COUNT(DISTINCT company_name)        AS total_counterparties,
            ROUND(AVG(ebitda_margin_pct), 1)    AS avg_ebitda_margin,
            ROUND(AVG(debt_to_equity), 2)       AS avg_debt_to_equity,
            ROUND(AVG(current_ratio), 2)        AS avg_current_ratio,
            ROUND(SUM(revenue_usd_m), 1)        AS total_revenue_usd_m
        FROM `{_cp_table()}`
    """
    rows = list(client.query(query))
    return dict(rows[0]) if rows else {}


def build_llm_context(company_name: str = None) -> str:
    """Build a compact text summary of BQ data to inject as LLM context."""
    rows = get_counterparties(search=company_name)
    if not rows:
        return "No counterparty data found in the database."

    lines = ["Treasury & Commodity Counterparty Database — Financial Snapshot\n"]
    for r in rows[:20]:
        lines.append(
            f"- {r.get('company_name')} ({r.get('country')}, {r.get('sector')}): "
            f"FY{r.get('period_year')} | Revenue ${r.get('revenue_usd_m')}M | "
            f"EBITDA {r.get('ebitda_margin_pct')}% | D/E {r.get('debt_to_equity')} | "
            f"Current Ratio {r.get('current_ratio')} | Rating {r.get('credit_rating')}"
        )
    return "\n".join(lines)


# ── Agent Memos ───────────────────────────────────────────────────────────────

def save_memo(record: dict) -> None:
    """Persist an agent memo to the agent_memos table."""
    client = get_bq_client()
    allowed_keys = {"id", "counterparty_name", "agent_type", "risk_level", "memo", "exposure_proposal", "created_at"}
    clean_record = {k: v for k, v in record.items() if k in allowed_keys}
    errors = client.insert_rows_json(_memo_table(), [clean_record])
    if errors:
        raise RuntimeError(f"BigQuery memo insert errors: {errors}")


def get_memos(counterparty_name: str = None, agent_type: str = None) -> list[dict]:
    """Retrieve stored agent memos, optionally filtered."""
    client = get_bq_client()
    query  = f"SELECT * FROM `{_memo_table()}`"
    params, conditions = [], []

    if counterparty_name:
        conditions.append("LOWER(counterparty_name) LIKE @cp")
        params.append(bigquery.ScalarQueryParameter("cp", "STRING", f"%{counterparty_name.lower()}%"))
    if agent_type:
        conditions.append("agent_type = @at")
        params.append(bigquery.ScalarQueryParameter("at", "STRING", agent_type))

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT 50"

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(client.query(query, job_config=job_config))
    return [dict(r) for r in rows]


# ── Deposits & Enhanced Indicators ─────────────────────────────────────────────

def get_deposits_by_counterparty(counterparty_name: str = None) -> list[dict]:
    """Retrieve deposit records, optionally filtered by counterparty."""
    client = get_bq_client()
    try:
        query = f"SELECT * FROM `{_deposits_table()}`"
        params, conditions = [], []

        if counterparty_name:
            conditions.append("LOWER(counterparty_name) LIKE @cp")
            params.append(bigquery.ScalarQueryParameter("cp", "STRING", f"%{counterparty_name.lower()}%"))

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY deposit_date DESC LIMIT 100"

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(client.query(query, job_config=job_config))
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_deposits_aggregate(counterparty_name: str = None) -> dict:
    """Get aggregate deposit statistics for dashboard indicators."""
    client = get_bq_client()
    try:
        query = f"""
            SELECT
                LOWER(counterparty_name) as counterparty,
                COUNT(*) as num_deposits,
                SUM(amount_usd) as total_deposits_usd,
                AVG(amount_usd) as avg_deposit_usd,
                MAX(deposit_date) as last_deposit_date
            FROM `{_deposits_table()}`
        """
        params, conditions = [], []

        if counterparty_name:
            conditions.append("LOWER(counterparty_name) = LOWER(@cp)")
            params.append(bigquery.ScalarQueryParameter("cp", "STRING", counterparty_name))

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " GROUP BY counterparty"

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(client.query(query, job_config=job_config))
        return dict(rows[0]) if rows else {}
    except Exception:
        return {}


def get_counterparty_indicators(counterparty_name: str) -> dict:
    """Get enhanced indicators for a specific counterparty (Task 4)."""
    cp_data = get_counterparty_detail(counterparty_name)
    if not cp_data:
        return {"error": "Counterparty not found"}

    deposits_agg = get_deposits_aggregate(counterparty_name)

    # Calculate additional metrics
    revenue = cp_data.get("revenue_usd_m", 0)
    ebitda = cp_data.get("ebitda_usd_m", 0)
    net_income = cp_data.get("net_income_usd_m", 0)
    assets = cp_data.get("total_assets_usd_m", 0)
    debt = cp_data.get("total_debt_usd_m", 0)
    equity = assets - debt

    return {
        "company_name": cp_data.get("company_name"),
        "country": cp_data.get("country"),
        "sector": cp_data.get("sector"),
        "credit_rating": cp_data.get("credit_rating", "N/A"),
        "period_year": cp_data.get("period_year"),
        # Financial metrics
        "revenue_usd_m": revenue,
        "ebitda_usd_m": ebitda,
        "ebitda_margin_pct": cp_data.get("ebitda_margin_pct", 0),
        "net_income_usd_m": net_income,
        "net_margin_pct": round((net_income / revenue * 100) if revenue else 0, 2),
        # Balance sheet
        "total_assets_usd_m": assets,
        "total_debt_usd_m": debt,
        "equity_usd_m": equity,
        # Leverage & liquidity
        "debt_to_equity": cp_data.get("debt_to_equity", 0),
        "debt_to_assets": round(debt / assets if assets > 0 else 0, 2),
        "current_ratio": cp_data.get("current_ratio", 0),
        # Cash equivalents (approximation)
        "cash_equivalents_estimate_pct": round(100 / cp_data.get("current_ratio", 1.0), 2),
        # Deposits
        "total_deposits_usd": deposits_agg.get("total_deposits_usd", 0),
        "num_deposits": deposits_agg.get("num_deposits", 0),
        "last_deposit_date": str(deposits_agg.get("last_deposit_date", "N/A")),
    }
