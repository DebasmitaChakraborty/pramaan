import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

from pramaan.sentry import execute_sweep, SweepResult
from pramaan.agent.agent import diagnose_violation

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
