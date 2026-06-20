"""market_agent.py
Market Risk Agent — LNG trader perspective.

Assesses a counterparty's exposure to commodity market movements,
revenue quality, sector concentration, and recommends a notional
trading exposure limit.

"""
from agent_base import call_gemini, build_memo_record, save_memo
from pydantic import BaseModel
import os
from dotenv import load_dotenv
import langchain

langchain.debug = False

load_dotenv()
  

SYSTEM_CONTEXT = """\
You are a senior LNG trader at a major energy company conducting market research for counterparty due diligence.
Your role is to assess market risk: where are the markets the counterparty operates? how exposed is this counterparty to commodity price swings?
what is the quality and stability of their revenue?how large a notional position should we
be willing to carry with them?

Use the get_headlines_tool and get_stock_price_tool (providing a stock ticker like 'SHEL', 'BP', or 'CVX') to check for recent news and investor sentiment. If headlines are unavailable or the company is private, acknowledge the lack of external market data and focus your analysis on the provided financial metrics and general sector trends.

Write a concise internal memo (5–8 sentences) covering:
1. Revenue quality and commodity price sensitivity
2. Sector and geographic concentration risk (incorporate recent news if available)
3. EBITDA margin as a buffer against price volatility
4. IF public - stock price and investor sentiment
5. Recommended maximum notional exposure (in USD million) with rationale
6. One-line risk verdict: LOW / MEDIUM / HIGH / CRITICAL

End your response with exactly two lines:
RISK_LEVEL: <LOW|MEDIUM|HIGH|CRITICAL>
EXPOSURE_LIMIT: <USD amount, e.g. "USD 250M">
"""


def run(counterparty_name: str, financial_data: dict) -> dict:
    """Run the Market Risk Agent for a given counterparty.

    Args:
        counterparty_name: Name of the counterparty.
        financial_data:    Dict of financial metrics from BigQuery.

    Returns:
        Dict with memo, risk_level, exposure_proposal, and metadata.
    """
    data_block = _format_data(financial_data)
    prompt = (
        f"{SYSTEM_CONTEXT}\n\n"
        f"COUNTERPARTY: {counterparty_name}\n"
        f"FINANCIAL DATA:\n{data_block}\n\n"
        "Write the memo now."
    )

    raw = call_gemini(prompt, temperature=1)
    risk_level, exposure = _parse_verdict(raw)
    memo_text = _strip_verdict_lines(raw)

    record = build_memo_record(
        counterparty=counterparty_name,
        agent_type="market",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=exposure,
    )
    save_memo(record)
    return record


def _format_data(d: dict) -> str:
    return (
        f"  Sector:          {d.get('sector', 'N/A')}\n"
        f"  Country:         {d.get('country', 'N/A')}\n"
        f"  FY Period:       {d.get('period_year', 'N/A')}\n"
        f"  Revenue:         USD {d.get('revenue_usd_m', 0):,.0f}M\n"
        f"  EBITDA:          USD {d.get('ebitda_usd_m', 0):,.0f}M\n"
        f"  EBITDA Margin:   {d.get('ebitda_margin_pct', 0):.1f}%\n"
        f"  Net Income:      USD {d.get('net_income_usd_m', 0):,.0f}M\n"
        f"  Credit Rating:   {d.get('credit_rating', 'N/A')}\n"
        f"  Total Assets:    USD {d.get('total_assets_usd_m', 0):,.0f}M\n"
        f"  Total Debt:      USD {d.get('total_debt_usd_m', 0):,.0f}M\n"
        f"  Debt/Equity:     {d.get('debt_to_equity', 0):.2f}x\n"
        f"  Current Ratio:   {d.get('current_ratio', 0):.2f}x\n"
    )


def _parse_verdict(text: str) -> tuple[str, str]:
    risk, exposure = "MEDIUM", "Pending assessment"
    for line in text.splitlines():
        if line.startswith("RISK_LEVEL:"):
            val = line.split(":", 1)[1].strip().upper()
            if val in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                risk = val
        if line.startswith("EXPOSURE_LIMIT:"):
            exposure = line.split(":", 1)[1].strip()
    return risk, exposure


def _strip_verdict_lines(text: str) -> str:
    lines = [l for l in text.splitlines()
             if not l.startswith("RISK_LEVEL:") and not l.startswith("EXPOSURE_LIMIT:")]
    return "\n".join(lines).strip()
