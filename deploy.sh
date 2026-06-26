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

# ── Load Local .env ──────────────────────────────────────────────────
if [ -f .env ]; then
  echo "==> Loading environment variables from .env..."
  while read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^#.* ]] || [[ -z "$line" ]] || export "$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/[[:space:]]*=[[:space:]]*/=/')"
  done < .env
fi

# ── Defaults ────────────────────────────────────────────────────────
export PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project)}"
export BUCKET="${BUCKET:-treasury_comm_agent}"
export REGION="${REGION:-europe-west4}"
export REPO_NAME="${REPO_NAME:-treasury-repo}"
export DASH_SERVICES="${DASH_SERVICES:-treasury-ingestor treasury-dashboard}"
export BQ_DATASET="${BQ_DATASET:-treasury_analytics}"
export IAP_CLIENT_ID="${IAP_CLIENT_ID:-}"
export IAP_CLIENT_SECRET="${IAP_CLIENT_SECRET:-}"
export LB_DOMAIN="${LB_DOMAIN:-}"
export IAP_USERS="${IAP_USERS:-eruizduarte@deloitte.nl assadie@deloitte.nl}"
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
  compute.googleapis.com \
  iap.googleapis.com \
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

if [[ -z "${LB_DOMAIN}" ]]; then
  echo "ERROR: LB_DOMAIN is not set. Export LB_DOMAIN to a valid domain name for IAP HTTPS access." >&2
  exit 1
fi

echo "==> Deploying private Cloud Run services: ${DASH_SERVICES}"
for SVC in ${DASH_SERVICES}; do
  echo "==> Deploying service: ${SVC}"
  gcloud run deploy "${SVC}" \
    --project "${PROJECT_ID}" \
    --image "${COMMON_IMAGE}" \
    --region "${REGION}" \
    --platform managed \
    --no-allow-unauthenticated \
    --ingress internal-and-cloud-load-balancing \
    --set-env-vars "BUCKET=${BUCKET},BQ_DATASET=${BQ_DATASET},GEMINI_API_KEY=${GEMINI_API_KEY},ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
done

echo "==> Cleaning up any stale load balancer resources..."
gcloud compute forwarding-rules delete "treasury-rule" --global --quiet 2>/dev/null || true
gcloud compute target-https-proxies delete "treasury-https-proxy" --global --quiet 2>/dev/null || true
gcloud compute target-http-proxies delete "treasury-proxy" --global --quiet 2>/dev/null || true
gcloud compute url-maps delete "treasury-url-map" --global --quiet 2>/dev/null || true
gcloud compute ssl-certificates delete "treasury-ssl-cert" --global --quiet 2>/dev/null || true
gcloud compute backend-services delete "treasury-backend" --global --quiet 2>/dev/null || true
gcloud compute network-endpoint-groups delete "treasury-neg" --region="${REGION}" --quiet 2>/dev/null || true
gcloud compute addresses delete "treasury-lb-ip" --global --quiet 2>/dev/null || true

echo "==> Provisioning Global External HTTP Load Balancer..."
IP_NAME="treasury-lb-ip"
NEG_NAME="treasury-neg"
BACKEND_NAME="treasury-backend"
URL_MAP_NAME="treasury-url-map"

# 1. Reserve static IP
gcloud compute addresses create "${IP_NAME}" --global --project "${PROJECT_ID}" 2>/dev/null || true
STATIC_IP=$(gcloud compute addresses describe "${IP_NAME}" --global --format='value(address)' --project "${PROJECT_ID}")

# 2. Create Serverless NEG for the dashboard
gcloud compute network-endpoint-groups create "${NEG_NAME}" \
    --region="${REGION}" \
    --network-endpoint-type=serverless \
    --cloud-run-service="treasury-dashboard" \
    --project "${PROJECT_ID}" 2>/dev/null || true

# 3. Create Backend Service
gcloud compute backend-services create "${BACKEND_NAME}" \
    --load-balancing-scheme=EXTERNAL_MANAGED \
    --global \
    --project "${PROJECT_ID}" 2>/dev/null || true

# 4. Add NEG to Backend Service
gcloud compute backend-services add-backend "${BACKEND_NAME}" \
    --global \
    --network-endpoint-group="${NEG_NAME}" \
    --network-endpoint-group-region="${REGION}" \
    --project "${PROJECT_ID}" 2>/dev/null || true

# 5. Create URL Map, Target Proxy, and Forwarding Rule
gcloud compute url-maps create "${URL_MAP_NAME}" --default-service "${BACKEND_NAME}" --project "${PROJECT_ID}" 2>/dev/null || true
CERT_NAME="treasury-ssl-cert"

gcloud compute ssl-certificates create "${CERT_NAME}" \
    --domains="${LB_DOMAIN}" \
    --global \
    --project "${PROJECT_ID}" 2>/dev/null || true

gcloud compute target-https-proxies create "treasury-https-proxy" \
    --url-map "${URL_MAP_NAME}" \
    --ssl-certificates "${CERT_NAME}" \
    --project "${PROJECT_ID}" 2>/dev/null || true

gcloud compute forwarding-rules delete "treasury-rule" --global --quiet 2>/dev/null || true
gcloud compute forwarding-rules create "treasury-rule" \
    --load-balancing-scheme=EXTERNAL_MANAGED \
    --address="${IP_NAME}" \
    --global \
    --target-https-proxy="treasury-https-proxy" \
    --ports=443 \
    --project "${PROJECT_ID}" 2>/dev/null || true

echo "==> Resolving Project Number for Project ID: ${PROJECT_ID}..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')

if [[ -n "${IAP_CLIENT_ID}" && -n "${IAP_CLIENT_SECRET}" ]]; then
  echo "==> Enabling IAP auth on backend service ${BACKEND_NAME}..."
  gcloud compute backend-services update "${BACKEND_NAME}" \
      --global \
      --iap=enabled,oauth2-client-id="${IAP_CLIENT_ID}",oauth2-client-secret="${IAP_CLIENT_SECRET}" \
      --project "${PROJECT_ID}"
else
  echo "WARNING: IAP_CLIENT_ID or IAP_CLIENT_SECRET not set. Skipping IAP backend configuration."
fi

echo "==> Enabling IAP for ${LB_DOMAIN}..."
gcloud iap web enable --resource=projects/${PROJECT_NUMBER}/iap_web --project "${PROJECT_ID}" 2>/dev/null || true

for USER in ${IAP_USERS}; do
  echo "==> Granting IAP access to ${USER}..."
  gcloud iap web add-iam-policy-binding \
      --resource=projects/${PROJECT_NUMBER}/iap_web \
      --member="user:${USER}" \
      --role="roles/iap.httpsResourceAccessor" \
      --project="${PROJECT_ID}"
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
echo "==> Setting 