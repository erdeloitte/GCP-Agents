"""main.py
Cloud Run service – Treasury & Commodity Counterparty Ingestion Pipeline.

Flow:
  Cloud Storage (financial document upload)
    → Pub/Sub OBJECT_FINALIZE event
      → this service (POST /)
        → parse financials → store in BigQuery
"""
import os
from dotenv import load_dotenv
load_dotenv()

import json
import base64
from datetime import datetime, timezone
from flask import Flask, request

from ocr_simulator import simulate_ocr
from cloud_storage_helper import download_blob
from bigquery_helper import insert_counterparty

app = Flask(__name__)


def enrich_record(record: dict, document_name: str) -> dict:
    """Attach document provenance and ingestion timestamp to a parsed record."""
    record["document_name"] = document_name
    record["upload_date"]   = datetime.now(timezone.utc).isoformat()
    return record


@app.route("/", methods=["POST"])
def handler():
    envelope = request.get_json()
    if not envelope:
        return "No Pub/Sub message received", 400

    message = envelope.get("message")
    if not message:
        return "Invalid Pub/Sub format", 400

    data = message.get("data")
    if not data:
        return "No data in Pub/Sub message", 400

    try:
        obj_info = json.loads(base64.b64decode(data).decode())
    except Exception as e:
        return f"Error decoding message: {e}", 400

    filename = obj_info.get("name")
    if not filename:
        return "No object name in message", 400

    try:
        content = download_blob(filename)
        records = simulate_ocr(content)
        for record in records:
            enriched = enrich_record(record, filename)
            insert_counterparty(enriched)
    except Exception as e:
        return f"Processing error: {e}", 500

    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
