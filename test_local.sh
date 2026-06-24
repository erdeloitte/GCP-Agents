#!/usr/bin/env bash
# test_local.sh – Start the Flask app locally for development / debugging.
#
# Prerequisites:
#   - Python 3.11+ installed
#   - Google Cloud credentials configured (gcloud auth application-default login)
#
# The app will listen on http://localhost:8080.
# You can send a test Pub/Sub-style POST with curl (see README.md).

set -euo pipefail

# ── Set required environment variables ──────────────────────────────
export BUCKET="${BUCKET:-treasury_comm_agent}"
export BQ_DATASET="${BQ_DATASET:-doc_metadata}"
export PORT="${PORT:-8080}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export GEMINI_API_KEY="${GEMINI_API_KEY:-}"

echo "Installing dependencies…"
pip install --upgrade pip
pip install -r requirements.txt --log pip_install_log.txt

echo ""
echo "Starting local server on http://localhost:${PORT}"
echo "Press Ctrl+C to stop."
echo ""

gunicorn --bind ":${PORT}" --workers 1 --threads 2 --timeout 60 main:app
