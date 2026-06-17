#!/usr/bin/env bash
# deploy.sh – Build, push, and deploy the Cloud Run service.
#
# Required environment variables (export them before running):
#   PROJECT_ID   – GCP project number or ID  (e.g. 236909908642)
#   REGION       – Target region              (e.g. europe-west1)
#   SERVICE_NAME – Cloud Run service name     (e.g. doc-processor)
#   BUCKET       – Cloud Storage bucket name  (e.g. doc-ingest-202607)
#   BQ_DATASET   – BigQuery dataset name      (e.g. doc_metadata)

set -euo pipefail

# ── Validate required env vars ──────────────────────────────────────
for var in PROJECT_ID REGION SERVICE_NAME BUCKET BQ_DATASET; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: Environment variable $var is not set."
    echo "Usage:"
    echo "  export PROJECT_ID=236909908642"
    echo "  export REGION=europe-west1"
    echo "  export SERVICE_NAME=doc-processor"
    echo "  export BUCKET=doc-ingest-202607"
    echo "  export BQ_DATASET=doc_metadata"
    echo "  ./deploy.sh"
    exit 1
  fi
done

IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "=============================================="
echo " Building Docker image: ${IMAGE}"
echo "=============================================="
gcloud builds submit --tag "${IMAGE}" .

echo ""
echo "=============================================="
echo " Deploying to Cloud Run: ${SERVICE_NAME}"
echo " Region: ${REGION}"
echo "=============================================="
gcloud run deploy "${SERVICE_NAME}" \
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
  --region "${REGION}" \
  --format='value(status.url)')

echo " Service URL: ${SERVICE_URL}"
echo ""
echo " Next steps:"
echo "   1) Create a Pub/Sub push subscription pointing to ${SERVICE_URL}"
echo "   2) Upload a file to gs://${BUCKET}/ and check BigQuery."
