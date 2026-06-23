"""agent_base.py
Shared utilities for all treasury agents: Gemini caller and BQ memo persistence.
"""
import os
import uuid
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import yfinance as yf

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.0-flash"

# ---------------------------------------------------------------------------
# Module-level Gemini client singleton — created once, reused across calls.
# ---------------------------------------------------------------------------
_gemini_client = None

def _get_gemini_client():
    """Return (or lazily create) the shared Gemini SDK client."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


class AgentResponse(str):
    def __new__(cls, value, search_queries=None, search_sources=None, tool_calls=None):
        obj = str.__new__(cls, value)
        obj.search_queries = search_queries or []
        obj.search_sources = search_sources or []
        obj.tool_calls = tool_calls or []
        return obj


# Canonical ticker mapping — single source of truth used by both agent_base and market_data_helper.
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


def _resolve_ticker(ticker_or_name: str) -> str:
    """Map common company names to stock tickers."""
    key = ticker_or_name.lower().strip()
    # Try exact match first, then partial match
    if key in TICKER_MAP:
        return TICKER_MAP[key]
    for name, ticker in TICKER_MAP.items():
        if name in key:
            return ticker
    return ticker_or_name.upper().strip()


def get_headlines_tool(ticker_or_name: str) -> str:
    """
    Retrieves the last 10 news headlines for a ticker or company name from Yahoo Finance.
    Args:
        ticker_or_name: The stock ticker (e.g., 'SHEL') or common company name.
    """
    try:
        ticker = _resolve_ticker(ticker_or_name)
        stock = yf.Ticker(ticker)
        news = stock.news
        if not news:
            return f"No recent headlines found for {ticker}."
        
        headlines = []
        for item in news[:10]:
            content = item.get('content', item)
            title = content.get('title', 'No Title')
            provider = content.get('provider', {})
            publisher = provider.get('displayName', content.get('publisher', 'N/A'))
            headlines.append(f"- {title} ({publisher})")
        return "\n".join(headlines)
    except Exception as e:
        return f"Error fetching news for {ticker_or_name}: {e}"


def get_stock_price_tool(ticker_or_name: str) -> str:
    """
    Retrieves the current stock price and currency for a ticker or company name.
    Args:
        ticker_or_name: The stock ticker (e.g., 'BP') or common company name.
    """
    try:
        ticker = _resolve_ticker(ticker_or_name)
        stock = yf.Ticker(ticker)
        price = stock.info.get('currentPrice') or stock.info.get('regularMarketPrice')
        currency = stock.info.get('currency', 'USD')
        return f"Current Price for {ticker}: {currency} {price:.2f}" if price else f"Price data unavailable for {ticker_or_name}."
    except Exception as e:
        return f"Error fetching price for {ticker_or_name}: {e}"


def call_gemini(prompt: str, temperature: float = 0.3) -> str:
    """Call Gemini and return the text response (as AgentResponse)."""
    if not GEMINI_API_KEY:
        return AgentResponse("ERROR: GEMINI_API_KEY not set.")
    try:
        from google.genai import types
        client = _get_gemini_client()

        # Automatic function calling is enabled by default when tools are provided in a Chat session.
        chat = client.chats.create(
            model=GEMINI_MODEL,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}, get_headlines_tool, get_stock_price_tool],
                tool_config=types.ToolConfig(
                    include_server_side_tool_invocations=True
                ),
                temperature=temperature,
                max_output_tokens=2048,
            ),
        )
        response = chat.send_message(prompt)
        text = response.text or ""
        
        # Extract search grounding queries and sources
        search_queries = []
        search_sources = []
        tool_calls_logged = []
        
        gm = response.candidates[0].grounding_metadata if response.candidates else None
        if gm:
            if gm.web_search_queries:
                search_queries = list(gm.web_search_queries)
            if getattr(gm, 'grounding_chunks', None):
                for chunk in gm.grounding_chunks:
                    if chunk.web:
                        search_sources.append({
                            "title": chunk.web.title,
                            "uri": chunk.web.uri
                        })
                        
        # Extract tool calls from chat history
        history = chat.get_history()
        for msg in history:
            for part in msg.parts:
                # 1. Custom client-side function calls
                if part.function_call:
                    tool_calls_logged.append(
                        f"Agent called tool '{part.function_call.name}' with arguments {part.function_call.args}"
                    )
                elif part.function_response:
                    resp_str = str(part.function_response.response)
                    if len(resp_str) > 200:
                        resp_str = resp_str[:200] + "..."
                    tool_calls_logged.append(
                        f"Tool '{part.function_response.name}' returned: {resp_str}"
                    )
                # 2. Built-in server-side tool calls (e.g. Google Search grounding)
                elif part.tool_call:
                    if getattr(part.tool_call, "tool_type", None) and part.tool_call.tool_type.name == "GOOGLE_SEARCH_WEB":
                        queries = part.tool_call.args.get("queries", []) if part.tool_call.args else []
                        if queries:
                            search_queries.extend(queries)
                            tool_calls_logged.append(
                                f"Agent queried Google Search with: {', '.join([repr(q) for q in queries])}"
                            )
                    
        return AgentResponse(
            text.strip(),
            search_queries=search_queries,
            search_sources=search_sources,
            tool_calls=tool_calls_logged
        )
    except Exception as e:
        return AgentResponse(f"ERROR: {e}")


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
