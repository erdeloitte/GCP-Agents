# Migration from CrewAI + Tavily to Gemini Orchestrator

## Overview
This migration removes CrewAI and Tavily API dependencies from the Treasury & Commodity Intelligence Platform, replacing them with a streamlined Gemini-based orchestrator using native Google Search grounding. This improves cloud deployment stability and reduces dependency complexity.

## Changes Made

### 1. **Dependency Removal** (`requirements.txt`)
- ❌ Removed `crewai==0.80.0` — CrewAI orchestration framework
- ❌ Removed `crewai-tools==0.17.0` — Web scraping and Tavily search tools
- ✅ Retained `google-genai==1.16.0` — Gemini API client (orchestrator)
- ✅ Retained `yfinance==0.2.43` — Yahoo Finance data via built-in tools

### 2. **Agent Refactoring**

#### **credit_agent.py**
**Before:**
- Conditionally used CrewAI if `TAVILY_API_KEY` environment variable was set
- Fell back to Gemini if key missing (unreliable behavior)
- Created CrewAI Agent with search/scrape tools
- No structured logging

**After:**
- ✅ Now uses **Gemini exclusively** as orchestrator
- ✅ Removed all CrewAI fallback logic
- ✅ **Added comprehensive logging** with `[CREDIT_AGENT]` prefix:
  - Logs counterparty name and financial data at start
  - Logs when Gemini is invoked
  - Logs parsed verdict (Risk Level, Credit Limit, Payment Terms)
  - Logs risk score calculation and breakdown
  - Logs tool calls executed
  - Logs BigQuery persistence
  - Logs completion

#### **market_agent.py**
- ✅ **Added comprehensive logging** with `[MARKET_AGENT]` prefix:
  - Logs financial data summary (Revenue, Sector, EBITDA Margin)
  - Logs Gemini invocation with temperature setting
  - Logs parsed verdict and tool call count
  - Logs risk score breakdown
  - Logs search queries performed
  - Logs BigQuery persistence and completion

#### **liquidity_agent.py**
- ✅ **Added comprehensive logging** with `[LIQUIDITY_AGENT]` prefix:
  - Logs financial data summary (Current Ratio, Revenue, Debt/Equity)
  - Logs Gemini invocation with temperature setting (0.2 for precision)
  - Logs parsed verdict and tool call count
  - Logs risk score breakdown
  - Logs BigQuery persistence and completion

### 3. **Gemini Orchestrator Enhancement** (`agent_base.py`)

**New Logging Features:**
- ✅ `[GEMINI]` prefix for all orchestrator logs
- ✅ Logs model initialization with temperature/token settings
- ✅ Logs prompt length for debugging
- ✅ Logs grounding metadata extraction:
  - Web search queries executed
  - Grounding chunks/sources count
  - Citation URLs
- ✅ Logs tool invocation details:
  - Tool call count and names
  - Custom function calls (`get_headlines_tool`, `get_stock_price_tool`)
  - Google Search built-in calls
  - Tool responses (truncated at 200 chars)
- ✅ Logs error handling with full exception stack trace
- ✅ Structured logging format: `timestamp - logger - level - message`

### 4. **Docker Configuration** (`Dockerfile`)

**Changes:**
- ✅ Removed transitive CrewAI/Tavily dependencies
- ✅ Added all agent files explicitly:
  - `agent_base.py`
  - `credit_agent.py`
  - `market_agent.py`
  - `liquidity_agent.py`
  - `risk_scorer.py`
  - `market_data_helper.py`
- ✅ Added comment clarifying CrewAI removal
- ✅ Reduced final image size (smaller transitive dependency tree)

### 5. **Deployment Configuration** (`deploy.sh`)

**Changes:**
- ✅ Removed `TAVILY_API_KEY` from required environment variables
- ✅ Removed `TAVILY_API_KEY` from environment variable defaults
- ✅ Removed `TAVILY_API_KEY` from `gcloud run deploy --set-env-vars`
- ✅ Updated documentation to clarify only `GEMINI_API_KEY` (and optional `ANTHROPIC_API_KEY`) are needed
- ✅ Simplified deployment validation logic

## Logging Examples

### Credit Agent Logs
```
[CREDIT_AGENT] Starting credit assessment for Glencore
[CREDIT_AGENT] Financial data summary: Revenue=$48000.00M, Debt/Equity=0.45x, Rating=BBB
[CREDIT_AGENT] Invoking Gemini orchestrator with credit assessment prompt
[GEMINI] Initializing chat session with model=gemini-3.5-flash, temperature=0.3
[GEMINI] Chat session created, sending message to gemini-3.5-flash
[GEMINI] Response received, length: 1247 characters
[GEMINI] Web search queries: ["Glencore credit rating 2025", "Glencore debt EBITDA 2025"]
[GEMINI] Total tool calls executed: 2
[CREDIT_AGENT] Parsed verdict: Risk Level=MEDIUM, Credit Limit=USD 150M, Payment Terms=Letter of Credit
[CREDIT_AGENT] Risk score calculated: 62/100 - Moderate risk profile
[CREDIT_AGENT] Saving credit memo to BigQuery for Glencore
```

### Market Agent Logs
```
[MARKET_AGENT] Starting market risk assessment for Shell
[MARKET_AGENT] Financial data: Revenue=$390000.00M, Sector=Energy, EBITDA Margin=25.3%
[MARKET_AGENT] Invoking Gemini orchestrator for market risk analysis
[GEMINI] Tool call [1]: get_headlines_tool()
[GEMINI] Tool call [2]: google_search()
[GEMINI] Total tool calls executed: 2
[MARKET_AGENT] Parsed verdict: Risk Level=LOW, Exposure Limit=USD 500M
```

## Backward Compatibility

- ✅ **Removed hardening required:** Remove `TAVILY_API_KEY` from Cloud Run service environment variables
- ✅ **No impact on BigQuery schema:** Memo records saved identically
- ✅ **API endpoint unchanged:** `/ingest`, `/dashboard` routes work as before
- ✅ **Pub/Sub integration unchanged:** File uploads trigger processing identically

## Benefits

| Aspect | Before | After |
|--------|--------|-------|
| **Dependencies** | CrewAI + Tavily (20+ transitive packages) | Gemini SDK (minimal dependencies) |
| **Failure points** | TAVILY_API_KEY requirement + CrewAI complexity | Single Gemini API call |
| **Logging** | Basic error catching only | Structured, granular agent & orchestrator logs |
| **Cloud Run stability** | Intermittent CrewAI initialization issues | Consistent Gemini behavior |
| **Debugging** | Difficult to trace agent decisions | Full execution trace via logs |
| **Container size** | Larger (CrewAI deps) | Smaller (minimal transitive deps) |

## Deployment Steps

1. **Update environment:**
   ```bash
   export GEMINI_API_KEY="your-gemini-api-key"
   export PROJECT_ID="your-gcp-project"
   export REGION="europe-west4"
   # Do NOT export TAVILY_API_KEY (no longer needed)
   ```

2. **Run deployment:**
   ```bash
   ./deploy.sh
   ```

3. **Verify logs in Cloud Run:**
   ```bash
   gcloud run logs read treasury-ingestor --limit 50 --region europe-west4
   # Look for [CREDIT_AGENT], [MARKET_AGENT], [LIQUIDITY_AGENT], [GEMINI] prefixes
   ```

## Testing

### Manual Test
```python
from credit_agent import run as credit_run

result = credit_run(
    "Glencore",
    {
        "country": "Switzerland",
        "sector": "Commodity Trading",
        "credit_rating": "BBB",
        "revenue_usd_m": 45000,
        "ebitda_usd_m": 5400,
        "ebitda_margin_pct": 12.0,
        "total_debt_usd_m": 8000,
        "debt_to_equity": 0.45,
        "current_ratio": 1.2,
        "net_income_usd_m": 2500,
        "total_assets_usd_m": 85000,
    }
)
print(result["risk_level"])  # Should be LOW, MEDIUM, HIGH, or CRITICAL
print(result["credit_limit"])  # Should be "USD XXM"
```

**Expected logs (in stdout/stderr):**
```
[CREDIT_AGENT] Starting credit assessment for Glencore
[GEMINI] Initializing chat session with model=gemini-3.5-flash, temperature=0.3
[GEMINI] Web search queries: [...]
[CREDIT_AGENT] Parsed verdict: Risk Level=..., Credit Limit=..., Payment Terms=...
[CREDIT_AGENT] Credit assessment completed for Glencore
```

## Troubleshooting

### If logs are not appearing:
1. Verify `GEMINI_API_KEY` is set correctly
2. Check Cloud Run service has sufficient permissions to call Gemini API
3. Ensure `logging.basicConfig()` isn't being overridden elsewhere

### If Gemini calls fail:
1. Check API quota in Google Cloud Console
2. Verify API is enabled: `gcloud services enable generativeai.googleapis.com`
3. Check error logs for rate limiting: `[GEMINI] Exception during call_gemini:`

### If BigQuery writes fail:
1. Verify service account has `BigQuery Data Editor` role
2. Check `BQ_DATASET` environment variable is set correctly
3. Review `save_memo()` logs in `agent_base.py`

## Migration Checklist

- [x] Remove CrewAI and Tavily from requirements.txt
- [x] Update credit_agent.py to use Gemini exclusively
- [x] Add comprehensive logging to all agents
- [x] Update Dockerfile to reflect dependency changes
- [x] Update deploy.sh to remove TAVILY_API_KEY
- [x] Test Gemini API calls work as expected
- [x] Verify BigQuery persistence works
- [x] Check Cloud Run deployment completes
- [x] Review Cloud Run logs for [GEMINI] and agent prefixes
- [x] Test end-to-end Pub/Sub → OCR → Agents → BigQuery flow

---

**Migration Date:** 2026-06-24  
**Status:** ✅ Complete  
**Impact:** Non-breaking (requires TAVILY_API_KEY removal from deployment)
