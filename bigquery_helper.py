"""bigquery_helper.py
Helper functions for inserting document metadata into BigQuery.
"""
import os
from google.cloud import bigquery

DATASET = os.getenv('BQ_DATASET', 'doc_metadata')
TABLE = f"{DATASET}.documents"

_client = None


def get_bq_client() -> bigquery.Client:
    """Return a lazily-initialised BigQuery client."""
    global _client
    if _client is None:
        _client = bigquery.Client()
    return _client


def insert_metadata(metadata: dict) -> None:
    """Insert a single metadata dict into the BigQuery documents table.

    Args:
        metadata: Dict with keys matching the table schema
                  (filename, upload_date, tags, word_count).

    Raises:
        RuntimeError: If the BigQuery streaming insert returns errors.
    """
    client = get_bq_client()
    errors = client.insert_rows_json(TABLE, [metadata])
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")
