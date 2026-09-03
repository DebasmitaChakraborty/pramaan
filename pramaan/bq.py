import os
from typing import Any, Dict, List

from google.cloud import bigquery

BYTES_PER_GIB = 1073741824  # 1 GiB ceiling limit

def get_bq_client(project_id: str | None = None) -> bigquery.Client:
    project = project_id or os.getenv("GCP_PROJECT_ID")
    return bigquery.Client(project=project)

def execute_query_job(
    query: str,
    client: bigquery.Client | None = None,
    max_bytes_billed: int = BYTES_PER_GIB,
    job_params: list | None = None
) -> bigquery.QueryJob:
    """Same choke point as execute_query, but returns the completed job itself
    (not just its rows) for callers that need job metadata like `.ended`."""
    client = client or get_bq_client()
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=max_bytes_billed,
        query_parameters=job_params or []
    )
    query_job = client.query(query, job_config=job_config)
    query_job.result()
    return query_job

def execute_query(
    query: str,
    client: bigquery.Client | None = None,
    max_bytes_billed: int = BYTES_PER_GIB,
    job_params: list | None = None
) -> bigquery.table.RowIterator:
    """Single choke point for all BigQuery queries enforcing byte ceiling."""
    return execute_query_job(query, client, max_bytes_billed, job_params).result()

def get_partition_info(dataset: str, table: str) -> List[Dict[str, Any]]:
    """Partition metadata for `table` from INFORMATION_SCHEMA.PARTITIONS."""
    query = f"""
    SELECT partition_id, total_rows, last_modified_time
    FROM `{dataset}.INFORMATION_SCHEMA.PARTITIONS`
    WHERE table_name = '{table}'
      AND partition_id NOT IN ('__NULL__', '__UNPARTITIONED__')
    ORDER BY partition_id DESC
    LIMIT 20
    """
    rows = execute_query(query)
    return [dict(row) for row in rows]

def get_recent_jobs_for_table(dataset: str, table: str, hours: int = 3) -> List[Dict[str, Any]]:
    """Jobs from INFORMATION_SCHEMA.JOBS_BY_PROJECT that wrote to `table` in the
    last `hours` hours."""
    client = get_bq_client()
    query = f"""
    SELECT job_id, creation_time, user_email, statement_type, query
    FROM `{client.project}`.`region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
    WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {hours} HOUR)
      AND destination_table.dataset_id = '{dataset}'
      AND destination_table.table_id = '{table}'
    ORDER BY creation_time DESC
    LIMIT 50
    """
    rows = execute_query(query, client=client)
    return [dict(row) for row in rows]

def get_table_columns(dataset: str, table: str, return_job: bool = False):
    """Column name/type pairs for `table` from INFORMATION_SCHEMA.COLUMNS. With
    return_job=True, also returns the job's completion timestamp (for callers
    measuring detection latency)."""
    query = f"""
    SELECT column_name, data_type
    FROM `{dataset}.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = '{table}'
    ORDER BY ordinal_position
    """
    if return_job:
        job = execute_query_job(query)
        return [dict(row) for row in job.result()], job.ended
    rows = execute_query(query)
    return [dict(row) for row in rows]
