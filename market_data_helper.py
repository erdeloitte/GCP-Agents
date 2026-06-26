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
        # Search for company filings including ownership (owner=include)
        search_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&company={urllib.parse.quote(company_name)}"
            f"&owner=include&count=20"
        )
        headers = {"User-Agent": "Deloitte Treasury Bot (internal research)"}
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode()
            found = []
            if '10-K' in html: found.append("Annual Reports (10-K)")
            if '10-Q' in html: found.append("Quarterly Reports (10-Q)")
            if 'SC 13D' in html or 'SC 13G' in html: found.append("Beneficial Ownership Filings (13D/G)")
            if 'Form 4' in html: found.append("Insider Ownership Changes (Form 4)")

            if found:
                return f"SEC EDGAR Records found for {company_name}: {', '.join(found)}."
    except Exception:
        pass
    return ""


def build_market_context(company_name: str) -> str:
    """Assemble market data context from available sources."""
    parts = []

    ticker = _resolve_ticker(company_name)

    if ticker != "PRIVATE":
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            price = info.get('currentPrice') or info.get('regularMarketPrice')
            if price:
                parts.append(f"Market Quote ({ticker}): {info.get('currency', 'USD')} {price:.2f}")

            # Investor Relations / Ownership context
            summary = info.get('longBusinessSummary')
            if summary:
                parts.append(f"Investor Profile Summary: {summary[:500]}...")

            inst_own = info.get('heldPercentInstitutions')
            if inst_own is not None:
                parts.append(f"Ownership Structure: {inst_own*100:.1f}% Institutional Ownership.")
        except Exception:
            pass

    sec_info = fetch_sec_filings(company_name)
    if sec_info:
        parts.append(sec_info)

    return "\n".join(parts) if parts else ""
