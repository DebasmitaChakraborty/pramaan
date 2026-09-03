import json
import os
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel
from pramaan.profiler import DraftContract

CONTRACTS_DIR = os.getenv("CONTRACTS_DIR", ".contracts_store")

class ApprovedContract(BaseModel):
    dataset: str
    table: str
    version: int
    approved_by: str
    approved_at: str
    rules: List[dict]

def _get_contract_path(dataset: str, table: str, status: str = "draft", version: int = 1) -> str:
    os.makedirs(CONTRACTS_DIR, exist_ok=True)
    return os.path.join(CONTRACTS_DIR, f"{dataset}_{table}_{status}_v{version}.json")

def save_draft_contract(draft: DraftContract) -> str:
    """Saves a draft contract version to the store."""
    path = _get_contract_path(draft.dataset, draft.table, status="draft", version=draft.version)
    with open(path, "w") as f:
        f.write(draft.model_dump_json(indent=2))
    return path

def approve_contract(dataset: str, table: str, draft_version: int, approved_by: str) -> ApprovedContract:
    """Promotes a draft contract to an approved, immutable contract with audit logs."""
    draft_path = _get_contract_path(dataset, table, status="draft", version=draft_version)
    if not os.path.exists(draft_path):
        raise FileNotFoundError(f"No draft contract found for {dataset}.{table} v{draft_version}")

    with open(draft_path, "r") as f:
        draft_data = json.load(f)

    approved = ApprovedContract(
        dataset=dataset,
        table=table,
        version=draft_version,
        approved_by=approved_by,
        approved_at=datetime.now(timezone.utc).isoformat(),
        rules=draft_data["rules"]
    )

    approved_path = _get_contract_path(dataset, table, status="approved", version=draft_version)
    with open(approved_path, "w") as f:
        f.write(approved.model_dump_json(indent=2))
    
    return approved

def load_active_contract(dataset: str, table: str) -> ApprovedContract:
    """Loads the latest approved contract. Fails if only draft exists (Invariant #3)."""
    os.makedirs(CONTRACTS_DIR, exist_ok=True)
    approved_files = [
        f for f in os.listdir(CONTRACTS_DIR) 
        if f.startswith(f"{dataset}_{table}_approved_v") and f.endswith(".json")
    ]
    
    if not approved_files:
        raise PermissionError(f"Execution rejected: No approved contract found for {dataset}.{table}. Sweeps cannot execute draft contracts.")

    approved_files.sort(reverse=True)
    latest_path = os.path.join(CONTRACTS_DIR, approved_files[0])
    
    with open(latest_path, "r") as f:
        data = json.load(f)
    return ApprovedContract.model_validate(data)
