"""market_data_helper.py
Fetch live market data from public sources to enrich chat context.

Supported sources:
  - Yahoo Finance (via yfinance)
  - SEC EDGAR (free, no key) — presence check only
"""
import logging
import urllib.request
import urllib.parse
import yfinance as yf

# Re-use the canonical ticker map from agent_base to avoid drift
from agent_base import TICKER_MAP

logger = logging.getLogger(__name__)


def _resolve_ticker(company_name: str) -> str:
    """Map a company name to its stock ticker using the shared canonical map."""
    name_lower = company_name.lower().strip()
    # Exact match
    if name_lower in TICKER_MAP:
        return TICKER_MAP[name_lower]
    # Partial match — check if any known name is contained in the input
    for known_name, ticker in TICKER_MAP.items():
        if known_name in name_lower:
            return ticker
    # Fall back to uppercase as a best-effort ticker
    return company_name.upper().strip()


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
        logger.warning("Yahoo Finance fetch failed for %s: %s", ticker, e)
    return None


def fetch_sec_filings(company_name: str) -> str:
    """Check for recent SEC filings presence (10-K / 10-Q).

    Note: This is a lightweight presence check only; it confirms whether
    the SEC EDGAR search returns 10-K/10-Q results for the company, but
    does not parse filing content.
    """
    try:
        search_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&company={urllib.parse.quote(company_name)}"
            f"&type=10-K%7C10-Q&dateb=&owner=exclude&count=10&search_text="
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode()
            # Check for table rows containing filing links — more specific than plain text match
            if '<td class="small">10-K</td>' in html or '<td class="small">10-Q</td>' in html:
                return f"SEC filings (10-K/10-Q) found for {company_name} on EDGAR."
    except Exception:
        pass
    return ""


def build_market_context(company_name: str) -> str:
    """Assemble market data context from available sources."""
    parts = []

    ticker = _resolve_ticker(company_name)

    if ticker != "PRIVATE":
        quote = fetch_company_quote(ticker)
        if quote:
            parts.append(
                f"Current Market Data ({ticker}): "
                f"Price {quote['currency']} {quote['price']}"
            )

    # SEC filings presence check (non-blocking, best-effort)
    sec_info = fetch_sec_filings(company_name)
    if sec_info:
        parts.append(sec_info)

    return "\n".join(parts) if parts else ""
