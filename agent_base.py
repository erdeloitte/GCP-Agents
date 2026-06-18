"""agent_base.py
Shared utilities for all treasury agents: Gemini caller and BQ memo persistence.
"""
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


def call_gemini(prompt: str, temperature: float = 0.3) -> str:
    """Call Gemini 1.5 Flash and return the text response."""
    if not GEMINI_API_KEY:
        return "ERROR: GEMINI_API_KEY not set."
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            "gemini-1.5-flash",
            generation_config={"temperature": temperature, "max_output_tokens": 1024},
        )
        return model.generate_content(prompt).text.strip()
    except Exception as e:
        return f"ERROR: {e}"


def save_memo(record: dict) -> None:
    """Persist an agent memo to BigQuery. Non-fatal if BQ is unavailable."""
    try:
        from bigquery_helper import get_bq_client, _memo_table
        client = get_bq_client()
        client.insert_rows_json(_memo_table(), [record])
    except Exception:
        pass


def build_memo_record(
    counterparty: str,
    agent_type: str,
    risk_level: str,
    memo: str,
    exposure_proposal: str,
) -> dict:
    return {
        "id":                str(uuid.uuid4()),
        "counterparty_name": counterparty,
        "agent_type":        agent_type,
        "risk_level":        risk_level.upper(),
        "memo":              memo,
        "exposure_proposal": exposure_proposal,
        "created_at":        datetime.now(timezone.utc).isoformat(),
    }
