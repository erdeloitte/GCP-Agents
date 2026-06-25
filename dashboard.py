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
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
load_dotenv()  # must be before any local imports that read os.getenv at module level

from flask import Flask, render_template, request, jsonify
from werkzeug.exceptions import HTTPException
from bigquery_helper import (
    get_counterparties, get_summary_stats, build_llm_context,
    get_counterparty_detail, get_memos,
)

# ---- Agent modules imported at module level to avoid per-request overhead ----
import market_agent
import credit_agent
import liquidity_agent
from agent_base import call_gemini, build_memo_record, save_memo

logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=os.path.dirname(os.path.abspath(__file__)),
    static_folder=os.path.dirname(os.path.abspath(__file__)),
    static_url_path=''
)
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# -- IAP Authentication Step --
@app.before_request
def verify_iap_token():
    """
    Ensures that requests are coming through Identity-Aware Proxy.
    In local development, this can be bypassed via an environment variable.
    """
    skip = os.getenv("SKIP_IAP_CHECK", "False") == "True"
    # Skip check for local development or explicit overrides
    if app.debug or os.getenv("FLASK_DEBUG") == "1" or skip:
        return

    # IAP injects this header after successful authentication
    iap_jwt = request.headers.get('X-Goog-IAP-JWT-Assertion')
    
    if not iap_jwt:
        logger.warning("Unauthorized access attempt: Missing IAP JWT header")
        return jsonify({
            "error": "Unauthorized: This application must be accessed through the secure corporate proxy."
        }), 401

    logger.info("Authenticated request received via IAP")


@app.errorhandler(Exception)
def handle_exception(e):
    """Generic error handler — returns a safe message without internal details."""
    # Let standard HTTP errors (like 404 or 405) be handled normally by Flask
    # rather than logging them as unhandled application crashes (500).
    if isinstance(e, HTTPException):
        return e

    logger.exception("Unhandled exception in request %s %s", request.method, request.path)
    return jsonify({"error": "An internal error occurred. Please try again or contact support."}), 500


# ── Helpers ──────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Simple connectivity check."""
    return "OK", 200

def _gemini(prompt: str, temperature: float = 0.3) -> str:
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEY not configured."
    try:
        from google import genai
        from google.genai import types
        from agent_base import GEMINI_MODEL
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                tool_config=types.ToolConfig(includeServerSideToolInvocations=True),
                temperature=temperature,
                max_output_tokens=2048,
            ),
        )
        text = response.text
        return text.strip() if text else "No response text returned."
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

@app.route("/", methods=["GET"])
def index():
    return render_template("dashboard.html")


@app.route("/favicon.ico")
def favicon():
    """Explicitly handle favicon requests to avoid 404 noise."""
    return "", 204


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


@app.route("/ingest", methods=["POST"])
def api_pubsub_ingest():
    """Background ingestion handler triggered by Pub/Sub."""
    import base64
    import json
    from cloud_storage_helper import download_blob
    from ocr_simulator import simulate_ocr
    from bigquery_helper import insert_counterparty
    from datetime import datetime, timezone

    envelope = request.get_json()
    if not envelope or "message" not in envelope:
        return "Invalid Pub/Sub message", 400

    pubsub_message = envelope["message"]
    if "data" not in pubsub_message:
        return "No data in message", 400

    try:
        # Decode GCS notification payload
        data_str = base64.b64decode(pubsub_message["data"]).decode("utf-8")
        data = json.loads(data_str)
        filename = data.get("name")

        if not filename:
            return "OK", 200

        logger.info(f"Background processing started for: {filename}")
        content = download_blob(filename)
        records = simulate_ocr(content, filename=filename)

        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            r["document_name"] = filename
            r["upload_date"]   = now
            insert_counterparty(r)

        return "OK", 200
    except Exception as e:
        logger.error(f"Background ingestion error: {e}")
        return f"Error: {e}", 500


@app.route("/api/upload", methods=["POST"])
def api_upload():
    from ocr_simulator import simulate_ocr
    from claude_ocr_enhance import claude_ocr_fallback
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

    # Fallback to Claude if standard OCR fails to find data
    if not records:
        records = claude_ocr_fallback(content, file.filename, []).get("records", [])

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
    question = (body.get("question") or "").strip()
    company  = (body.get("company") or "").strip() or None

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
        "You are a treasury and commodity trading senior analyst. "
        "Answer the user's question. Use the provided database context to answer questions about the counterparties, "
        "and utilize your web search grounding tool to lookup any live stock prices, news, external financials, or general industry trends. "
        "Be quantitative, concise, professional, and flag any credit or liquidity risks.\n\n"
        f"DATA:\n{context}\n\nQUESTION: {question}"
    )
    return jsonify({"answer": _gemini(prompt)})


# ── Agent routes ──────────────────────────────────────────────────────────────

def _get_body_counterparty() -> str:
    body = request.get_json(force=True) or {}
    name = (body.get("counterparty") or "").strip()
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

    result = market_agent.run(canonical, data)
    return jsonify(result)


@app.route("/api/agent/credit", methods=["POST"])
def api_agent_credit():
    try:
        name = _get_body_counterparty()
        canonical, data = _resolve_counterparty(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    result = credit_agent.run(canonical, data)
    return jsonify(result)


@app.route("/api/agent/liquidity", methods=["POST"])
def api_agent_liquidity():
    try:
        name = _get_body_counterparty()
        canonical, data = _resolve_counterparty(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    result = liquidity_agent.run(canonical, data)
    return jsonify(result)


@app.route("/api/agent/orchestrate", methods=["POST"])
def api_agent_orchestrate():
    try:
        name = _get_body_counterparty()
        canonical, data = _resolve_counterparty(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # -----------------------------------------------------------------------
    # Run all three agents in PARALLEL using a thread pool to cut latency
    # from ~60–90s (serial) down to ~20–30s (limited by slowest agent).
    # -----------------------------------------------------------------------
    results: dict = {}
    errors:  dict = {}
    tasks = {
        "market":    lambda: market_agent.run(canonical, data),
        "credit":    lambda: credit_agent.run(canonical, data),
        "liquidity": lambda: liquidity_agent.run(canonical, data),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_map = {pool.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                logger.error("Agent '%s' failed: %s", key, exc)
                errors[key] = str(exc)
                results[key] = {
                    "risk_level": "HIGH",
                    "memo": f"Agent failed: {exc}",
                    "exposure_proposal": "N/A",
                }

    market    = results["market"]
    credit    = results["credit"]
    liquidity = results["liquidity"]

    # Weighted aggregate risk: market 40%, credit 35%, liquidity 25%
    levels = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    weights = {"market": 0.40, "credit": 0.35, "liquidity": 0.25}
    weighted_sum = sum(
        levels.get(results[k].get("risk_level", "MEDIUM"), 2) * w
        for k, w in weights.items()
    )
    # Round to nearest integer, map back to label
    inv_levels = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}
    agg_level = inv_levels.get(round(weighted_sum), "HIGH")

    summary_prompt = (
        "You are a head of treasury at an LNG trading company. "
        "Synthesise the three specialist memos below into an executive summary (6–8 sentences). "
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
        memo=str(summary),
        exposure_proposal=(
            f"Market: {market['exposure_proposal']} | "
            f"Credit: {credit['exposure_proposal']} | "
            f"Liquidity: {liquidity['exposure_proposal']}"
        ),
    )
    save_memo(orch_record)

    response_payload = {
        "counterparty":   canonical,
        "aggregate_risk": agg_level,
        "summary":        str(summary),
        "market":         market,
        "credit":         credit,
        "liquidity":      liquidity,
    }
    if errors:
        response_payload["agent_errors"] = errors
    return jsonify(response_payload)


@app.route("/api/memos")
def api_memos():
    cp = request.args.get("counterparty") or None
    at = request.args.get("agent_type") or None
    rows = get_memos(counterparty_name=cp, agent_type=at)
    return jsonify(_serialise(rows))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
