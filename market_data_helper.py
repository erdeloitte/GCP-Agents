"""market_data_helper.py
Fetch live market data from public sources to enrich chat context.

Supported sources:
  - SEC EDGAR (free, no key)
  - Yahoo Finance (via yfinance)
"""
import urllib.request
import urllib.parse
import yfinance as yf

def fetch_company_quote(ticker: str) -> dict | None:
    """Fetch current stock quote from Yahoo Finance.

    Args:
        ticker: Stock ticker (e.g., "SHEL", "BP", "CVX")

    Returns:
        Dict with price, currency, or None if unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        price = stock.info.get('currentPrice') or stock.info.get('regularMarketPrice')
        currency = stock.info.get('currency', 'USD')
        if price:
            return {
                "symbol": ticker,
                "price": f"{price:.2f}",
                "currency": currency
            }
    except Exception as e:
        print(f"Yahoo Finance fetch failed for {ticker}: {e}")
    return None

def fetch_sec_filings(company_name: str) -> str:
    """Check for recent SEC filings presence."""
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
        "glencore": "GLEN.L",
        "vitol": "PRIVATE",
        "shell": "SHEL",
        "totalenergies": "TTE",
        "bp": "BP",
        "chevron": "CVX",
    }
    
    clean_name = company_name.lower().replace(" ", "")
    ticker = ticker_map.get(clean_name, company_name.upper())

    if ticker != "PRIVATE":
        quote = fetch_company_quote(ticker)
        if quote:
            parts.append(
                f"Current Market Data ({ticker}): "
                f"Price {quote['currency']} {quote['price']}"
            )

    # Try SEC filings
    sec_info = fetch_sec_filings(company_name)
    if sec_info:
        parts.append(sec_info)

    return "\n".join(parts) if parts else ""
