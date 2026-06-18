"""bigquery_helper.py
BigQuery client for the Treasury & Commodity Counterparty Analytics platform.

Tables:
  treasury_analytics.counterparties  – financial snapshots per counterparty
  treasury_analytics.agent_memos     – AI agent memos and exposure proposals
"""
import os
from google.cloud import bigquery

DATASET    = os.getenv("BQ_DATASET", "treasury_analytics")
CP_TABLE   = f"{DATASET}.counterparties"
MEMO_TABLE = f"{DATASET}.agent_memos"

_client = None


def get_bq_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client()
    return _client


# ── Counterparties ────────────────────────────────────────────────────────────

def insert_counterparty(record: dict) -> None:
    """Stream a single counterparty financial record into BigQuery."""
    client = get_bq_client()
    errors = client.insert_rows_json(CP_TABLE, [record])
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")


def get_counterparties(search: str = None, sector: str = None) -> list[dict]:
    """Fetch counterparty records, optionally filtered by name or sector."""
    client = get_bq_client()
    query  = f"SELECT * FROM `{CP_TABLE}`"
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
        SELECT * FROM `{CP_TABLE}`
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
        FROM `{CP_TABLE}`
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
    errors = client.insert_rows_json(MEMO_TABLE, [record])
    if errors:
        raise RuntimeError(f"BigQuery memo insert errors: {errors}")


def get_memos(counterparty_name: str = None, agent_type: str = None) -> list[dict]:
    """Retrieve stored agent memos, optionally filtered."""
    client = get_bq_client()
    query  = f"SELECT * FROM `{MEMO_TABLE}`"
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
