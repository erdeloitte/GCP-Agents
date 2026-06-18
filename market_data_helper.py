"""market_data_helper.py
Fetch live market data from public sources to enrich chat context.

Supported sources:
  - Yahoo Finance (requires API key)
  - SEC EDGAR (free, no key)
  - Alpha Vantage (free tier available)
  - Public commodity exchanges
"""
import os
import urllib.request
import urllib.parse
import json


YAHOO_API_KEY = os.getenv("YAHOO_API_KEY", "")


def fetch_company_quote(symbol: str) -> dict | None:
    """Fetch current stock quote and basic info from Yahoo Finance.

    Args:
        symbol: Stock ticker (e.g., "GLENX" or company name to look up)

    Returns:
        Dict with price, change, market cap, or None if unavailable.
    """
    if not YAHOO_API_KEY:
        return None

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        req = urllib.request.Request(url, headers={"User-Agent": "Python"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if data.get("chart", {}).get("result"):
                result = data["chart"]["result"][0]
                quote = result.get("meta", {})
                return {
                    "symbol": quote.get("symbol"),
                    "price": quote.get("regularMarketPrice"),
                    "currency": quote.get("currency"),
                    "market_cap": quote.get("marketCap"),
                    "52_week_high": quote.get("fiftyTwoWeekHigh"),
                    "52_week_low": quote.get("fiftyTwoWeekLow"),
                }
    except Exception:
        pass
    return None


def fetch_sec_filings(company_name: str) -> str:
    """Fetch recent SEC filings for a company (free, no key required).

    Args:
        company_name: Full company name or CIK number

    Returns:
        Summary of recent 10-K and 10-Q filings.
    """
    try:
        # SEC EDGAR company search endpoint
        search_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={urllib.parse.quote(company_name)}&type=10-K%7C10-Q&dateb=&owner=exclude&count=10&search_text="
        req = urllib.request.Request(search_url, headers={"User-Agent": "Python"})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode()
            # Parse HTML for filing links (simplified)
            if "10-K" in html or "10-Q" in html:
                return f"Recent SEC filings found for {company_name}. Access via sec.gov for detailed financials."
    except Exception:
        pass
    return ""


def build_market_context(company_name: str) -> str:
    """Assemble market data context from available sources."""
    parts = []

    # Try stock quote
    # Handle company name to ticker mapping (simplified)
    ticker_map = {
        "glencore": "GLDRX",
        "vitol": "VITOL",
        "shell": "SHEL",
        "totalenergies": "TTE",
        "bp": "BP",
        "chevron": "CVX",
    }
    ticker = ticker_map.get(company_name.lower().replace(" ", ""), company_name.upper())
    quote = fetch_company_quote(ticker)
    if quote:
        parts.append(
            f"Current Market Data ({ticker}): "
            f"Price ${quote['price']} | "
            f"52W High ${quote['52_week_high']} | "
            f"52W Low ${quote['52_week_low']}"
        )

    # Try SEC filings
    sec_info = fetch_sec_filings(company_name)
    if sec_info:
        parts.append(sec_info)

    return "\n".join(parts) if parts else ""
