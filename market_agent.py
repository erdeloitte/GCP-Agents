"""market_agent.py
Market Risk Agent using CrewAI with Gemini (+ Claude fallback) orchestration.
Assesses commodity price sensitivity and trading exposure limits.
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
    """Run the Market Risk Agent for a given counterparty using CrewAI."""
    logger.info(f"[MARKET_AGENT] Starting market risk assessment for {counterparty_name}")
    logger.info(f"[MARKET_AGENT] Financial data: Revenue=${financial_data.get('revenue_usd_m', 0):.0f}M, Sector={financial_data.get('sector', 'N/A')}")

    try:
        llm = get_llm()
        logger.info("[MARKET_AGENT] LLM initialized")
    except Exception as e:
        logger.error(f"[MARKET_AGENT] LLM initialization failed: {e}")
        return _fallback_response(counterparty_name, "market", str(e))

    data_block = _format_data(financial_data)

    # Define the market analyst agent with web search capability
    tools = []
    if TAVILY_API_KEY:
        try:
            from crewai_tools import TavilySearchTool
            tools = [TavilySearchTool(tavily_api_key=TAVILY_API_KEY)]
            logger.info("[MARKET_AGENT] Web search tool enabled (Tavily)")
        except Exception as e:
            logger.warning(f"[MARKET_AGENT] Tavily tool failed: {e}")

    market_analyst = Agent(
        role="Senior Market Risk Analyst",
        goal="Assess commodity price exposure and market risk for the counterparty",
        backstory=(
            "You are a treasury risk manager with 15+ years in commodity trading. "
            "Your job is to analyze counterparty exposure to market price movements, "
            "sector concentration, and revenue quality. Use web search to find recent news, "
            "acquisitions, and market developments."
        ),
        llm=llm,
        tools=tools,
        verbose=True,
    )

    assess_task = Task(
        description=(
            f"Analyze market risk for {counterparty_name}\n\n"
            f"FINANCIAL DATA:\n{data_block}\n\n"
            "INSTRUCTIONS:\n"
            "1. Use web search to find recent news, acquisitions, and market developments for this company\n"
            "2. Assess revenue quality, commodity price sensitivity, and business model stability\n"
            "3. Evaluate geographic and sector concentration risks\n"
            "4. Recommend a maximum notional trading exposure in USD million\n"
            "5. Provide a risk verdict: LOW / MEDIUM / HIGH / CRITICAL\n\n"
            "Format your response with:\n"
            "ANALYSIS: [detailed analysis]\n"
            "RISK_LEVEL: [LOW|MEDIUM|HIGH|CRITICAL]\n"
            "EXPOSURE_LIMIT: [USD XXM]\n"
        ),
        expected_output="Market risk assessment with risk level and exposure recommendation",
        agent=market_analyst,
    )

    try:
        crew = Crew(agents=[market_analyst], tasks=[assess_task], verbose=True)
        logger.info("[MARKET_AGENT] Crew created, executing task")
        result = crew.kickoff()
        logger.info("[MARKET_AGENT] Crew execution completed")
    except Exception as e:
        logger.error(f"[MARKET_AGENT] Crew execution failed: {e}")
        return _fallback_response(counterparty_name, "market", str(e))

    output_text = str(result)
    risk_level, exposure = _parse_verdict(output_text)
    memo_text = _strip_verdict_lines(output_text)

    logger.info(f"[MARKET_AGENT] Parsed verdict: Risk Level={risk_level}, Exposure={exposure}")

    # Quantitative risk score
    logger.info(f"[MARKET_AGENT] Calculating quantitative risk score")
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

    logger.info(f"[MARKET_AGENT] Risk score: {risk_score_result['score']}/100")

    record = build_memo_record(
        counterparty=counterparty_name,
        agent_type="market",
        risk_level=risk_level,
        memo=memo_text,
        exposure_proposal=exposure,
    )
    record["risk_score"] = risk_score_result["score"]
    record["risk_score_breakdown"] = risk_score_result["breakdown"]

    logger.info(f"[MARKET_AGENT] Saving market memo to BigQuery for {counterparty_name}")
    save_memo(record)
    logger.info(f"[MARKET_AGENT] Market assessment completed")
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
    lines = [
        l for l in text.splitlines()
        if "RISK_LEVEL:" not in l and "EXPOSURE_LIMIT:" not in l
    ]
    return "\n".join(lines).strip()
