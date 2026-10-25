# AgriRisk — Agricultural Derivatives CTRM Prototype

> **PROTOTYPE — illustrative only.** All trades, prices, FX rates and limits are
> synthetic demo data. Valuation is deliberately simplified (options at intrinsic
> value only, no discounting, fixed FX) and is not suitable for live trading,
> financial reporting or regulatory use.

The dashboard service (`dashboard.py`) serves a prototype trade-capture,
monitoring and risk tool for agricultural derivatives at `/`. The older
counterparty analytics page (market / credit / liquidity agents) is still
available at `/counterparty-analytics`.

### Screens

Left navigation with a top bar for section, portfolio scope, data freshness,
AI status and the demo identity.

| Screen | What it shows |
|--------|---------------|
| Dashboard | Summary line, positions by commodity (long / short / net t, notional, MtM, valuation coverage, limit status), exposure by maturity, items requiring attention, market-input freshness, limit utilisation, upcoming maturities |
| Trade blotter / Capture | Sortable, filterable list; validated create / amend / cancel with a live trade ticket, reason and confirmation |
| Trade detail | Terms, valuation with formula, inputs and assumptions, exceptions, simulated broker confirmation, audit history, review actions |
| Monitoring | Monitoring items by category (maturities, missing data, market inputs, unmatched, valuation, reviews, limits) and a 60-day maturity calendar |
| Exposure & risk | Exposure by commodity / maturity / portfolio / counterparty, configured limits, and a derivation drawer listing contributing trades and marks |
| Exceptions | Acknowledge / override (Risk role, reason required, audited) and trade review |
| Audit trail | Every change with timestamp, demo user, before/after and reason, including AI requests |
| System & AI | Boundaries, AI provider status and connection test, data-handling policy, persistence statement |

The portfolio selector scopes the dashboard, blotter and monitoring; limits and
the Exposure & risk page are always firm-wide.

### Calculation and AI boundaries

- Valuation, exposure, limits and exception detection are plain, tested Python
  in `agri/` (`valuation.py`, `risk.py`, `monitoring.py`, `validation.py`).
  Missing or stale prices produce a flagged status, not a number. The
  valuation basis is shown next to every valuation figure.
- AI explanations (`/api/agri/ai/*`) are assistive only. The user first sees
  the exact payload (`/ai/preview`) and must confirm before anything is sent.
  The payload is an allow-list built in `agri/ai_payload.py`: IDs,
  counterparties, portfolios, brokers, execution references, notes, user names
  and free-text details are never sent. The AI cannot write anything; each
  request is audited with the provider used and the fields sent.
- Existing market/credit/liquidity agent outputs are tagged `"advisory": true`
  and do not feed limits or valuations.
- Identity is a selectable **demo user** (Demo Trader / Demo Risk Reviewer),
  used for role checks and maker-checker. It is not authentication; IAP still
  protects deployed services.

### LLM configuration (Claude only)

All LLM calls (agents and AI explanations) go through `llm_providers.py`,
which uses Anthropic Claude. Gemini support has been removed.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_ENABLED` | `true` | Turn all LLM features off without removing the key |
| `ANTHROPIC_API_KEY` | — | Required for AI features |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | |
| `LLM_TIMEOUT_SECONDS` | `30` | Per-request timeout |

Failures are reported as non-sensitive diagnostic codes with a remediation
hint (e.g. `INVALID_KEY`, `MODEL_NOT_FOUND`, `RATE_LIMITED`), on the System &
AI page and in the explanation dialog. Keys are never logged or displayed.
*System & AI → Test connection* sends a fixed prompt and no application data.

### Storage (BigQuery)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGRI_STORE` | `memory` | `bigquery` persists to BigQuery; anything else keeps data in memory |
| `BQ_DATASET` | `treasury_analytics` | Dataset holding the `agri_*` tables |
| `BQ_PROJECT` | client default | Optional project override |

With `AGRI_STORE=bigquery` the app creates and uses these tables in
`BQ_DATASET`: `agri_trades`, `agri_limits`, `agri_confirmations`,
`agri_exception_actions`, `agri_audit_events`, `agri_market_prices`,
`agri_meta`. They are **append-only**: each save adds a row, the latest row
per trade/limit is current, and *Reset demo data* starts a new `generation`
instead of deleting anything. To see current trades in the BigQuery console:

```sql
SELECT * FROM `treasury_analytics.agri_trades`
WHERE generation = (SELECT MAX(generation) FROM `treasury_analytics.agri_meta`)
QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY saved_at DESC) = 1
```

The app itself uses only streaming inserts and `tabledata.list`, so its
service account needs **BigQuery Data Editor on the dataset only** (no
project-level `bigquery.jobs.create`). State is loaded once at start-up and
written through, so run a **single instance** (`--max-instances=1`). If
BigQuery is unreachable at start-up the app falls back to memory and says
so in the UI; a failed write returns an error and changes nothing.
Market prices remain a synthetic feed: one snapshot per day is generated and
stored in `agri_market_prices`.

### Layout

```
agri/                 # deterministic engine + service layer (no Flask)
  models.py validation.py valuation.py risk.py monitoring.py
  reference.py demo_data.py repository.py bq_repository.py audit.py service.py ai_payload.py
agri_api.py           # Flask blueprint: /api/agri/*
llm_providers.py      # Claude calls and diagnostics
agri.html static/     # UI (vanilla JS/CSS, no build step)
tests/                # pytest suite
```

### Run locally

```bash
pip install -r requirements.txt
SKIP_IAP_CHECK=True python dashboard.py      # http://localhost:8081
```

(PowerShell: `$env:SKIP_IAP_CHECK="True"; python dashboard.py`.) Locally the
store defaults to memory; set `AGRI_STORE=bigquery` (with Application Default
Credentials) to use BigQuery. `ANTHROPIC_API_KEY` is optional; without it the
AI features report "not configured" and everything else works.

### Deploy the dashboard (Cloud Run)

`deploy.sh` rebuilds the whole platform, including the load balancer. To ship
only a new dashboard version:

```bash
gcloud builds submit --config cloudbuild.dash.yaml .
gcloud run deploy treasury-dashboard --region europe-west4   --image europe-west4-docker.pkg.dev/dttl-nl-genai-sandbox/treasury-repo/treasury-platform   --update-env-vars AGRI_STORE=bigquery --max-instances 1
```

### Run tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

---

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

**Region:** `europe-west4`

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

1. A Google Cloud project with **billing enabled** and `gcloud` configured: `gcloud config set project YOUR_PROJECT_ID`.
2. The **Google Cloud SDK** (`gcloud`, `gsutil`, `bq`) installed and authenticated, or use **Google Cloud Shell** (https://shell.cloud.google.com).
3. The following APIs enabled on your project:
   - Storage
   - Pub/Sub
   - Cloud Run
   - BigQuery
   - Cloud Build

Enable them in one command:

```bash
gcloud services enable \
  storage.googleapis.com \
  pubsub.googleapis.com \
  run.googleapis.com \
  bigquery.googleapis.com \
  cloudbuild.googleapis.com
```

---

## Quick Start

### 1. Set Environment Variables

```bash
export PROJECT_ID=$(gcloud config get-value project)
export REGION=europe-west4
export SERVICE_NAME=doc-processor
export BUCKET=treasury_comm_agent
export BQ_DATASET=doc_metadata
```

### 2. Create GCP Resources

```bash
# Cloud Storage bucket
gcloud storage buckets create gs://${BUCKET:?Please run 'export BUCKET=your-unique-name' first} --location=${REGION}

# Pub/Sub topic
gcloud pubsub topics create doc-ingest-topic

# BigQuery dataset and table
bq --project_id=${PROJECT_ID} mk --location=${REGION} ${BQ_DATASET}
bq --project_id=${PROJECT_ID} mk -t ${BQ_DATASET}.documents \
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

## Cloud Run Authentication Notes

There are two different access controls involved:

1. Cloud Run service access
   - Cloud Run can be public (`--allow-unauthenticated`) or private.
   - For full IAP deployment, services must be private and use Cloud Load Balancing.
   - This repo now supports `LB_DOMAIN` and `IAP_USER` for IAP-secured access.

2. App-level IAP validation
   - This app checks for the `X-Goog-IAP-JWT-Assertion` header if `SKIP_IAP_CHECK` is not set.
   - If you access the Cloud Run URL directly without going through IAP, that header will be missing and the app will reject the request.

So if browser access fails with Google auth, the likely causes are:
- Cloud Run is private and the user is not authorized as an invoker;
- The service is behind IAP but the request is not going through the IAP-secured endpoint;
- The code is expecting the IAP header even though the access URL is direct.

For full IAP deployment, set:
```bash
export LB_DOMAIN=your.domain.example
export IAP_USER=eruizduarte@deloitte.nl
```
Then deploy with `./deploy.sh` and access the app through `https://${LB_DOMAIN}`.

If you want direct browser access without IAP, deploy with `--allow-unauthenticated` and set `SKIP_IAP_CHECK=True`.
