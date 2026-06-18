#!/usr/bin/env bash
# deploy.sh – Build, push, and deploy the Treasury & Commodity Counterparty
#             Analytics platform to Google Cloud Run.
#
# Required env vars (export before running):
#   PROJECT_ID    – GCP project ID          (e.g. my-gcp-project)
#   REGION        – Deployment region        (e.g. europe-west4)
#   SERVICE_NAME  – Ingestion service name   (e.g. treasury-ingestor)
#   DASH_SERVICE  – Dashboard service name   (e.g. treasury-dashboard)
#   BUCKET        – Cloud Storage bucket     (e.g. treasury_comm_agent)
#   BQ_DATASET    – BigQuery dataset         (e.g. treasury_analytics)
#   GEMINI_API_KEY – Google AI Studio key   (free tier at aistudio.google.com)

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────
export PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project)}"
export BUCKET="${BUCKET:-treasury_comm_agent}"
export REGION="${REGION:-europe-west4}"
export SERVICE_NAME="${SERVICE_NAME:-treasury-ingestor}"
export DASH_SERVICE="${DASH_SERVICE:-treasury-dashboard}"
export BQ_DATASET="${BQ_DATASET:-treasury_analytics}"
export GEMINI_API_KEY="${GEMINI_API_KEY:-}"

# ── Validate ─────────────────────────────────────────────────────────
for var in PROJECT_ID REGION SERVICE_NAME DASH_SERVICE BUCKET BQ_DATASET; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: $var is not set. Export it before running ./deploy.sh"; exit 1
  fi
done

if [[ -z "${GEMINI_API_KEY}" ]]; then
  echo "WARNING: GEMINI_API_KEY not set — the AI chat feature will return 503."
  echo "         Get a free key at https://aistudio.google.com/app/apikey"
fi

# ── BigQuery setup ───────────────────────────────────────────────────
echo "==> Creating BigQuery dataset and table (idempotent)…"
bq --project_id "${PROJECT_ID}" mk --dataset --location EU "${BQ_DATASET}" 2>/dev/null || true
bq --project_id "${PROJECT_ID}" query --use_legacy_sql=false "
  CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${BQ_DATASET}.agent_memos\` (
    id                STRING,
    counterparty_name STRING,
    agent_type        STRING,
    risk_level        STRING,
    memo              STRING,
    exposure_proposal STRING,
    created_at        TIMESTAMP
  )"

bq --project_id "${PROJECT_ID}" query --use_legacy_sql=false "
  CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${BQ_DATASET}.counterparties\` (
    company_name       STRING,
    country            STRING,
    sector             STRING,
    credit_rating      STRING,
    period_year        INT64,
    revenue_usd_m      FLOAT64,
    ebitda_usd_m       FLOAT64,
    ebitda_margin_pct  FLOAT64,
    net_income_usd_m   FLOAT64,
    total_assets_usd_m FLOAT64,
    total_debt_usd_m   FLOAT64,
    debt_to_equity     FLOAT64,
    current_ratio      FLOAT64,
    document_name      STRING,
    upload_date        TIMESTAMP
  )"

# ── Cloud Storage & Pub/Sub ──────────────────────────────────────────
echo "==> Creating Cloud Storage bucket…"
gsutil mb -p "${PROJECT_ID}" -l "${REGION}" "gs://${BUCKET}" 2>/dev/null || true

echo "==> Creating Pub/Sub topic…"
gcloud pubsub topics create treasury-financials --project "${PROJECT_ID}" 2>/dev/null || true

echo "==> Attaching GCS notification to Pub/Sub…"
gsutil notification create \
  -t "projects/${PROJECT_ID}/topics/treasury-financials" \
  -f json \
  "gs://${BUCKET}" 2>/dev/null || true

# ── Build & deploy ingestion service ────────────────────────────────
INGESTOR_IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
echo "==> Building ingestor image: ${INGESTOR_IMAGE}"
gcloud builds submit --project "${PROJECT_ID}" --tag "${INGESTOR_IMAGE}" .

echo "==> Deploying ingestor to Cloud Run…"
gcloud run deploy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --image "${INGESTOR_IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "BUCKET=${BUCKET},BQ_DATASET=${BQ_DATASET}"

INGESTOR_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format='value(status.url)')

echo "==> Creating Pub/Sub push subscription → ${INGESTOR_URL}"
gcloud pubsub subscriptions create treasury-ingestor-sub \
  --topic treasury-financials \
  --push-endpoint "${INGESTOR_URL}" \
  --project "${PROJECT_ID}" 2>/dev/null || true

# ── Build & deploy dashboard service ────────────────────────────────
DASH_IMAGE="gcr.io/${PROJECT_ID}/${DASH_SERVICE}"
echo "==> Building dashboard image: ${DASH_IMAGE}"
# Dashboard uses dashboard.py as entry point — swap CMD in a copy
cp main.py main.py.bak
cp dashboard.py main_dash_entry.py
cat > Dockerfile.dash <<'EOF'
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD gunicorn --bind :${PORT:-8080} --workers 1 --threads 8 --timeout 0 dashboard:app
EOF
gcloud builds submit --project "${PROJECT_ID}" --tag "${DASH_IMAGE}" \
  --config /dev/stdin . <<'CLOUDBUILD'
steps:
- name: 'gcr.io/cloud-builders/docker'
  args: ['build', '-f', 'Dockerfile.dash', '-t', '$_IMAGE', '.']
- name: 'gcr.io/cloud-builders/docker'
  args: ['push', '$_IMAGE']
images: ['$_IMAGE']
CLOUDBUILD

gcloud run deploy "${DASH_SERVICE}" \
  --project "${PROJECT_ID}" \
  --image "${DASH_IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "BQ_DATASET=${BQ_DATASET},GEMINI_API_KEY=${GEMINI_API_KEY}"

DASH_URL=$(gcloud run services describe "${DASH_SERVICE}" \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format='value(status.url)')

rm -f Dockerfile.dash main_dash_entry.py

echo ""
echo "========================================================"
echo " Treasury & Commodity Intelligence Platform — deployed!"
echo "========================================================"
echo " Ingestor (pipeline):  ${INGESTOR_URL}"
echo " Dashboard (UI + AI):  ${DASH_URL}"
echo ""
echo " Quick test — upload a sample financial CSV:"
echo "   gsutil cp sample_counterparty.csv gs://${BUCKET}/"
echo " Then open the dashboard: ${DASH_URL}"
