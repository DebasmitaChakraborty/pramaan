import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List
from pramaan.bq import execute_query

GROUND_TRUTH_LOG = os.getenv("GROUND_TRUTH_LOG", ".ground_truth.json")

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

def inject_null_flood(dataset: str, table: str = "users", column: str = "email"):
    """Scenario 1: Injects NULLs to trigger null_rate rule."""
    log_ground_truth("null_flood", table, column, "null_rate")
    query = f"""
    UPDATE `{dataset}.{table}`
    SET {column} = NULL
    WHERE RAND() < 0.25;
    """
    execute_query(query)

def inject_silent_duplicate_load(dataset: str, table: str = "orders", column: str = "order_id"):
    """Scenario 2: Duplicates primary keys to trigger uniqueness rule."""
    log_ground_truth("silent_duplicate_load", table, column, "uniqueness")
    query = f"""
    INSERT INTO `{dataset}.{table}`
    SELECT * FROM `{dataset}.{table}` LIMIT 100;
    """
    execute_query(query)

def inject_referential_orphan(dataset: str, table: str = "order_items", column: str = "user_id"):
    """Scenario 5: Injects orphan IDs to trigger referential_integrity rule."""
    log_ground_truth("referential_orphan", table, column, "referential_integrity")
    query = f"""
    UPDATE `{dataset}.{table}`
    SET {column} = 99999999
    WHERE RAND() < 0.10;
    """
    execute_query(query)

def get_ground_truth_logs() -> List[Dict[str, Any]]:
    if not os.path.exists(GROUND_TRUTH_LOG):
        return []
    with open(GROUND_TRUTH_LOG, "r") as f:
        return json.load(f)
