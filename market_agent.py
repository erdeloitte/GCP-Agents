"""market_agent.py
Market Risk Agent — LNG trader perspective.

Assesses a counterparty's exposure to commodity market movements,
revenue quality, sector concentration, and recommends a notional
trading exposure limit.

"""
from agent_base import call_gemini, build_memo_record, save_memo
from risk_scorer import RiskScorer
import os
from dotenv import load_dotenv

load_dotenv()
  

SYSTEM_CONTEXT = """\
You are a senior LNG trader at a major energy company conducting market research for counterparty due diligence.
Your role is to assess market risk: where are the markets the counterparty operates? how exposed is this counterparty to commodity price swings?
what is the quality and stability of their revenue? how large a notional position should we
be willing to carry with them?

You have access to:
1. Google Search grounding (automatically enabled; use it to search the web for the latest news, financials, ESG developments, and market reports for the counterparty).
2. `get_headlines_tool` and `get_stock_price_tool` for stock ticker lookup (providing a stock ticker like 'SHEL', 'BP', 'CVX', or 'GLEN.L') to check recent Yahoo Finance news and stock price if public.

Always use these tools to research the counterparty's recent activities, business changes, and commodity market status (e.g., search for '[Counterparty Name] news financials 2025 2026'). If the company is private (like Vitol, Trafigura, Louis Dreyfus, or Gunvor), use Google Search grounding to find news, estimated trading volumes, and credit/market events.

CRITICAL INSTRUCTION: Your memo must be highly specific, quantitative, and factual. You MUST incorporate exact details of recent news, financial transactions, acquisitions, joint ventures, or key corporate statements discovered during your web search (including details like specific companies acquired, specific projects like Baleine, and dates like 2025/2026). Do NOT make generic summaries or general statements like "we found news about acquisitions". Name the specific news events, companies, and statistics you found online. If you look up stock prices, cite the exact stock price and listing exchange in the memo.

Write a detailed, analytical internal memo (6–10 sentences) as a cohesive professional narrative. Do NOT include sentence numbers, labels (like "1.", "2.", or "Sentence X:"), or internal reasoning/thought headers. The memo should flow naturally and cover:
1. Revenue quality, commodity price sensitivity, and business model
2. Sector and geographic concentration risk (incorporate recent news, acquisitions, and expansions from your search results)
3. EBITDA margin and cash buffers against price volatility
4. Public vs Private status: stock price, investor sentiment, and disclosures
5. Recommended maximum notional exposure (in USD million) with detailed risk-based rationale
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

    raw = call_gemini(prompt, temperature=0.4)
    risk_level, exposure = _parse_verdict(raw)
    memo_text = _strip_verdict_lines(raw)

    # Calculate risk score based on financial data
    risk_score_result = RiskScorer.calculate_score(
        company_name=counterparty_name,
        country=financial_data.get("country", ""),
        sector=financial_data.get("sector", ""),
        credit_rating=financial_data.get("credit_rating", "N/A"),
        debt_to_equity=financial_data.get("debt_to_equity", 0),
        current_ratio=financial_data.get("current_ratio", 1.0),
        ebitda_margin_pct=financial_data.get("ebitda_margin_pct", 0),
        revenue_usd_m=financial_data.get("revenue_usd_m", 0),
        total_debt_usd_m=financial_data.get("total_debt_usd_m", 0),
        is_public=_is_public_company(counterparty_name),
    )

    record = build_memo_record(
        counterparty=counterparty_name,
        agent_type="market",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=exposure,
    )
    record["search_queries"] = getattr(raw, "search_queries", [])
    record["search_sources"] = getattr(raw, "search_sources", [])
    record["tool_calls"] = getattr(raw, "tool_calls", [])
    record["risk_score"] = risk_score_result["score"]
    record["risk_score_breakdown"] = risk_score_result["breakdown"]
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
    risk, exposure = "MEDIUM", "Pending exposure assessment"
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


def _is_public_company(name: str) -> bool:
    """Determine if company is likely public based on name patterns."""
    name_lower = (name or "").lower()

    # Known private trading companies
    private_companies = [
        "vitol", "trafigura", "louis dreyfus", "gunvor",
        "mercuria", "noble", "cargill", "archer daniels",
        "privately held", "private",
    ]

    if any(p in name_lower for p in private_companies):
        # Note: Glencore is public (LSE: GLEN) — explicitly carve out
        if "glencore" in name_lower:
            return True
        return False

    # Check for public company legal-form indicators (word-boundary safe)
    public_indicators = ["plc", "inc.", "corp.", " ag", " se", " sa", " nv"]
    if any(p in name_lower for p in public_indicators):
        return True

    # Default: assume public if not in known private list
    return True
