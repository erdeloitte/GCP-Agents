"""agent_base.py
Shared utilities for treasury agents: Gemini + Claude with web search via Tavily.
Direct SDK calls (no frameworks), automatic fallback to Claude when Gemini fails.
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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
GEMINI_MODEL = "gemini-3.5-flash"


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
    """Call Gemini directly, falling back to Claude. Logs full reasoning trace.

    Args:
        prompt: The full prompt.
        temperature: Sampling temperature.
        trace_label: A label (e.g. "MARKET") tagging traceability logs so each
            agent's reasoning is identifiable in the logs.
    """
    # Traceability: log a preview of what the model is being asked to reason over.
    logger.info(f"[{trace_label}] Prompt dispatched ({len(prompt)} chars). Preview: "
                f"{prompt[:280].replace(chr(10), ' ')}…")

    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info(f"[{trace_label}] Reasoning via Gemini ({GEMINI_MODEL}), temp={temperature}")
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"temperature": temperature, "max_output_tokens": 2000},
            )
            text = response.text or ""
            logger.info(f"[{trace_label}] Gemini reasoning output ({len(text)} chars): "
                        f"{text[:500].replace(chr(10), ' ')}…")
            return text.strip()
        except Exception as e:
            logger.warning(f"[{trace_label}] Gemini failed: {e} — falling back to Claude")

    if ANTHROPIC_API_KEY:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            logger.info(f"[{trace_label}] Reasoning via Claude (fallback), temp={temperature}")
            response = client.messages.create(
                model="claude-haiku-4-5@20251001",
                max_tokens=2048,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text if response.content else ""
            logger.info(f"[{trace_label}] Claude reasoning output ({len(text)} chars): "
                        f"{text[:500].replace(chr(10), ' ')}…")
            return text.strip()
        except Exception as e:
            logger.error(f"[{trace_label}] Claude failed: {e}")

    return "ERROR: No LLM available (set GEMINI_API_KEY or ANTHROPIC_API_KEY)"


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


def call_gemini(prompt: str, temperature: float = 0.3, trace_label: str = "LLM") -> str:
    """Alias for call_llm (backward compatibility)."""
    return call_llm(prompt, temperature, trace_label=trace_label)


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
    }
