#!/usr/bin/env bash
# deploy.sh – Build, push, and deploy the Cloud Run service.
#
# Required environment variables (export them before running):
#   PROJECT_ID   – GCP project number or ID  (e.g. 236909908642)
#   REGION       – Target region              (e.g. europe-west4)
#   SERVICE_NAME – Cloud Run service name     (e.g. doc-processor)
#   BUCKET       – Cloud Storage bucket name  (e.g. treasury_comm_agent)
#   BQ_DATASET   – BigQuery dataset name      (e.g. doc_metadata)

set -euo pipefail

# ── Set defaults and dynamic lookups ────────────────────────────────
export PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project)}"
export BUCKET="${BUCKET:-treasury_comm_agent}"
export REGION="${REGION:-europe-west4}"
export SERVICE_NAME="${SERVICE_NAME:-doc-processor}"
export BQ_DATASET="${BQ_DATASET:-doc_metadata}"

# ── Validate required env vars ──────────────────────────────────────
for var in PROJECT_ID REGION SERVICE_NAME BUCKET BQ_DATASET; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: Environment variable $var is not set."
    echo "Usage:"
    echo "  export PROJECT_ID=236909908642"
    echo "  export REGION=europe-west4"
    echo "  export SERVICE_NAME=doc-processor"
    echo "  export BUCKET=treasury_comm_agent"
    echo "  export BQ_DATASET=doc_metadata"
    echo "  ./deploy.sh"
    exit 1
  fi
done

IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "=============================================="
echo " Building Docker image: ${IMAGE}"
echo "=============================================="
gcloud builds submit --project "${PROJECT_ID}" --tag "${IMAGE}" .

echo ""
echo "=============================================="
echo " Deploying to Cloud Run: ${SERVICE_NAME}"
echo " Region: ${REGION}"
echo "=============================================="
gcloud run deploy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "BUCKET=${BUCKET},BQ_DATASET=${BQ_DATASET}"

echo ""
echo "=============================================="
echo " Deployment complete!"
echo "=============================================="

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format='value(status.url)')

echo " Service URL: ${SERVICE_URL}"
echo ""
echo " Next steps:"
echo "   1) Create a Pub/Sub push subscription pointing to ${SERVICE_URL}"
echo "   2) Upload a file to gs://${BUCKET}/ and check BigQuery."
