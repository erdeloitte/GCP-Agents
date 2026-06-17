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


def get_documents(tag_filter=None):
    """Fetch document metadata from BigQuery, optionally filtered by tag.

    Args:
        tag_filter: A string to filter the 'tags' column.

    Returns:
        A list of dictionaries representing the rows.
    """
    client = get_bq_client()
    query = f"SELECT filename, upload_date, tags, word_count FROM `{TABLE}`"
    query_params = []

    if tag_filter:
        query += " WHERE tags LIKE @tag"
        query_params.append(bigquery.ScalarQueryParameter("tag", "STRING", f"%{tag_filter}%"))

    query += " ORDER BY upload_date DESC"
    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    query_job = client.query(query, job_config=job_config)
    return [dict(row) for row in query_job]
