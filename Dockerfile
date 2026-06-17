FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .
COPY cloud_storage_helper.py .
COPY bigquery_helper.py .
COPY ocr_simulator.py .

# Expose the port Cloud Run will send traffic to
EXPOSE 8080

# Use gunicorn as the production WSGI server
# Cloud Run sets the PORT env var automatically
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
