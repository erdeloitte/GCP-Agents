"""liquidity_agent.py
Liquidity & Settlement Risk Agent using CrewAI with Gemini (+ Claude fallback) orchestration.
Evaluates payment settlement capability and liquidity risk.
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
    """Run the Liquidity & Settlement Risk Agent using CrewAI."""
    logger.info(f"[LIQUIDITY_AGENT] Starting liquidity assessment for {counterparty_name}")
    logger.info(f"[LIQUIDITY_AGENT] Financial data: Current Ratio={financial_data.get('current_ratio', 0):.2f}x, Revenue=${financial_data.get('revenue_usd_m', 0):.0f}M")

    try:
        llm = get_llm()
        logger.info("[LIQUIDITY_AGENT] LLM initialized")
    except Exception as e:
        logger.error(f"[LIQUIDITY_AGENT] LLM initialization failed: {e}")
        return _fallback_response(counterparty_name, "liquidity", str(e))

    data_block = _format_data(financial_data)

    # Define the liquidity analyst agent with web search
    tools = []
    if TAVILY_API_KEY:
        try:
            from crewai_tools import TavilySearchTool
            tools = [TavilySearchTool(tavily_api_key=TAVILY_API_KEY)]
            logger.info("[LIQUIDITY_AGENT] Web search tool enabled (Tavily)")
        except Exception as e:
            logger.warning(f"[LIQUIDITY_AGENT] Tavily tool failed: {e}")

    liquidity_analyst = Agent(
        role="Senior Liquidity & Settlement Risk Manager",
        goal="Assess settlement risk and liquidity adequacy for LNG trades on T+5 to T+10 terms",
        backstory=(
            "You are a treasury risk manager on an LNG trading desk with 15+ years experience. "
            "Your focus is settlement risk—ensuring counterparties can pay for cargo on agreed terms. "
            "Use web search to find working capital metrics, recent payment issues, and operational metrics."
        ),
        llm=llm,
        tools=tools,
        verbose=True,
    )

    assess_task = Task(
        description=(
            f"Evaluate liquidity & settlement risk for {counterparty_name}\n\n"
            f"FINANCIAL DATA:\n{data_block}\n\n"
            "INSTRUCTIONS:\n"
            "1. Use web search to find working capital metrics, cash flow, and operational data\n"
            "2. Analyze current ratio, short-term solvency, and settlement capability\n"
            "3. Assess payment reliability on standard T+5 to T+10 LNG cargo terms\n"
            "4. Recommend settlement structure: Standard Terms / Advance Payment / Escrow / LC Required\n"
            "5. Provide a risk verdict: LOW / MEDIUM / HIGH / CRITICAL\n\n"
            "Format your response with:\n"
            "ANALYSIS: [detailed analysis]\n"
            "RISK_LEVEL: [LOW|MEDIUM|HIGH|CRITICAL]\n"
            "SETTLEMENT_TERMS: [Standard|Advance Payment|Escrow|LC Required|Partial Advance]\n"
        ),
        expected_output="Liquidity risk assessment with settlement terms recommendation",
        agent=liquidity_analyst,
    )

    try:
        crew = Crew(agents=[liquidity_analyst], tasks=[assess_task], verbose=True)
        logger.info("[LIQUIDITY_AGENT] Crew created, executing task")
        result = crew.kickoff()
        logger.info("[LIQUIDITY_AGENT] Crew execution completed")
    except Exception as e:
        logger.error(f"[LIQUIDITY_AGENT] Crew execution failed: {e}")
        return _fallback_response(counterparty_name, "liquidity", str(e))

    output_text = str(result)
    risk_level, settlement = _parse_verdict(output_text)
    memo_text = _strip_verdict_lines(output_text)

    logger.info(f"[LIQUIDITY_AGENT] Parsed verdict: Risk Level={risk_level}, Settlement={settlement}")

    # Quantitative risk score
    logger.info(f"[LIQUIDITY_AGENT] Calculating quantitative risk score")
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

    logger.info(f"[LIQUIDITY_AGENT] Risk score: {risk_score_result['score']}/100")

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

    logger.info(f"[LIQUIDITY_AGENT] Saving liquidity memo to BigQuery for {counterparty_name}")
    save_memo(record)
    logger.info(f"[LIQUIDITY_AGENT] Liquidity assessment completed")
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
