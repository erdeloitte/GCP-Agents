"""credit_agent.py
Credit Risk Agent — LNG trader perspective.

Assesses a counterparty's creditworthiness, leverage, and debt serviceability.
Recommends a credit limit and payment terms for LNG trading agreements.
"""
from agent_base import call_gemini, build_memo_record, save_memo


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

    raw = call_gemini(prompt, temperature=0.2)
    risk_level, credit_limit, payment_terms = _parse_verdict(raw)
    memo_text = _strip_verdict_lines(raw)
    exposure_proposal = f"{credit_limit} | {payment_terms}"

    record = build_memo_record(
        counterparty=counterparty_name,
        agent_type="credit",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=exposure_proposal,
    )
    record["credit_limit"]    = credit_limit
    record["payment_terms"]   = payment_terms
    save_memo(record)
    return record


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
