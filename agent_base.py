"""agent_base.py
Shared utilities for all treasury agents: Gemini caller and BQ memo persistence.
"""
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
import yfinance as yf

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


def _resolve_ticker(ticker_or_name: str) -> str:
    """Internal helper to map common company names to tickers."""
    mapping = {
        "glencore": "GLEN.L",
        "shell": "SHEL",
        "totalenergies": "TTE",
        "bp": "BP",
        "chevron": "CVX",
    }
    return mapping.get(ticker_or_name.lower().strip(), ticker_or_name.upper().strip())


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
        
        headlines = [f"- {item['title']} ({item.get('publisher', 'N/A')})" for item in news[:10]]
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
    """Call Gemini 3.5 Flash and return the text response."""
    if not GEMINI_API_KEY:
        return "ERROR: GEMINI_API_KEY not set."
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # Automatic function calling is enabled by default when tools are provided in a Chat session.
        chat = client.chats.create(
            model="gemini-3.5-flash",
            config=types.GenerateContentConfig(
                tools=[get_headlines_tool, get_stock_price_tool],
                temperature=temperature,
                max_output_tokens=1024,
            ),
        )
        response = chat.send_message(prompt)
        text = response.text or ""
        return text.strip()
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
