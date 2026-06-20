"""dashboard.py
Deloitte AI Hub — Treasury & Commodity Counterparty Analytics

Endpoints:
  GET  /                      – dashboard UI
  GET  /api/counterparties    – counterparty table data
  GET  /api/stats             – KPI summary
  POST /api/upload            – local file ingestion
  POST /api/chat              – Gemini free-form Q&A
  POST /api/agent/market      – Market Risk Agent
  POST /api/agent/credit      – Credit Risk Agent
  POST /api/agent/liquidity   – Liquidity & Settlement Agent
  POST /api/agent/orchestrate – All three agents + executive summary
  GET  /api/memos             – Retrieve saved agent memos
"""
import os
from dotenv import load_dotenv
load_dotenv()  # must be before any local imports that read os.getenv at module level

from flask import Flask, render_template, request, jsonify
from bigquery_helper import (
    get_counterparties, get_summary_stats, build_llm_context,
    get_counterparty_detail, get_memos,
)

app = Flask(__name__, template_folder=os.path.dirname(os.path.abspath(__file__)))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500


# ── Helpers ──────────────────────────────────────────────────────────────────

def _gemini(prompt: str, temperature: float = 0.3) -> str:
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEY not configured."
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=temperature,
                max_output_tokens=2048,
            ),
        )
        return response.text.strip()
    except Exception as e:
        return f"LLM error: {e}"


def _resolve_counterparty(name: str) -> tuple[str, dict | None]:
    """Return (canonical_name, financial_data). Raises ValueError if not found."""
    data = get_counterparty_detail(name)
    if not data:
        raise ValueError(f"Counterparty '{name}' not found in the database.")
    return data.get("company_name", name), data


def _serialise(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return rows


# ── Core routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/counterparties")
def api_counterparties():
    rows = get_counterparties(
        search=request.args.get("search") or None,
        sector=request.args.get("sector") or None,
    )
    return jsonify(_serialise(rows))


@app.route("/api/stats")
def api_stats():
    return jsonify(get_summary_stats())


@app.route("/api/upload", methods=["POST"])
def api_upload():
    from ocr_simulator import simulate_ocr
    from bigquery_helper import insert_counterparty
    from datetime import datetime, timezone

    if "file" not in request.files:
        return jsonify({"error": "No file in request"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    content = file.read()

    try:
        records = simulate_ocr(content, filename=file.filename)
    except Exception as e:
        return jsonify({"error": f"Could not parse file: {e}"}), 422

    if not records:
        return jsonify({"error": "No records extracted. Check the file has the required columns."}), 422

    now = datetime.now(timezone.utc).isoformat()
    for r in records:
        r["document_name"] = file.filename
        r["upload_date"]   = now

    bq_warning = None
    try:
        for r in records:
            insert_counterparty(r)
    except Exception as e:
        bq_warning = str(e)

    if os.getenv("BUCKET"):
        try:
            from cloud_storage_helper import upload_blob
            upload_blob(file.filename, content)
        except Exception:
            pass

    resp = {"ingested": len(records), "filename": file.filename, "records": records}
    if bq_warning:
        resp["warning"] = f"Parsed OK but BigQuery insert failed: {bq_warning}"
    return jsonify(resp)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body     = request.get_json(force=True) or {}
    question = body.get("question", "").strip()
    company  = body.get("company", "").strip() or None

    if not question:
        return jsonify({"error": "No question provided"}), 400
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

    context = build_llm_context(company_name=company)

    # Enrich context with external live market data if a counterparty is explicitly targeted or mentioned
    lookup_name = company
    if not lookup_name:
        for c in ["glencore", "vitol", "shell", "totalenergies", "bp", "chevron"]:
            if c in question.lower():
                lookup_name = c
                break
    if lookup_name:
        from market_data_helper import build_market_context
        market_context = build_market_context(lookup_name)
        if market_context:
            context += f"\n\nExternal Live Market Context:\n{market_context}"

    prompt  = (
        "You are a treasury and commodity trading analyst assistant. "
        "Answer the user's question. Use the provided database context to answer questions about the portfolio counterparties, "
        "and utilize your web search grounding tool to lookup any live stock prices, news, external financials, or general industry trends. "
        "Be quantitative, concise, professional, and flag any credit or liquidity risks.\n\n"
        f"DATA:\n{context}\n\nQUESTION: {question}"
    )
    return jsonify({"answer": _gemini(prompt)})


# ── Agent routes ──────────────────────────────────────────────────────────────

def _get_body_counterparty() -> str:
    body = request.get_json(force=True) or {}
    name = body.get("counterparty", "").strip()
    if not name:
        raise ValueError("counterparty field is required")
    return name


@app.route("/api/agent/market", methods=["POST"])
def api_agent_market():
    try:
        name = _get_body_counterparty()
        canonical, data = _resolve_counterparty(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    import market_agent
    result = market_agent.run(canonical, data)
    return jsonify(result)


@app.route("/api/agent/credit", methods=["POST"])
def api_agent_credit():
    try:
        name = _get_body_counterparty()
        canonical, data = _resolve_counterparty(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    import credit_agent
    result = credit_agent.run(canonical, data)
    return jsonify(result)


@app.route("/api/agent/liquidity", methods=["POST"])
def api_agent_liquidity():
    try:
        name = _get_body_counterparty()
        canonical, data = _resolve_counterparty(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    import liquidity_agent
    result = liquidity_agent.run(canonical, data)
    return jsonify(result)


@app.route("/api/agent/orchestrate", methods=["POST"])
def api_agent_orchestrate():
    try:
        name = _get_body_counterparty()
        canonical, data = _resolve_counterparty(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    import market_agent, credit_agent, liquidity_agent
    from agent_base import call_gemini, build_memo_record, save_memo

    market    = market_agent.run(canonical, data)
    credit    = credit_agent.run(canonical, data)
    liquidity = liquidity_agent.run(canonical, data)

    # Determine aggregate risk
    levels = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    agg_level = max(
        [market["risk_level"], credit["risk_level"], liquidity["risk_level"]],
        key=lambda x: levels.get(x, 2),
    )

    summary_prompt = (
        "You are a head of treasury at an LNG trading company. "
        "Synthesise the three specialist memos below into a single executive summary (6–8 sentences). "
        "Conclude with a clear recommendation: approve / approve with conditions / decline.\n\n"
        f"COUNTERPARTY: {canonical}\n\n"
        f"MARKET AGENT:\n{market['memo']}\nExposure: {market['exposure_proposal']}\n\n"
        f"CREDIT AGENT:\n{credit['memo']}\nExposure: {credit['exposure_proposal']}\n\n"
        f"LIQUIDITY AGENT:\n{liquidity['memo']}\nExposure: {liquidity['exposure_proposal']}\n\n"
        "Write the executive summary now."
    )
    summary = call_gemini(summary_prompt, temperature=0.3)

    orch_record = build_memo_record(
        counterparty=canonical,
        agent_type="orchestrator",
        risk_level=agg_level,
        memo=summary,
        exposure_proposal=(
            f"Market: {market['exposure_proposal']} | "
            f"Credit: {credit['exposure_proposal']} | "
            f"Liquidity: {liquidity['exposure_proposal']}"
        ),
    )
    save_memo(orch_record)

    return jsonify({
        "counterparty": canonical,
        "aggregate_risk": agg_level,
        "summary": summary,
        "market":    market,
        "credit":    credit,
        "liquidity": liquidity,
    })


@app.route("/api/memos")
def api_memos():
    cp = request.args.get("counterparty") or None
    at = request.args.get("agent_type") or None
    rows = get_memos(counterparty_name=cp, agent_type=at)
    return jsonify(_serialise(rows))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
