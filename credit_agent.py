"""credit_agent.py
Credit Risk Agent — LNG trader perspective.

Assesses a counterparty's creditworthiness, leverage, and debt serviceability.
Recommends a credit limit and payment terms for LNG trading agreements.
Uses Gemini as the orchestrator with comprehensive logging of all actions.
"""
import os
import logging
from agent_base import build_memo_record, save_memo, call_gemini
from risk_scorer import RiskScorer

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


SYSTEM_CONTEXT = """\
You are a senior credit analyst embedded in an LNG trading desk.
Your role is to assess the creditworthiness of a trading counterparty before extending
open credit on LNG supply or offtake contracts.

Write a concise internal credit memo (5–8 sentences) covering:
1. Credit rating assessment and implied default risk
2. Leverage analysis: Debt/Equity and ability to service debt from EBITDA
3. Net income sustainability and earnings quality
4. Recommended credit limit (in USD million) for unsecured LNG exposure
5. Recommended payment terms: Open Credit / Letter of Credit / Prepayment
6. One-line risk verdict: LOW / MEDIUM / HIGH / CRITICAL

End your response with exactly three lines:
RISK_LEVEL: <LOW|MEDIUM|HIGH|CRITICAL>
CREDIT_LIMIT: <USD amount, e.g. "USD 150M">
PAYMENT_TERMS: <Open Credit|Letter of Credit|Prepayment|Partial LC>
"""


def run(counterparty_name: str, financial_data: dict) -> dict:
    data_block = _format_data(financial_data)
    prompt = (
        f"{SYSTEM_CONTEXT}\n\n"
        f"COUNTERPARTY: {counterparty_name}\n"
        f"FINANCIAL DATA:\n{data_block}\n\n"
        "Write the memo now."
    )

    logger.info(f"[CREDIT_AGENT] Starting credit assessment for {counterparty_name}")
    logger.info(f"[CREDIT_AGENT] Financial data summary: Revenue=${financial_data.get('revenue_usd_m', 0):.0f}M, Debt/Equity={financial_data.get('debt_to_equity', 0):.2f}x, Rating={financial_data.get('credit_rating', 'N/A')}")

    logger.info(f"[CREDIT_AGENT] Invoking Gemini orchestrator with credit assessment prompt")
    raw_text = str(call_gemini(prompt, temperature=0.3))

    logger.info(f"[CREDIT_AGENT] Gemini response received, processing verdict lines")

    risk_level, credit_limit, payment_terms = _parse_verdict(raw_text)
    memo_text = _strip_verdict_lines(raw_text)
    exposure_proposal = f"{credit_limit} | {payment_terms}"

    logger.info(f"[CREDIT_AGENT] Parsed verdict: Risk Level={risk_level}, Credit Limit={credit_limit}, Payment Terms={payment_terms}")

    logger.info(f"[CREDIT_AGENT] Calculating quantitative risk score for {counterparty_name}")
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

    logger.info(f"[CREDIT_AGENT] Risk score calculated: {risk_score_result['score']}/100 - {risk_score_result['breakdown']}")

    record = build_memo_record(
        counterparty=counterparty_name,
        agent_type="credit",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=exposure_proposal,
    )
    record["credit_limit"]           = credit_limit
    record["payment_terms"]          = payment_terms
    record["risk_score"]             = risk_score_result["score"]
    record["risk_score_breakdown"]   = risk_score_result["breakdown"]

    logger.info(f"[CREDIT_AGENT] Saving credit memo to BigQuery for {counterparty_name}")
    save_memo(record)
    logger.info(f"[CREDIT_AGENT] Credit assessment completed for {counterparty_name}")
    return record


def _is_public_company(name: str) -> bool:
    """Determine if company is likely public based on name patterns."""
    name_lower = (name or "").lower()
    private_companies = [
        "vitol", "trafigura", "louis dreyfus", "gunvor",
        "mercuria", "noble", "cargill", "archer daniels",
        "privately held", "private",
    ]
    if any(p in name_lower for p in private_companies):
        return False
    if "glencore" in name_lower:
        return True
    public_indicators = ["plc", "inc.", "corp.", " ag", " se", " sa", " nv"]
    if any(p in name_lower for p in public_indicators):
        return True
    return True  # default: assume public


def _format_data(d: dict) -> str:
    ebitda = d.get("ebitda_usd_m", 0) or 0
    debt   = d.get("total_debt_usd_m", 0) or 0
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
        if line.startswith("RISK_LEVEL:"):
            val = line.split(":", 1)[1].strip().upper()
            if val in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                risk = val
        if line.startswith("CREDIT_LIMIT:"):
            limit = line.split(":", 1)[1].strip()
        if line.startswith("PAYMENT_TERMS:"):
            terms = line.split(":", 1)[1].strip()
    return risk, limit, terms


def _strip_verdict_lines(text: str) -> str:
    tags = ("RISK_LEVEL:", "CREDIT_LIMIT:", "PAYMENT_TERMS:")
    lines = [l for l in text.splitlines() if not any(l.startswith(t) for t in tags)]
    return "\n".join(lines).strip()
