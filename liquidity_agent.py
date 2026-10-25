"""liquidity_agent.py
Liquidity & Settlement Risk Agent — Direct LLM calls with web search.
Evaluates payment settlement capability and liquidity risk.
"""
import logging
import os
from dotenv import load_dotenv

from agent_base import (
    call_llm, web_search, build_memo_record, save_memo,
    STANDARD_REPORT_INSTRUCTION, summarize_to_sentences,
)
from risk_scorer import RiskScorer

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def run(counterparty_name: str, financial_data: dict) -> dict:
    """Run Liquidity & Settlement Risk Agent."""
    logger.info(f"[LIQUIDITY_AGENT] Starting liquidity assessment for {counterparty_name}")
    logger.info(f"[LIQUIDITY_AGENT] Current Ratio={financial_data.get('current_ratio', 0):.2f}x, Revenue=${financial_data.get('revenue_usd_m', 0):.0f}M")

    data_block = _format_data(financial_data)

    # Search for working capital and payment metrics
    search_results = web_search(f"{counterparty_name} working capital cash flow operations 2025", max_results=3)
    search_context = f"\nRECENT OPERATIONAL DATA:\n{search_results}" if search_results else ""

    prompt = (
        "You are a treasury risk manager on an LNG trading desk focused on settlement risk. "
        "Assess whether this counterparty can settle LNG trades on T+5 to T+10 terms based on:\n"
        "1. Current ratio and short-term solvency\n"
        "2. Working capital adequacy and cash flow\n"
        "3. Settlement reliability on standard LNG terms\n"
        "4. Structural liquidity vulnerabilities\n"
        "5. Recommended settlement structure: Standard Terms / Advance Payment / Escrow / LC Required\n\n"
        f"COUNTERPARTY: {counterparty_name}\n"
        f"FINANCIAL DATA:\n{data_block}\n"
        f"{search_context}\n\n"
        f"{STANDARD_REPORT_INSTRUCTION}\n"
        "Provide your assessment as the concise narrative, then exactly these two lines:\n"
        "RISK_LEVEL: [LOW|MEDIUM|HIGH|CRITICAL]\n"
        "SETTLEMENT_TERMS: [Standard|Advance Payment|Escrow|LC Required|Partial Advance]\n"
    )

    logger.info(f"[LIQUIDITY_AGENT] Calling LLM with web search context")
    response = call_llm(prompt, temperature=0.2, trace_label="LIQUIDITY")

    risk_level, settlement = _parse_verdict(response)
    memo_text = summarize_to_sentences(_strip_verdict_lines(response), max_sentences=6)

    logger.info(f"[LIQUIDITY_AGENT] Parsed: Risk={risk_level}, Settlement={settlement}")

    # Quantitative score
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
    record["settlement_terms"] = settlement
    record["risk_score"] = risk_score_result["score"]
    record["risk_score_breakdown"] = risk_score_result["breakdown"]

    logger.info(f"[LIQUIDITY_AGENT] Saving memo and returning")
    save_memo(record)
    return record


def _format_data(d: dict) -> str:
    assets = d.get("total_assets_usd_m", 0) or 0
    debt = d.get("total_debt_usd_m", 0) or 0
    de_ratio = d.get("debt_to_equity", 0) or 0
    if de_ratio > 0 and debt > 0:
        equity = round(debt / de_ratio, 1)
    else:
        equity = max(assets - debt, 0)
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
    for line in text.splitlines():
        if "RISK_LEVEL:" in line:
            val = line.split("RISK_LEVEL:")[-1].strip().upper()
            if val in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                risk = val
        if "SETTLEMENT_TERMS:" in line:
            settlement = line.split("SETTLEMENT_TERMS:")[-1].strip()
    return risk, settlement


def _strip_verdict_lines(text: str) -> str:
    tags = ("RISK_LEVEL:", "SETTLEMENT_TERMS:")
    lines = [l for l in text.splitlines() if not any(t in l for t in tags)]
    return "\n".join(lines).strip()
