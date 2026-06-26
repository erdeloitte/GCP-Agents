"""agent_base.py
Shared utilities for all treasury agents: Gemini + Claude LLM orchestration with CrewAI.
Provides LLM routing (Gemini primary, Claude fallback) and agent creation helpers.
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
GEMINI_MODEL = "gemini-2.0-flash"

# Canonical ticker mapping for market data lookups
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


def get_llm():
    """Return the primary LLM (Gemini) configured for CrewAI."""
    if not GEMINI_API_KEY:
        logger.warning("[LLM] GEMINI_API_KEY not set, falling back to Claude")
        return _get_claude_llm()

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            api_key=GEMINI_API_KEY,
            temperature=0.3,
        )
        logger.info(f"[LLM] Gemini LLM initialized: {GEMINI_MODEL}")
        return llm
    except Exception as e:
        logger.error(f"[LLM] Gemini initialization failed: {e}, falling back to Claude")
        return _get_claude_llm()


def _get_claude_llm():
    """Return Claude as fallback LLM."""
    if not ANTHROPIC_API_KEY:
        error_msg = "ANTHROPIC_API_KEY not set and Gemini unavailable"
        logger.error(f"[LLM] {error_msg}")
        raise RuntimeError(error_msg)

    try:
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(
            model="claude-3-5-sonnet-20241022",
            api_key=ANTHROPIC_API_KEY,
            temperature=0.3,
        )
        logger.info("[LLM] Claude LLM initialized (fallback)")
        return llm
    except Exception as e:
        logger.error(f"[LLM] Claude initialization failed: {e}")
        raise


def call_gemini(prompt: str, temperature: float = 0.3) -> str:
    """Call Gemini directly (no CrewAI). Fallback to Claude if Gemini fails."""
    try:
        if GEMINI_API_KEY:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info(f"[GEMINI] Calling {GEMINI_MODEL}")
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"temperature": temperature, "max_output_tokens": 2048},
            )
            text = response.text or ""
            logger.info(f"[GEMINI] Response received, length: {len(text)} characters")
            return text.strip()
    except Exception as e:
        logger.warning(f"[GEMINI] Call failed: {e}, trying Claude fallback")

    try:
        if ANTHROPIC_API_KEY:
            from anthropic import Anthropic
            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            logger.info("[CLAUDE] Calling Claude as fallback")
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2048,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text if response.content else ""
            logger.info(f"[CLAUDE] Response received, length: {len(text)} characters")
            return text.strip()
    except Exception as e:
        logger.error(f"[CLAUDE] Fallback failed: {e}")

    return "ERROR: All LLM backends unavailable"


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
