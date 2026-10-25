"""agent_base.py
Shared utilities for treasury agents: LLM calls (Claude, via llm_providers.py)
and web search via Tavily.
"""
import os
import uuid
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


TICKER_MAP: dict[str, str] = {
    "glencore":      "GLEN.L",
    "shell":         "SHEL",
    "totalenergies": "TTE",
    "total energies": "TTE",
    "bp":            "BP",
    "chevron":       "CVX",
    "vitol":         "PRIVATE",
    "trafigura":     "PRIVATE",
    "gunvor":        "PRIVATE",
    "mercuria":      "PRIVATE",
    "louis dreyfus": "PRIVATE",
    "cargill":       "PRIVATE",
    "exxon":         "XOM",
    "exxonmobil":    "XOM",
    "conocophillips": "COP",
    "equinor":       "EQNR",
    "eni":           "E",
}


ADVISORY_NOTICE = (
    "Advisory AI output — risk level and exposure proposal are LLM opinions, "
    "not authoritative figures. They do not set limits or approve exposure."
)


# ── Standardized reporting ─────────────────────────────────────────────────────
# Every agent appends this so memos stay consistent, concise, and actionable.
STANDARD_REPORT_INSTRUCTION = (
    "REPORTING RULES (mandatory):\n"
    "- Write a MAXIMUM of 6 concise sentences summarizing the key findings.\n"
    "- Be quantitative: cite the specific ratios, figures, or facts that drive the verdict.\n"
    "- End with a clear, actionable conclusion on exposure/credit capacity.\n"
    "- No bullet lists, no headings, no preamble — just the concise narrative followed "
    "by the required verdict lines.\n"
)


def summarize_to_sentences(text: str, max_sentences: int = 6) -> str:
    """Trim a memo to at most `max_sentences` sentences as a safety net.

    The prompt already asks the model for <= 6 sentences; this guarantees it even
    if the model over-produces. Sentence boundaries are detected on . ! ? followed
    by whitespace, preserving the trailing punctuation.
    """
    import re
    if not text:
        return text
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    parts = [p for p in parts if p.strip()]
    if len(parts) <= max_sentences:
        return text.strip()
    trimmed = " ".join(parts[:max_sentences]).strip()
    logger.info(f"[REPORT] Trimmed memo from {len(parts)} to {max_sentences} sentences")
    return trimmed


def call_llm(prompt: str, temperature: float = 0.3, trace_label: str = "LLM") -> str:
    """Call Claude via llm_providers.py.

    Args:
        prompt: The full prompt.
        temperature: Sampling temperature.
        trace_label: A label (e.g. "MARKET") tagging traceability logs so each
            agent's reasoning is identifiable in the logs.
    Returns the model text, or a string starting with "ERROR:" that carries a
    non-sensitive diagnostic code.
    """
    import llm_providers
    # Traceability: log a preview of what the model is being asked to reason over.
    logger.info(f"[{trace_label}] Prompt dispatched ({len(prompt)} chars). Preview: "
                f"{prompt[:280].replace(chr(10), ' ')}…")
    result = llm_providers.generate(prompt, temperature=temperature, trace_label=trace_label)
    if result.ok:
        logger.info(f"[{trace_label}] output ({len(result.text)} chars): "
                    f"{result.text[:500].replace(chr(10), ' ')}…")
        return result.text
    return f"ERROR: No LLM available ({result.diagnostic['code']})"


def web_search(query: str, max_results: int = 3) -> str:
    """Search the web using Tavily. Returns formatted results or empty string if unavailable."""
    if not TAVILY_API_KEY:
        return ""

    try:
        from tavily import Client
        client = Client(api_key=TAVILY_API_KEY)
        logger.info(f"[WEB] Searching Tavily: {query}")
        response = client.search(query, max_results=max_results)

        if not response.get("results"):
            return ""

        results = []
        for r in response["results"][:max_results]:
            results.append(f"- {r.get('title', 'N/A')}: {r.get('content', '')[:200]}")

        result_text = "\n".join(results)
        logger.info(f"[WEB] Found {len(response['results'])} results")
        return result_text
    except Exception as e:
        logger.warning(f"[WEB] Tavily search failed: {e}")
        return ""


def save_memo(record: dict) -> None:
    """Persist an agent memo to BigQuery. Non-fatal if BQ is unavailable."""
    try:
        from bigquery_helper import save_memo as bq_save_memo
        bq_save_memo(record)
    except Exception as exc:
        logger.warning("save_memo: BigQuery write failed — %s", exc)


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
        # LLM verdicts (risk level, exposure proposal) are opinions, not system
        # figures; they must not feed limits, valuations or approvals.
        "advisory":          True,
        "advisory_notice":   ADVISORY_NOTICE,
    }
