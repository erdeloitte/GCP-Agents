"""dashboard.py
Treasury & Commodity Counterparty Analytics – web dashboard with LLM chat.

Endpoints:
  GET  /                 – main dashboard
  GET  /api/counterparties – JSON data for the table (supports ?search=&sector=)
  GET  /api/stats        – summary KPIs as JSON
  POST /api/chat         – Gemini-powered Q&A over counterparty data
"""
import os
from google import genai
from flask import Flask, render_template, request, jsonify
from bigquery_helper import get_counterparties, get_summary_stats, build_llm_context

app = Flask(__name__, template_folder=os.path.dirname(os.path.abspath(__file__)))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


def _get_gemini_response(question: str, context: str) -> str:
    """Call Gemini 3.5 Flash (free tier) with BQ context injected."""
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            "You are a treasury and commodity trading analyst assistant. "
            "Answer the user's question using ONLY the data provided below. "
            "Be concise, quantitative, and flag any credit or liquidity risks.\n\n"
            f"DATA:\n{context}\n\n"
            f"QUESTION: {question}"
        )
        response = client.models.generate_content(model="gemini-3.5-flash", contents=prompt)
        return response.text
    except Exception as e:
        return f"LLM error: {e}"


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/counterparties")
def api_counterparties():
    search = request.args.get("search", "")
    sector = request.args.get("sector", "")
    rows   = get_counterparties(search=search or None, sector=sector or None)
    # Serialise datetime objects to string
    for r in rows:
        if "upload_date" in r and hasattr(r["upload_date"], "isoformat"):
            r["upload_date"] = r["upload_date"].isoformat()
    return jsonify(rows)


@app.route("/api/stats")
def api_stats():
    return jsonify(get_summary_stats())


@app.route("/api/upload", methods=["POST"])
def api_upload():
    from ocr_simulator import simulate_ocr
    from claude_ocr_enhance import claude_ocr_fallback
    from datetime import datetime, timezone

    if "file" not in request.files:
        return jsonify({"error": "No file in request"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    content = file.read()

    # Step 1 — try standard OCR first
    try:
        records = simulate_ocr(content, filename=file.filename)
    except Exception as e:
        records = []

    # Step 2 — if standard OCR is empty or uncertain, try Claude enhancement
    claude_result = None
    if not records or len(records) == 0:
        try:
            claude_result = claude_ocr_fallback(content, file.filename, records)
            records = claude_result.get("records", [])
        except Exception:
            pass

    if not records:
        return jsonify({"error": "No records extracted. Check the file has the required columns."}), 422

    now = datetime.now(timezone.utc).isoformat()
    for r in records:
        r["document_name"] = file.filename
        r["upload_date"] = now

    # Step 3 — check for duplicates
    duplicates_flagged = []
    if claude_result:
        duplicates_flagged = claude_result.get("duplicates_flagged", [])

    # If duplicates detected, flag for user review (don't auto-insert yet)
    if duplicates_flagged:
        return jsonify({
            "status": "duplicates_detected",
            "message": "One or more counterparties are already registered. Review required.",
            "duplicates": duplicates_flagged,
            "records": records,
            "filename": file.filename,
            "requires_approval": True,
        }), 202

    # Step 4 — insert into BigQuery (optional; skipped if credentials not configured)
    bq_warning = None
    ingested_count = 0
    try:
        from bigquery_helper import insert_counterparty
        for r in records:
            insert_counterparty(r)
            ingested_count += 1
    except Exception as e:
        bq_warning = str(e)

    # Step 5 — optionally mirror to GCS
    if os.getenv("BUCKET"):
        try:
            from cloud_storage_helper import upload_blob
            upload_blob(file.filename, content)
        except Exception:
            pass  # non-fatal

    resp = {
        "ingested": ingested_count,
        "filename": file.filename,
        "records": records,
        "extraction_method": claude_result.get("extraction_method", "standard_ocr") if claude_result else "standard_ocr",
    }
    if bq_warning:
        resp["warning"] = f"Parsed OK but BigQuery insert failed: {bq_warning}"
    if claude_result:
        resp["claude_confidence"] = claude_result.get("confidence", "unknown")
    return jsonify(resp)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body     = request.get_json(force=True) or {}
    question = body.get("question", "").strip()
    company  = body.get("company", "").strip() or None

    if not question:
        return jsonify({"error": "No question provided"}), 400
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured on the server"}), 503

    context = build_llm_context(company_name=company)
    answer  = _get_gemini_response(question, context)
    return jsonify({"answer": answer})


@app.route("/api/approve-duplicate", methods=["POST"])
def api_approve_duplicate():
    """User approves update of a duplicate counterparty."""
    from datetime import datetime, timezone

    body = request.get_json(force=True) or {}
    records = body.get("records", [])

    if not records:
        return jsonify({"error": "No records to approve"}), 400

    now = datetime.now(timezone.utc).isoformat()
    ingested_count = 0
    bq_warning = None

    try:
        from bigquery_helper import insert_counterparty
        for r in records:
            r["document_name"] = body.get("filename", "approved_update")
            r["upload_date"] = now
            insert_counterparty(r)
            ingested_count += 1
    except Exception as e:
        bq_warning = str(e)

    resp = {
        "status": "approved",
        "ingested": ingested_count,
        "message": f"{ingested_count} record(s) updated successfully."
    }
    if bq_warning:
        resp["warning"] = bq_warning

    return jsonify(resp)


@app.route("/api/indicators/<counterparty>")
def api_indicators(counterparty):
    """Get enhanced indicators for a specific counterparty (Task 4)."""
    from bigquery_helper import get_counterparty_indicators

    indicators = get_counterparty_indicators(counterparty)
    if "error" in indicators:
        return jsonify(indicators), 404
    return jsonify(indicators)


@app.route("/api/deposits/<counterparty>")
def api_deposits(counterparty):
    """Get deposit records for a specific counterparty."""
    from bigquery_helper import get_deposits_by_counterparty

    deposits = get_deposits_by_counterparty(counterparty)
    return jsonify(deposits)


@app.route("/api/deposits-chart-data")
def api_deposits_chart_data():
    """Get aggregated deposit data for chart generation."""
    from bigquery_helper import get_bq_client

    try:
        client = get_bq_client()
        query = """
            SELECT
                counterparty_name,
                SUM(amount_usd) as total_usd,
                COUNT(*) as count
            FROM `treasury_analytics.deposits`
            GROUP BY counterparty_name
            ORDER BY total_usd DESC
            LIMIT 20
        """
        rows = list(client.query(query))
        data = [{"counterparty": row.counterparty_name, "amount": row.total_usd, "count": row.count} for row in rows]
        return jsonify({"data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/market-context")
def api_market_context():
    """Provide context for market data assistant with deposits integration."""
    from bigquery_helper import get_counterparties, get_bq_client

    try:
        counterparties = get_counterparties()

        # Get deposit summary
        client = get_bq_client()
        dep_query = """
            SELECT
                COUNT(DISTINCT counterparty_name) as num_counterparties_with_deposits,
                SUM(amount_usd) as total_deposits_usd,
                AVG(amount_usd) as avg_deposit_usd
            FROM `treasury_analytics.deposits`
        """
        dep_rows = list(client.query(dep_query))
        dep_summary = dict(dep_rows[0]) if dep_rows else {}

        return jsonify({
            "total_counterparties": len(counterparties),
            "deposits_summary": dep_summary,
            "sample_counterparties": [cp["company_name"] for cp in counterparties[:10]]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
