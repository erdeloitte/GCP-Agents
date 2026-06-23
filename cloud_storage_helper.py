"""cloud_storage_helper.py
Utility functions for interacting with Google Cloud Storage.
"""
import os
import mimetypes
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

def upload_blob(blob_name: str, data: bytes, content_type: str | None = None) -> None:
    """Upload raw bytes to the bucket under `blob_name`.

    Args:
        blob_name:    Object name / path within the bucket.
        data:         File bytes to upload.
        content_type: Optional MIME type.  If omitted, inferred from blob_name.
    """
    if content_type is None:
        content_type, _ = mimetypes.guess_type(blob_name)
        content_type = content_type or "application/octet-stream"
    bucket = get_bucket()
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type=content_type)
    # Do NOT return public_url — the bucket is private; callers should use
    # signed URLs or the GCS console for access.

def download_blob(blob_name: str) -> bytes:
    """Download the contents of `blob_name` as bytes."""
    bucket = get_bucket()
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()
