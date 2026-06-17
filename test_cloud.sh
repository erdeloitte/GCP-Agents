#!/usr/bin/env bash
# test_cloud.sh – End-to-end integration test for the deployed pipeline.
#
# What it does:
#   1. Creates a small test file.
#   2. Uploads it to the Cloud Storage ingestion bucket.
#   3. Waits for the Cloud Run service to process the Pub/Sub message.
#   4. Queries BigQuery to confirm metadata was inserted.
#
# Required environment variables (export them before running):
#   PROJECT_ID   – GCP project number or ID  (e.g. 236909908642)
#   BUCKET       – Cloud Storage bucket name  (e.g. doc-ingest-202607)
#   BQ_DATASET   – BigQuery dataset name      (e.g. doc_metadata)

set -euo pipefail

# ── Validate required env vars ──────────────────────────────────────
for var in PROJECT_ID BUCKET BQ_DATASET; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: Environment variable $var is not set."
    exit 1
  fi
done

TEST_FILE="/tmp/test_doc_$(date +%s).txt"
TEST_BLOB="test_doc_$(date +%s).txt"

echo "=============================================="
echo " Creating test file: ${TEST_FILE}"
echo "=============================================="
cat > "${TEST_FILE}" <<EOF
This is a sample document for testing the serverless
document processing pipeline. It contains enough words
to generate meaningful metadata including tags and a
realistic word count for verification purposes.
EOF

echo " Contents:"
cat "${TEST_FILE}"
echo ""

echo "=============================================="
echo " Uploading to gs://${BUCKET}/${TEST_BLOB}"
echo "=============================================="
gsutil cp "${TEST_FILE}" "gs://${BUCKET}/${TEST_BLOB}"

WAIT_SECS=15
echo ""
echo "=============================================="
echo " Waiting ${WAIT_SECS}s for Cloud Run to process…"
echo "=============================================="
sleep "${WAIT_SECS}"

echo ""
echo "=============================================="
echo " Querying BigQuery for results"
echo "=============================================="
bq query --use_legacy_sql=false \
  "SELECT filename, upload_date, tags, word_count
   FROM \`${PROJECT_ID}.${BQ_DATASET}.documents\`
   WHERE filename = '${TEST_BLOB}'
   ORDER BY upload_date DESC
   LIMIT 5;"

echo ""
echo "=============================================="
echo " Test complete!"
echo "=============================================="
echo " If you see a row above, the pipeline is working end-to-end."
echo " If no rows appear, check Cloud Run logs:"
echo "   gcloud logging read 'resource.type=cloud_run_revision' --limit 20"
