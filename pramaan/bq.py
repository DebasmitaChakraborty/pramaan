import os
from google.cloud import bigquery

BYTES_PER_GIB = 1073741824  # 1 GiB ceiling limit

def get_bq_client(project_id: str | None = None) -> bigquery.Client:
    project = project_id or os.getenv("GCP_PROJECT_ID")
    return bigquery.Client(project=project)

def execute_query(
    query: str, 
    client: bigquery.Client | None = None, 
    max_bytes_billed: int = BYTES_PER_GIB,
    job_params: list | None = None
) -> bigquery.table.RowIterator:
    """Single choke point for all BigQuery queries enforcing byte ceiling."""
    client = client or get_bq_client()
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=max_bytes_billed,
        query_parameters=job_params or []
    )
    query_job = client.query(query, job_config=job_config)
    return query_job.result()
