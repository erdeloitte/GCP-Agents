# Serverless Document Processing Pipeline

A serverless, event-driven document processing pipeline on Google Cloud Platform.

## Architecture

```
┌──────────┐     ┌─────────────┐     ┌───────────┐     ┌──────────┐
│  User     │────▶│ Cloud       │────▶│ Pub/Sub   │────▶│ Cloud    │────▶ BigQuery
│  uploads  │     │ Storage     │     │ (push)    │     │ Run      │     (metadata)
│  file     │     │ bucket      │     │           │     │ service  │
└──────────┘     └─────────────┘     └───────────┘     └──────────┘
```

| Component | GCP Service | Purpose |
|-----------|-------------|---------|
| Ingestion | Cloud Storage | Receives uploaded documents |
| Trigger | Pub/Sub (push) | Sends `OBJECT_FINALIZE` events to Cloud Run |
| Processor | Cloud Run (Python) | Simulated OCR + metadata extraction |
| Storage | BigQuery | Stores extracted metadata for analytics |

**Region:** `europe-west1`

---

## Project Structure

```
google-cloud-serverless-app/
├── main.py                  # Flask app – Cloud Run entry point
├── cloud_storage_helper.py  # GCS download/upload utilities
├── bigquery_helper.py       # BigQuery insert helper
├── ocr_simulator.py         # Simulated OCR (placeholder)
├── requirements.txt         # Python dependencies
├── Dockerfile               # Container definition
├── deploy.sh                # Build & deploy script
├── test_local.sh            # Run locally for development
├── test_cloud.sh            # End-to-end integration test
└── README.md                # This file
```

---

## Prerequisites

1. A Google Cloud project with **billing enabled**.
2. The **Google Cloud SDK** (`gcloud`, `gsutil`, `bq`) installed and authenticated, or use **Google Cloud Shell** (https://shell.cloud.google.com).
3. The following APIs enabled on your project:
   - Cloud Storage
   - Pub/Sub
   - Cloud Run
   - BigQuery

Enable them in one command:

```bash
gcloud services enable \
  storage.googleapis.com \
  pubsub.googleapis.com \
  run.googleapis.com \
  bigquery.googleapis.com
```

---

## Quick Start

### 1. Set Environment Variables

```bash
export PROJECT_ID=236909908642       # Your GCP project ID
export REGION=europe-west1
export SERVICE_NAME=doc-processor
export BUCKET=doc-ingest-202607      # Choose a globally unique name
export BQ_DATASET=doc_metadata
```

### 2. Create GCP Resources

```bash
# Cloud Storage bucket
gcloud storage buckets create gs://${BUCKET} --location=${REGION}

# Pub/Sub topic
gcloud pubsub topics create doc-ingest-topic

# BigQuery dataset and table
bq mk ${BQ_DATASET}
bq mk -t ${BQ_DATASET}.documents \
    filename:STRING,upload_date:TIMESTAMP,tags:STRING,word_count:INT64
```

### 3. Deploy the Pipeline

```bash
chmod +x deploy.sh
./deploy.sh
```

The script will:
- Build a Docker image via Cloud Build
- Push it to Container Registry
- Deploy the Cloud Run service
- Print the service URL

### 4. Create the Pub/Sub Push Subscription

After `deploy.sh` prints the service URL, wire Pub/Sub to it:

```bash
CLOUD_RUN_URL=$(gcloud run services describe ${SERVICE_NAME} \
    --region ${REGION} --format='value(status.url)')

gcloud pubsub subscriptions create doc-ingest-sub \
    --topic=doc-ingest-topic \
    --push-endpoint="${CLOUD_RUN_URL}" \
    --ack-deadline=600
```

### 5. Run the End-to-End Test

```bash
chmod +x test_cloud.sh
./test_cloud.sh
```

The script uploads a test file, waits 15 seconds for processing, then queries BigQuery. If you see a row for your test file, the pipeline is working.

---

## Local Development

To run the service locally (requires Python 3.11+ and GCP credentials):

```bash
# Authenticate for local development
gcloud auth application-default login

chmod +x test_local.sh
./test_local.sh
```

The app will start on `http://localhost:8080`. You can send a simulated Pub/Sub message with curl:

```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "data": "'$(echo -n '{"name":"sample.txt"}' | base64)'"
    }
  }'
```

---

## Extending the OCR

The current OCR implementation in `ocr_simulator.py` is a placeholder that simply decodes file bytes to text. To use real OCR:

1. Install an OCR library (e.g., `pytesseract`).
2. Add system dependencies to the `Dockerfile` (e.g., `tesseract-ocr`).
3. Update the `simulate_ocr()` function in `ocr_simulator.py`.
4. Re-deploy: `./deploy.sh`

---

## Clean Up

To remove all resources created by this pipeline and stop incurring charges:

```bash
# Delete the Cloud Run service
gcloud run services delete ${SERVICE_NAME} --region ${REGION} --quiet

# Delete the Pub/Sub subscription and topic
gcloud pubsub subscriptions delete doc-ingest-sub --quiet
gcloud pubsub topics delete doc-ingest-topic --quiet

# Delete the Cloud Storage bucket (and all objects inside it)
gsutil rm -r gs://${BUCKET}

# Delete the BigQuery dataset (and all tables inside it)
bq rm -r -f ${BQ_DATASET}

# (Optional) Delete the container image
gcloud container images delete gcr.io/${PROJECT_ID}/${SERVICE_NAME} --quiet
```

---

## Troubleshooting

| Symptom | What to check |
|---------|---------------|
| `deploy.sh` fails at `gcloud builds submit` | Make sure Cloud Build API is enabled: `gcloud services enable cloudbuild.googleapis.com` |
| Cloud Run returns 500 | Check logs: `gcloud logging read "resource.type=cloud_run_revision" --limit 20` |
| No rows in BigQuery after upload | Verify the Pub/Sub subscription exists and points to the correct Cloud Run URL |
| `BUCKET` or `BQ_DATASET` not set errors | Re-export the environment variables (they don't persist across shell sessions) |
