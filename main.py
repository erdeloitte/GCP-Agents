import os
import json
import base64
from datetime import datetime, timezone
from flask import Flask, request

# Use project-specific helper modules for modularity and deduplication
from ocr_simulator import simulate_ocr
from cloud_storage_helper import download_blob
from bigquery_helper import insert_metadata

app = Flask(__name__)

def extract_metadata(filename: str, text: str) -> dict:
    words = text.split()
    word_count = len(words)
    # Deduplicate tags and join as a string to match BigQuery STRING schema
    tags_list = list({w.lower() for w in words if len(w) > 6})
    return {
        "filename": filename,
        "upload_date": datetime.now(timezone.utc).isoformat(),
        "tags": ",".join(tags_list),
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

    try:
        content = download_blob(filename)
        text = simulate_ocr(content)
        metadata = extract_metadata(filename, text)
        insert_metadata(metadata)
    except Exception as e:
        return f'Processing error: {e}', 500

    return '', 204

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
