"""market_data_helper.py
Fetch live market data from public sources to enrich chat context.

Supported sources:
  - SEC EDGAR (free, no key)
  - Alpha Vantage (free tier available)
  - Public commodity exchanges
"""
import os
import urllib.request
import urllib.parse
import json
import re

def fetch_company_quote(ticker: str) -> dict | None:
    """Fetch current stock quote from Google Finance by parsing the public page.

    Args:
        ticker: Stock ticker with exchange (e.g., "SHEL:NYSE" or "BP:LON")

    Returns:
        Dict with price, currency, or None if unavailable.
    """
    try:
        # Use Google Finance public URL
        url = f"https://www.google.com/finance/quote/{ticker}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode()
            
            # Google Finance uses data-last-price and data-currency-code for real-time price blocks
            price_match = re.search(r'data-last-price="([\d,.]+)"', html)
            curr_match  = re.search(r'data-currency-code="(\w+)"', html)
            
            if price_match:
                return {
                    "symbol": ticker,
                    "price": price_match.group(1),
                    "currency": curr_match.group(1) if curr_match else "USD"
                }
    except Exception as e:
        # Log fetch error locally; allow agent context to proceed without data on failure
        print(f"Google Finance fetch failed for {ticker}: {e}")
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
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
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
        "glencore": "GLEN:LON",
        "vitol": "PRIVATE",
        "shell": "SHEL:NYSE",
        "totalenergies": "TTE:PAR",
        "bp": "BP:LON",
        "chevron": "CVX:NYSE",
    }
    
    clean_name = company_name.lower().replace(" ", "")
    ticker = ticker_map.get(clean_name, company_name.upper())

    if ticker != "PRIVATE":
        quote = fetch_company_quote(ticker)
        if quote:
            parts.append(
                f"Current Google Finance Data ({ticker}): "
                f"Price {quote['currency']} {quote['price']}"
            )

    # Try SEC filings
    sec_info = fetch_sec_filings(company_name)
    if sec_info:
        parts.append(sec_info)

    return "\n".join(parts) if parts else ""
