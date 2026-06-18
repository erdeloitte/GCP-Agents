"""cloud_storage_helper.py
Utility functions for interacting with Google Cloud Storage.
"""
import os
from google.cloud import storage

_storage_client = None

def _get_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client

def get_bucket():
    bucket_name = os.getenv('BUCKET')
    if not bucket_name:
        raise RuntimeError('BUCKET environment variable not set')
    return _get_client().bucket(bucket_name)

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
