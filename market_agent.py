"""market_agent.py
Market Risk Agent — Direct LLM calls with web search.
Assesses commodity price sensitivity and trading exposure limits.
"""
import logging
import os
from dotenv import load_dotenv

from agent_base import call_llm, web_search, build_memo_record, save_memo
from market_data_helper import build_market_context
from risk_scorer import RiskScorer

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def run(counterparty_name: str, financial_data: dict) -> dict:
    """Run Market Risk Agent for a given counterparty."""
    logger.info(f"[MARKET_AGENT] Starting market risk assessment for {counterparty_name}")
    logger.info(f"[MARKET_AGENT] Revenue=${financial_data.get('revenue_usd_m', 0):.0f}M, Sector={financial_data.get('sector', 'N/A')}")

    data_block = _format_data(financial_data)

    # 1. Enriched Online Context (SEC Filings, IR Summary, Ownership)
    market_context = build_market_context(counterparty_name)
    
    # 2. General news search
    news_results = web_search(f"{counterparty_name} news acquisitions market 2025", max_results=3)
    search_context = f"\nEXTERNAL MARKET & NEWS CONTEXT:\n{market_context}\n{news_results}"

    prompt = (
        "You are a senior market risk analyst for an LNG trading company. "
        "Assess market risk for this counterparty based on:\n"
        "1. Revenue quality and commodity price sensitivity\n"
        "2. Ownership structure, ultimate parent stability, and investor relations\n"
        "3. Sector concentration and EBITDA margin buffers\n"
        "4. Recent market developments or acquisitions\n"
        "5. Recommended maximum notional trading exposure (USD million)\n\n"
        f"COUNTERPARTY: {counterparty_name}\n"
        f"FINANCIAL DATA:\n{data_block}\n"
        f"{search_context}\n\n"
        "Provide your assessment in this format:\n"
        "ANALYSIS: [your detailed analysis]\n"
        "RISK_LEVEL: [LOW|MEDIUM|HIGH|CRITICAL]\n"
        "EXPOSURE_LIMIT: [USD XXM]\n"
    )

    logger.info(f"[MARKET_AGENT] Calling LLM with web search context")
    response = call_llm(prompt, temperature=0.4)

    risk_level, exposure = _parse_verdict(response)
    memo_text = _strip_verdict_lines(response)

    logger.info(f"[MARKET_AGENT] Parsed: Risk={risk_level}, Exposure={exposure}")

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
        agent_type="market",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=exposure,
    )
    record["risk_score"] = risk_score_result["score"]
    record["risk_score_breakdown"] = risk_score_result["breakdown"]

    logger.info(f"[MARKET_AGENT] Saving memo and returning")
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
        f"  Total Debt:      USD {d.get('total_debt_usd_m', 0):,.0f}M\n"
        f"  Debt/Equity:     {d.get('debt_to_equity', 0):.2f}x\n"
        f"  Current Ratio:   {d.get('current_ratio', 0):.2f}x\n"
    )


def _parse_verdict(text: str) -> tuple[str, str]:
    risk, exposure = "MEDIUM", "Pending"
    for line in text.splitlines():
        if "RISK_LEVEL:" in line:
            val = line.split("RISK_LEVEL:")[-1].strip().upper()
            if val in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                risk = val
        if "EXPOSURE_LIMIT:" in line:
            exposure = line.split("EXPOSURE_LIMIT:")[-1].strip()
    return risk, exposure


def _strip_verdict_lines(text: str) -> str:
    lines = [l for l in text.splitlines() if "RISK_LEVEL:" not in l and "EXPOSURE_LIMIT:" not in l]
    return "\n".join(lines).strip()
