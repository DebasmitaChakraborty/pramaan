import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List

from google.api_core.exceptions import NotFound

from pramaan.bq import execute_query, get_bq_client

GROUND_TRUTH_LOG = os.getenv("GROUND_TRUTH_LOG", ".ground_truth.json")
SNAPSHOT_PREFIX = "_snap_"

def log_ground_truth(scenario: str, table: str, target_column: str, expected_rule_type: str):
    """Logs ground truth entry BEFORE fault injection (Design Invariant #4)."""
    records = []
    if os.path.exists(GROUND_TRUTH_LOG):
        with open(GROUND_TRUTH_LOG, "r") as f:
            records = json.load(f)

    entry = {
        "fault_id": f"fault_{scenario}_{int(datetime.now(timezone.utc).timestamp())}",
        "scenario": scenario,
        "table": table,
        "target_column": target_column,
        "expected_rule_type": expected_rule_type,
        "injected_at": datetime.now(timezone.utc).isoformat()
    }
    records.append(entry)

    with open(GROUND_TRUTH_LOG, "w") as f:
        json.dump(records, f, indent=2)
    return entry

def _snapshot_table_id(table: str) -> str:
    return f"{SNAPSHOT_PREFIX}{table}"

def _snapshot_exists(dataset: str, table: str) -> bool:
    client = get_bq_client()
    try:
        client.get_table(f"{client.project}.{dataset}.{_snapshot_table_id(table)}")
        return True
    except NotFound:
        return False

def snapshot(dataset: str, table: str) -> str:
    """Copies `dataset.table` to `dataset._snap_<table>` as a pre-fault baseline."""
    snap_table = _snapshot_table_id(table)
    execute_query(f"""
    CREATE OR REPLACE TABLE `{dataset}.{snap_table}`
    COPY `{dataset}.{table}`;
    """)
    return snap_table

def restore(dataset: str, table: str) -> None:
    """Copies `dataset._snap_<table>` back over `dataset.table` and drops the snapshot."""
    snap_table = _snapshot_table_id(table)
    if not _snapshot_exists(dataset, table):
        raise FileNotFoundError(
            f"No snapshot found for {dataset}.{table} (expected {dataset}.{snap_table})"
        )
    execute_query(f"""
    CREATE OR REPLACE TABLE `{dataset}.{table}`
    COPY `{dataset}.{snap_table}`;
    """)
    execute_query(f"DROP TABLE `{dataset}.{snap_table}`;")

def _ensure_snapshot(dataset: str, table: str) -> None:
    """Snapshots `dataset.table` unless a snapshot already exists (first fault wins,
    so `restore` always gets you back to the pre-chaos baseline, not the last fault)."""
    if not _snapshot_exists(dataset, table):
        snapshot(dataset, table)

def inject_null_flood(dataset: str, table: str = "users", column: str = "email"):
    """Scenario 1: Injects NULLs to trigger null_rate rule."""
    _ensure_snapshot(dataset, table)
    log_ground_truth("null_flood", table, column, "null_rate")
    query = f"""
    UPDATE `{dataset}.{table}`
    SET {column} = NULL
    WHERE RAND() < 0.25;
    """
    execute_query(query)

def inject_silent_duplicate_load(dataset: str, table: str = "orders", column: str = "order_id"):
    """Scenario 2: Duplicates primary keys to trigger uniqueness rule."""
    _ensure_snapshot(dataset, table)
    log_ground_truth("silent_duplicate_load", table, column, "uniqueness")
    query = f"""
    INSERT INTO `{dataset}.{table}`
    SELECT * FROM `{dataset}.{table}` LIMIT 100;
    """
    execute_query(query)

def inject_currency_swap(dataset: str, table: str = "order_items", column: str = "sale_price"):
    """Scenario 3: Multiplies a monetary column by a conversion-like factor to simulate
    a currency unit mismatch. This schema has no explicit currency column, so this
    corrupts the numeric magnitude of the price column instead -- the closest available
    stand-in for a currency_swap fault."""
    _ensure_snapshot(dataset, table)
    log_ground_truth("currency_swap", table, column, "range")
    query = f"""
    UPDATE `{dataset}.{table}`
    SET {column} = {column} * 83.0
    WHERE RAND() < 0.15;
    """
    execute_query(query)

def inject_stalled_partition(dataset: str, table: str = "orders", column: str = "created_at"):
    """Scenario 4: Shifts the most recent days' timestamps backward to simulate a
    pipeline that stopped landing new partitions, triggering a freshness rule."""
    _ensure_snapshot(dataset, table)
    log_ground_truth("stalled_partition", table, column, "freshness")
    query = f"""
    UPDATE `{dataset}.{table}`
    SET {column} = TIMESTAMP_SUB({column}, INTERVAL 30 DAY)
    WHERE DATE({column}) >= DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY);
    """
    execute_query(query)

def inject_referential_orphan(dataset: str, table: str = "order_items", column: str = "user_id"):
    """Scenario 5: Injects orphan IDs to trigger referential_integrity rule."""
    _ensure_snapshot(dataset, table)
    log_ground_truth("referential_orphan", table, column, "referential_integrity")
    query = f"""
    UPDATE `{dataset}.{table}`
    SET {column} = 99999999
    WHERE RAND() < 0.10;
    """
    execute_query(query)

def inject_row_count_collapse(dataset: str, table: str = "orders", column: str = "order_id"):
    """Scenario 6: Deletes most rows to simulate an upstream load collapsing,
    triggering row_count_drift."""
    _ensure_snapshot(dataset, table)
    log_ground_truth("row_count_collapse", table, column, "row_count_drift")
    query = f"""
    DELETE FROM `{dataset}.{table}`
    WHERE RAND() < 0.9;
    """
    execute_query(query)

def inject_schema_drift(dataset: str, table: str = "order_items", column: str = "status"):
    """Scenario 7: Drops a column to simulate an upstream schema change. Caught by a
    schema_conformance rule (see rules.py/sentry.py), which checks the contract's
    expected column list against bq.get_table_columns rather than a SELECT."""
    _ensure_snapshot(dataset, table)
    log_ground_truth("schema_drift", table, column, "schema_conformance")
    query = f"""
    ALTER TABLE `{dataset}.{table}`
    DROP COLUMN {column};
    """
    execute_query(query)

def get_ground_truth_logs() -> List[Dict[str, Any]]:
    if not os.path.exists(GROUND_TRUTH_LOG):
        return []
    with open(GROUND_TRUTH_LOG, "r") as f:
        return json.load(f)
