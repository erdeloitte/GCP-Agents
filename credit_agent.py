"""credit_agent.py
Credit Risk Agent — Direct LLM calls with web search.
Evaluates creditworthiness and recommends credit limits.
"""
import logging
import os
from dotenv import load_dotenv

from agent_base import call_llm, web_search, build_memo_record, save_memo
from risk_scorer import RiskScorer

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def run(counterparty_name: str, financial_data: dict) -> dict:
    """Run Credit Risk Agent for a given counterparty."""
    logger.info(f"[CREDIT_AGENT] Starting credit assessment for {counterparty_name}")
    logger.info(f"[CREDIT_AGENT] Revenue=${financial_data.get('revenue_usd_m', 0):.0f}M, Debt/Equity={financial_data.get('debt_to_equity', 0):.2f}x")

    data_block = _format_data(financial_data)

    # Search for credit ratings and payment history
    search_results = web_search(f"{counterparty_name} credit rating financials payment history 2025", max_results=3)
    search_context = f"\nRECENT CREDIT INFORMATION:\n{search_results}" if search_results else ""

    prompt = (
        "You are a senior credit analyst specializing in trade finance and counterparty risk. "
        "Assess creditworthiness for this counterparty based on:\n"
        "1. Credit rating and implied default risk\n"
        "2. Leverage analysis and debt serviceability\n"
        "3. Net income sustainability and cash flow\n"
        "4. Recommended unsecured credit limit for LNG exposure (USD million)\n"
        "5. Recommended payment terms: Open Credit / Letter of Credit / Prepayment\n\n"
        f"COUNTERPARTY: {counterparty_name}\n"
        f"FINANCIAL DATA:\n{data_block}\n"
        f"{search_context}\n\n"
        "Provide your assessment in this format:\n"
        "ANALYSIS: [your detailed analysis]\n"
        "RISK_LEVEL: [LOW|MEDIUM|HIGH|CRITICAL]\n"
        "CREDIT_LIMIT: [USD XXM]\n"
        "PAYMENT_TERMS: [Open Credit|Letter of Credit|Prepayment|Partial LC]\n"
    )

    logger.info(f"[CREDIT_AGENT] Calling LLM with web search context")
    response = call_llm(prompt, temperature=0.3)

    risk_level, credit_limit, payment_terms = _parse_verdict(response)
    memo_text = _strip_verdict_lines(response)
    exposure_proposal = f"{credit_limit} | {payment_terms}"

    logger.info(f"[CREDIT_AGENT] Parsed: Risk={risk_level}, Limit={credit_limit}, Terms={payment_terms}")

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
        agent_type="credit",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=exposure_proposal,
    )
    record["credit_limit"] = credit_limit
    record["payment_terms"] = payment_terms
    record["risk_score"] = risk_score_result["score"]
    record["risk_score_breakdown"] = risk_score_result["breakdown"]

    logger.info(f"[CREDIT_AGENT] Saving memo and returning")
    save_memo(record)
    return record


def _format_data(d: dict) -> str:
    ebitda = d.get("ebitda_usd_m", 0) or 0
    debt = d.get("total_debt_usd_m", 0) or 0
    debt_ebitda = round(debt / ebitda, 1) if ebitda > 0 else "N/A"
    return (
        f"  Credit Rating:   {d.get('credit_rating', 'N/A')}\n"
        f"  Sector:          {d.get('sector', 'N/A')}\n"
        f"  Country:         {d.get('country', 'N/A')}\n"
        f"  FY Period:       {d.get('period_year', 'N/A')}\n"
        f"  Revenue:         USD {d.get('revenue_usd_m', 0):,.0f}M\n"
        f"  EBITDA:          USD {ebitda:,.0f}M\n"
        f"  EBITDA Margin:   {d.get('ebitda_margin_pct', 0):.1f}%\n"
        f"  Net Income:      USD {d.get('net_income_usd_m', 0):,.0f}M\n"
        f"  Total Debt:      USD {debt:,.0f}M\n"
        f"  Debt/Equity:     {d.get('debt_to_equity', 0):.2f}x\n"
        f"  Debt/EBITDA:     {debt_ebitda}x\n"
        f"  Total Assets:    USD {d.get('total_assets_usd_m', 0):,.0f}M\n"
    )


def _parse_verdict(text: str) -> tuple[str, str, str]:
    risk, limit, terms = "MEDIUM", "Pending", "Letter of Credit"
    for line in text.splitlines():
        if "RISK_LEVEL:" in line:
            val = line.split("RISK_LEVEL:")[-1].strip().upper()
            if val in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                risk = val
        if "CREDIT_LIMIT:" in line:
            limit = line.split("CREDIT_LIMIT:")[-1].strip()
        if "PAYMENT_TERMS:" in line:
            terms = line.split("PAYMENT_TERMS:")[-1].strip()
    return risk, limit, terms


def _strip_verdict_lines(text: str) -> str:
    tags = ("RISK_LEVEL:", "CREDIT_LIMIT:", "PAYMENT_TERMS:")
    lines = [l for l in text.splitlines() if not any(t in l for t in tags)]
    return "\n".join(lines).strip()
