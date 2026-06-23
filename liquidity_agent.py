"""liquidity_agent.py
Liquidity & Settlement Risk Agent — LNG trader perspective.

Assesses a counterparty's ability to settle LNG trades on time.
Focuses on short-term liquidity, working capital, and operational cash flow.
"""
from agent_base import call_gemini, build_memo_record, save_memo
from risk_scorer import RiskScorer


SYSTEM_CONTEXT = """\
You are a treasury risk manager on an LNG trading desk responsible for settlement risk.
Your role is to assess whether a counterparty has sufficient liquidity to settle LNG
cargo payments on standard T+5 to T+10 terms without requiring extraordinary credit support.

Write a concise internal liquidity memo (5–8 sentences) covering:
1. Current ratio analysis and short-term solvency
2. Working capital adequacy relative to trading volumes
3. Settlement risk: likelihood of delayed or failed payment on an LNG cargo
4. Any structural liquidity vulnerabilities (e.g. high short-term debt relative to assets)
5. Recommended settlement structure: Standard Terms / Advance Payment / Escrow / LC Required
6. One-line risk verdict: LOW / MEDIUM / HIGH / CRITICAL

End your response with exactly two lines:
RISK_LEVEL: <LOW|MEDIUM|HIGH|CRITICAL>
SETTLEMENT_TERMS: <Standard|Advance Payment|Escrow|LC Required|Partial Advance>
"""


def run(counterparty_name: str, financial_data: dict) -> dict:
    data_block = _format_data(financial_data)
    prompt = (
        f"{SYSTEM_CONTEXT}\n\n"
        f"COUNTERPARTY: {counterparty_name}\n"
        f"FINANCIAL DATA:\n{data_block}\n\n"
        "Write the memo now."
    )

    raw = call_gemini(prompt, temperature=0.2)
    risk_level, settlement = _parse_verdict(raw)
    memo_text = _strip_verdict_lines(raw)

    # Calculate quantitative risk score (consistent across all agents)
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
    )

    record = build_memo_record(
        counterparty=counterparty_name,
        agent_type="liquidity",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=f"Settlement: {settlement}",
    )
    record["settlement_terms"]       = settlement
    record["risk_score"]             = risk_score_result["score"]
    record["risk_score_breakdown"]   = risk_score_result["breakdown"]
    record["search_queries"]         = getattr(raw, "search_queries", [])
    record["search_sources"]         = getattr(raw, "search_sources", [])
    record["tool_calls"]             = getattr(raw, "tool_calls", [])
    save_memo(record)
    return record


def _format_data(d: dict) -> str:
    assets         = d.get("total_assets_usd_m", 0) or 0
    debt           = d.get("total_debt_usd_m", 0) or 0
    de_ratio       = d.get("debt_to_equity", 0) or 0
    # Equity (est.) derived from D/E ratio: Equity = Debt / D/E when D/E > 0,
    # else fall back to Assets - Debt as a rough approximation.
    if de_ratio > 0 and debt > 0:
        equity = round(debt / de_ratio, 1)
    else:
        equity = max(assets - debt, 0)  # lower-bound at 0
    return (
        f"  Sector:          {d.get('sector', 'N/A')}\n"
        f"  Country:         {d.get('country', 'N/A')}\n"
        f"  FY Period:       {d.get('period_year', 'N/A')}\n"
        f"  Revenue:         USD {d.get('revenue_usd_m', 0):,.0f}M\n"
        f"  EBITDA:          USD {d.get('ebitda_usd_m', 0):,.0f}M\n"
        f"  Net Income:      USD {d.get('net_income_usd_m', 0):,.0f}M\n"
        f"  Total Assets:    USD {assets:,.0f}M\n"
        f"  Total Debt:      USD {debt:,.0f}M\n"
        f"  Equity (est.):   USD {equity:,.0f}M\n"
        f"  Debt/Equity:     {d.get('debt_to_equity', 0):.2f}x\n"
        f"  Current Ratio:   {d.get('current_ratio', 0):.2f}x\n"
        f"  Credit Rating:   {d.get('credit_rating', 'N/A')}\n"
    )


def _parse_verdict(text: str) -> tuple[str, str]:
    risk, settlement = "MEDIUM", "LC Required"
    risk, settlement = "MEDIUM", "Awaiting Settlement Terms"
    for line in text.splitlines():
        if line.startswith("RISK_LEVEL:"):
            val = line.split(":", 1)[1].strip().upper()
            if val in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                risk = val
        if line.startswith("SETTLEMENT_TERMS:"):
            settlement = line.split(":", 1)[1].strip()
    return risk, settlement


def _strip_verdict_lines(text: str) -> str:
    tags = ("RISK_LEVEL:", "SETTLEMENT_TERMS:")
    lines = [l for l in text.splitlines() if not any(l.startswith(t) for t in tags)]
    return "\n".join(lines).strip()
