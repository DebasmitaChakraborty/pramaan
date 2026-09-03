import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

from pramaan.contracts import list_active_contract_tables, load_active_contract
from pramaan.sentry import execute_sweep, SweepResult
from pramaan.agent.agent import diagnose_violation

DEMO_DATASET = os.getenv("DEMO_DATASET", "pramaan_demo")

app = FastAPI(title="Pramaan Agentic Data Trust API", version="0.1.0")

class SweepRequest(BaseModel):
    dataset: str
    table: str

class DiagnosisRequest(BaseModel):
    dataset: str
    table: str
    rule_id: str
    rule_type: str
    metric_value: float

@app.get("/health")
def health_check():
    return {"status": "healthy", "project": os.getenv("GCP_PROJECT_ID", "pramaan-506517")}

@app.get("/contracts")
def list_contracts(dataset: str = DEMO_DATASET):
    tables = list_active_contract_tables(dataset)
    contracts = []
    for table in tables:
        c = load_active_contract(dataset, table)
        contracts.append({
            "table": table,
            "version": c.version,
            "approved_by": c.approved_by,
            "approved_at": c.approved_at,
            "rule_count": len(c.rules),
            "rule_types": sorted({r["rule_type"] for r in c.rules}),
        })
    return {"dataset": dataset, "contracts": contracts}

@app.post("/sweep", response_model=List[SweepResult])
def run_sweep(req: SweepRequest):
    try:
        results = execute_sweep(req.dataset, req.table)
        return results
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sweep execution failed: {str(e)}")

@app.post("/diagnose")
def run_diagnosis(req: DiagnosisRequest):
    try:
        note = diagnose_violation(
            dataset=req.dataset,
            table=req.table,
            rule_id=req.rule_id,
            rule_type=req.rule_type,
            metric_value=req.metric_value
        )
        return {"root_cause_note": note}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Diagnosis failed: {str(e)}")
