import os
import json
import base64
from datetime import datetime
from flask import Flask, request
from google.cloud import storage, bigquery

app = Flask(__name__)

BUCKET_NAME = os.getenv('BUCKET')
BQ_DATASET = os.getenv('BQ_DATASET')
BQ_TABLE = f"{BQ_DATASET}.documents"

storage_client = storage.Client()
bq_client = bigquery.Client()

def simulate_ocr(content: bytes) -> str:
    """Placeholder OCR – simply decode bytes to string.
    Replace with real OCR (e.g., pytesseract) for production.
    """
    return content.decode(errors='ignore')

def extract_metadata(filename: str, text: str) -> dict:
    words = text.split()
    word_count = len(words)
    tags = list({w.lower() for w in words if len(w) > 6})
    return {
        "filename": filename,
        "upload_date": datetime.utcnow().isoformat(),
        "tags": tags,
        "word_count": word_count,
    }

@app.route('/', methods=['POST'])
def handler():
    envelope = request.get_json()
    if not envelope:
        return 'No Pub/Sub message received', 400
    message = envelope.get('message')
    if not message:
        return 'Invalid Pub/Sub format', 400
    data = message.get('data')
    if not data:
        return 'No data in Pub/Sub message', 400
    try:
        obj_info = json.loads(base64.b64decode(data).decode())
    except Exception as e:
        return f'Error decoding message: {e}', 400
    filename = obj_info.get('name')
    if not filename:
        return 'No object name in message', 400
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    content = blob.download_as_bytes()
    text = simulate_ocr(content)
    metadata = extract_metadata(filename, text)
    errors = bq_client.insert_rows_json(BQ_TABLE, [metadata])
    if errors:
        return f'BigQuery insert errors: {errors}', 500
    return '', 204

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
