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

app = Flask(
    __name__,
    template_folder=os.path.dirname(os.path.abspath(__file__)),
    static_folder=os.path.dirname(os.path.abspath(__file__)),
    static_url_path=''
)

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
    from datetime import datetime, timezone

    if "file" not in request.files:
        return jsonify({"error": "No file in request"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    content = file.read()

    # Step 1 — parse the file
    try:
        records = simulate_ocr(content, filename=file.filename)
    except Exception as e:
        return jsonify({"error": f"Could not parse file: {e}"}), 422

    if not records:
        return jsonify({"error": "No records extracted. Check the file has the required columns."}), 422

    now = datetime.now(timezone.utc).isoformat()
    for r in records:
        r["document_name"] = file.filename
        r["upload_date"] = now

    # Step 2 — insert into BigQuery (optional; skipped if credentials not configured)
    bq_warning = None
    try:
        from bigquery_helper import insert_counterparty
        for r in records:
            insert_counterparty(r)
    except Exception as e:
        bq_warning = str(e)

    # Step 3 — optionally mirror to GCS
    if os.getenv("BUCKET"):
        try:
            from cloud_storage_helper import upload_blob
            upload_blob(file.filename, content)
        except Exception:
            pass  # non-fatal

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
        return jsonify({"error": "GEMINI_API_KEY not configured on the server"}), 503

    context = build_llm_context(company_name=company)
    answer  = _get_gemini_response(question, context)
    return jsonify({"answer": answer})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
