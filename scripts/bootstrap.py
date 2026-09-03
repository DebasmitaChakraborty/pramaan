import os
import sys
from google.cloud import bigquery

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pramaan.bq import get_bq_client

DEMO_DATASET = os.getenv("DEMO_DATASET", "pramaan_demo")

TABLE_QUERIES = {
    "orders": """
        CREATE OR REPLACE TABLE `{dataset}.orders`
        PARTITION BY DATE(created_at) AS
        SELECT * FROM `bigquery-public-data.thelook_ecommerce.orders`
        WHERE created_at >= '2023-01-01';
    """,
    "users": """
        CREATE OR REPLACE TABLE `{dataset}.users` AS
        SELECT * FROM `bigquery-public-data.thelook_ecommerce.users`
        WHERE id IN (SELECT DISTINCT user_id FROM `{dataset}.orders`);
    """,
    "products": """
        CREATE OR REPLACE TABLE `{dataset}.products` AS
        SELECT * FROM `bigquery-public-data.thelook_ecommerce.products`;
    """,
    "order_items": """
        CREATE OR REPLACE TABLE `{dataset}.order_items`
        PARTITION BY DATE(created_at) AS
        SELECT * FROM `bigquery-public-data.thelook_ecommerce.order_items`
        WHERE order_id IN (SELECT order_id FROM `{dataset}.orders`);
    """
}

def bootstrap(project_id: str):
    client = get_bq_client(project_id)
    dataset_ref = bigquery.DatasetReference(project_id, DEMO_DATASET)
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = "US"
    client.create_dataset(dataset, exists_ok=True)
    print(f"Dataset {DEMO_DATASET} ready.")

    for table_name, query_template in TABLE_QUERIES.items():
        query = query_template.format(dataset=f"{project_id}.{DEMO_DATASET}")
        print(f"Bootstrapping {table_name}...")
        job = client.query(query)
        job.result()
        print(f"Table {table_name} created successfully.")

def teardown(project_id: str):
    client = get_bq_client(project_id)
    dataset_id = f"{project_id}.{DEMO_DATASET}"
    client.delete_dataset(dataset_id, delete_contents=True, not_found_ok=True)
    print(f"Dataset {dataset_id} deleted successfully.")

if __name__ == "__main__":
    project = os.getenv("GCP_PROJECT_ID")
    if not project:
        raise ValueError("GCP_PROJECT_ID environment variable not set.")
    
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        bootstrap(project)
    elif cmd == "teardown":
        teardown(project)
    else:
        print("Usage: python bootstrap.py [build|teardown]")
