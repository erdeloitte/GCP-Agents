"""cloud_storage_helper.py
Utility functions for interacting with Google Cloud Storage.
"""
import os
from google.cloud import storage

# The bucket name is passed via environment variable `BUCKET`
BUCKET_NAME = os.getenv('BUCKET')

_storage_client = storage.Client()

def get_bucket():
    """Return a Bucket object for the configured bucket name."""
    if not BUCKET_NAME:
        raise RuntimeError('BUCKET environment variable not set')
    return _storage_client.bucket(BUCKET_NAME)

def upload_blob(blob_name: str, data: bytes):
    """Upload raw bytes to the bucket under `blob_name`."""
    bucket = get_bucket()
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data)
    return blob.public_url

def download_blob(blob_name: str) -> bytes:
    """Download the contents of `blob_name` as bytes."""
    bucket = get_bucket()
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()
