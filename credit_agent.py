"""credit_agent.py
Credit Risk Agent using CrewAI with Gemini (+ Claude fallback) orchestration.
Evaluates creditworthiness and recommends credit limits.
"""
import logging
import os
from dotenv import load_dotenv

from agent_base import get_llm, build_memo_record, save_memo, TAVILY_API_KEY
from risk_scorer import RiskScorer
from crewai import Agent, Task, Crew

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def run(counterparty_name: str, financial_data: dict) -> dict:
    """Run the Credit Risk Agent for a given counterparty using CrewAI."""
    logger.info(f"[CREDIT_AGENT] Starting credit assessment for {counterparty_name}")
    logger.info(f"[CREDIT_AGENT] Financial data: Revenue=${financial_data.get('revenue_usd_m', 0):.0f}M, Debt/Equity={financial_data.get('debt_to_equity', 0):.2f}x")

    try:
        llm = get_llm()
        logger.info("[CREDIT_AGENT] LLM initialized")
    except Exception as e:
        logger.error(f"[CREDIT_AGENT] LLM initialization failed: {e}")
        return _fallback_response(counterparty_name, "credit", str(e))

    data_block = _format_data(financial_data)

    # Define the credit analyst agent with web search
    tools = []
    if TAVILY_API_KEY:
        try:
            from crewai_tools import TavilySearchTool
            tools = [TavilySearchTool(tavily_api_key=TAVILY_API_KEY)]
            logger.info("[CREDIT_AGENT] Web search tool enabled (Tavily)")
        except Exception as e:
            logger.warning(f"[CREDIT_AGENT] Tavily tool failed: {e}")

    credit_analyst = Agent(
        role="Senior Credit Analyst",
        goal="Assess creditworthiness and recommend credit limits for LNG counterparties",
        backstory=(
            "You are a credit risk specialist with 20+ years in trade finance. "
            "Your expertise is evaluating counterparty creditworthiness, debt serviceability, "
            "and payment reliability. Use web search to find credit ratings, recent news, "
            "financial disclosures, and payment history."
        ),
        llm=llm,
        tools=tools,
        verbose=True,
    )

    assess_task = Task(
        description=(
            f"Evaluate credit risk for {counterparty_name}\n\n"
            f"FINANCIAL DATA:\n{data_block}\n\n"
            "INSTRUCTIONS:\n"
            "1. Use web search to find credit ratings, recent news, and financial disclosures\n"
            "2. Analyze leverage (Debt/Equity), debt service coverage, and cash flow\n"
            "3. Assess payment reliability and default risk\n"
            "4. Recommend a credit limit in USD million\n"
            "5. Recommend payment terms: Open Credit / Letter of Credit / Prepayment\n"
            "6. Provide a risk verdict: LOW / MEDIUM / HIGH / CRITICAL\n\n"
            "Format your response with:\n"
            "ANALYSIS: [detailed analysis]\n"
            "RISK_LEVEL: [LOW|MEDIUM|HIGH|CRITICAL]\n"
            "CREDIT_LIMIT: [USD XXM]\n"
            "PAYMENT_TERMS: [Open Credit|Letter of Credit|Prepayment|Partial LC]\n"
        ),
        expected_output="Credit risk assessment with credit limit and payment terms recommendation",
        agent=credit_analyst,
    )

    try:
        crew = Crew(agents=[credit_analyst], tasks=[assess_task], verbose=True)
        logger.info("[CREDIT_AGENT] Crew created, executing task")
        result = crew.kickoff()
        logger.info("[CREDIT_AGENT] Crew execution completed")
    except Exception as e:
        logger.error(f"[CREDIT_AGENT] Crew execution failed: {e}")
        return _fallback_response(counterparty_name, "credit", str(e))

    output_text = str(result)
    risk_level, credit_limit, payment_terms = _parse_verdict(output_text)
    memo_text = _strip_verdict_lines(output_text)
    exposure_proposal = f"{credit_limit} | {payment_terms}"

    logger.info(f"[CREDIT_AGENT] Parsed verdict: Risk Level={risk_level}, Credit Limit={credit_limit}, Terms={payment_terms}")

    # Quantitative risk score
    logger.info(f"[CREDIT_AGENT] Calculating quantitative risk score")
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

    logger.info(f"[CREDIT_AGENT] Risk score: {risk_score_result['score']}/100")

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

    logger.info(f"[CREDIT_AGENT] Saving credit memo to BigQuery for {counterparty_name}")
    save_memo(record)
    logger.info(f"[CREDIT_AGENT] Credit assessment completed")
    return record


def _fallback_response(counterparty: str, agent_type: str, error: str) -> dict:
    """Return a safe fallback response when CrewAI fails."""
    return build_memo_record(
        counterparty=counterparty,
        agent_type=agent_type,
        risk_level="MEDIUM",
        memo=f"Agent failed: {error}",
        exposure_proposal="Pending assessment",
    )


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
