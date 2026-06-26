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


def call_llm(prompt: str, temperature: float = 0.3) -> str:
    """Call Gemini directly. Fallback to Claude if Gemini fails."""
    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info(f"[LLM] Calling Gemini ({GEMINI_MODEL})")
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"temperature": temperature, "max_output_tokens": 1000},
            )
            text = response.text or ""
            logger.info(f"[LLM] Gemini response: {len(text)} chars")
            return text.strip()
        except Exception as e:
            logger.warning(f"[LLM] Gemini failed: {e}, trying Claude fallback")

    if ANTHROPIC_API_KEY:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            logger.info("[LLM] Calling Claude (fallback)")
            response = client.messages.create(
                model="claude-haiku-4-5@20251001",
                max_tokens=2048,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text if response.content else ""
            logger.info(f"[LLM] Claude response: {len(text)} chars")
            return text.strip()
        except Exception as e:
            logger.error(f"[LLM] Claude failed: {e}")

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


## Testing apis
_gemmini = call_llm("What is AI in one sentence", temperature=0.3)
_claude = call_llm("What is AI in one sentence", temperature=0.3 )

print(f'gemmini: {_gemmini}')
print(f'claude:, {_claude}')

def call_gemini(prompt: str, temperature: float = 0.3) -> str:
    """Alias for call_llm (backward compatibility)."""
    return call_llm(prompt, temperature)


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
