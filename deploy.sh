#!/usr/bin/env bash
# deploy.sh – Build, push, and deploy the Treasury & Commodity Counterparty
#             Analytics platform to Google Cloud Run.
#
# Required env vars (export before running):
#   PROJECT_ID      – GCP project ID          (dttl-nl-genai-sandbox)
#   REGION          – Deployment region        (europe-west4)
#   DASH_SERVICE    – Dashboard service name   (treasury-ingestor and treasury-dashboard)
#   BUCKET          – Cloud Storage bucket     (e.g. treasury_comm_agent)
#   BQ_DATASET      – BigQuery dataset         (e.g. treasury_analytics)
#   GEMINI_API_KEY  – Google AI Studio key   (free tier at aistudio.google.com)
#   ANTHROPIC_API_KEY – Claude API key for advanced features (optional)


set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────
export PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project)}"
export BUCKET="${BUCKET:-treasury_comm_agent}"
export REGION="${REGION:-europe-west4}"
export REPO_NAME="${REPO_NAME:-treasury-repo}"
export DASH_SERVICES="${DASH_SERVICES:-treasury-ingestor treasury-dashboard}"
export BQ_DATASET="${BQ_DATASET:-treasury_analytics}"
export GEMINI_API_KEY="${GEMINI_API_KEY:-}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"



# ── Validate ─────────────────────────────────────────────────────────
for var in PROJECT_ID REGION BUCKET BQ_DATASET; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: $var is not set. Export it before running ./deploy.sh"; exit 1
  fi
done

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "WARNING: GEMINI_API_KEY not set — the AI chat feature will return 503."
  echo "         Get a free key at https://aistudio.google.com/app/apikey"
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "WARNING: ANTHROPIC_API_KEY not set. Advanced features like Claude-based OCR fallback will be disabled."
fi

echo "==> Enabling required APIs..."
gcloud services enable artifactregistry.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com --project "${PROJECT_ID}"

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

# ── Artifact Registry setup ──────────────────────────────────────────
echo "==> Ensuring Artifact Registry repository exists..."
gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Treasury Agent Images" \
    --project="${PROJECT_ID}" 2>/dev/null || true

# ── Build & deploy dashboard service ────────────────────────────────
COMMON_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/treasury-platform"
echo "==> Building platform image: ${COMMON_IMAGE}"

cp dashboard.py main_dash_entry.py
cat > Dockerfile.dash <<'EOF'
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --default-timeout=100 -r requirements.txt
COPY . .
EXPOSE 8080
CMD gunicorn --bind :${PORT:-8080} --workers 1 --threads 8 --timeout 0 dashboard:app
EOF

gcloud builds submit --project "${PROJECT_ID}" \
  --config /dev/stdin . <<CONFIG
steps:
- name: 'gcr.io/cloud-builders/docker'
  args: ['build', '-f', 'Dockerfile.dash', '-t', '${COMMON_IMAGE}', '.']
images:
- '${COMMON_IMAGE}'
CONFIG

for SVC in ${DASH_SERVICES}; do
  echo "==> Deploying service: ${SVC}"
  gcloud run deploy "${SVC}" \
    --project "${PROJECT_ID}" \
    --image "${COMMON_IMAGE}" \
    --region "${REGION}" \
    --platform managed \
    --no-allow-unauthenticated \
    --set-env-vars "BUCKET=${BUCKET},BQ_DATASET=${BQ_DATASET},GEMINI_API_KEY=${GEMINI_API_KEY},ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
done

INGESTOR_URL=$(gcloud run services describe "treasury-ingestor" \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format='value(status.url)' 2>/dev/null || echo "")

DASH_URL=$(gcloud run services describe "treasury-dashboard" \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format='value(status.url)' 2>/dev/null || echo "${INGESTOR_URL}")

rm -f Dockerfile.dash main_dash_entry.py

echo "==> Updating Pub/Sub subscription to point to Dashboard..."
# For private services, Pub/Sub needs an OIDC token to authenticate with Cloud Run.
echo "==> Resolving Project Number for Project ID: ${PROJECT_ID}..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
echo "==> Project Number: ${PROJECT_NUMBER}"

INVOKER_SA_NAME="treasury-invoker"
INVOKER_SA="${INVOKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PUB_SUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
CURRENT_USER=$(gcloud config get-value account)

# Grant Pub/Sub Service Agent permission to invoke the private Cloud Run service.
echo "==> Setting up dedicated service account for Pub/Sub push authentication..."
gcloud iam service-accounts create "${INVOKER_SA_NAME}" \
    --display-name="Treasury Pub/Sub Invoker" \
    --project="${PROJECT_ID}" 2>/dev/null || true

# 1. Allow the current user to 'actAs' the new service account to create the subscription
echo "==> Granting iam.serviceAccountUser to ${CURRENT_USER} on ${INVOKER_SA}..."
gcloud iam service-accounts add-iam-policy-binding "${INVOKER_SA}" \
    --member="user:assadie@deloitte.nl" \
    --member="user:${CURRENT_USER}" \
    --role="roles/iam.serviceAccountUser" \
    --project="${PROJECT_ID}" --quiet

# 2. Allow Pub/Sub Service Agent to create OIDC tokens for our invoker service account
echo "==> Granting iam.serviceAccountTokenCreator to Pub/Sub on ${INVOKER_SA}..."
gcloud iam service-accounts add-iam-policy-binding "${INVOKER_SA}" \
    --member="serviceAccount:${PUB_SUB_SA}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project="${PROJECT_ID}" --quiet

# 3. Grant the invoker service account permission to call the Cloud Run services
for SVC in ${DASH_SERVICES}; do
  gcloud run services add-iam-policy-binding "${SVC}" \
    --member="serviceAccount:${PUB_SUB_SA}" \
    --member="serviceAccount:${INVOKER_SA}" \
    --member="user:assadie@deloitte.nl" \
    --member="user:${CURRENT_USER}" \
    --role="roles/run.invoker" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" --quiet
done

gcloud pubsub subscriptions delete treasury-ingestor-sub --project "${PROJECT_ID}" 2>/dev/null || true
gcloud pubsub subscriptions create treasury-ingestor-sub \
  --topic treasury-financials \
  --push-endpoint "${DASH_URL}/ingest" \
  --push-auth-service-account="${PUB_SUB_SA}" \
  --push-auth-service-account="${INVOKER_SA}" \
  --project "${PROJECT_ID}"

echo ""
echo "========================================================"
echo " Treasury & Commodity Intelligence Platform — deployed!"
echo "========================================================"
echo " All-in-one Platform:  ${DASH_URL}"
echo ""
echo " Quick test — upload a sample financial CSV:"
echo "   gsutil cp sample_counterparty.csv gs://${BUCKET}/"
echo " Then open the dashboard: ${DASH_URL}"
