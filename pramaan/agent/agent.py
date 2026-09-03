import json
import logging
from typing import Dict, Any

from google.genai import types

from pramaan.bq import get_partition_info, get_recent_jobs_for_table, get_table_columns
from pramaan.config import get_genai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a data quality diagnostician. Reason ONLY from the evidence
provided below -- never speculate beyond it or invent details it doesn't contain.

When the evidence points to a specific cause, name the specific job_id, partition_id,
or column that is responsible.

If the evidence's recent_jobs list is empty and nothing in partitions or columns
shows a metadata change in the window, you MUST set status to "insufficient_evidence"
and say so in the summary -- do not guess a cause anyway.

Respond with a JSON object of the shape {"status": "diagnosed" | "insufficient_evidence", "summary": "..."}.
"""

def _gather_evidence(dataset: str, table: str) -> Dict[str, Any]:
    return {
        "partitions": get_partition_info(dataset, table),
        "recent_jobs": get_recent_jobs_for_table(dataset, table, hours=3),
        "columns": get_table_columns(dataset, table),
    }

def diagnose_violation(
    dataset: str,
    table: str,
    rule_id: str,
    rule_type: str,
    metric_value: float,
) -> Dict[str, Any]:
    """
    Diagnoses data quality violations by inspecting metrics and BigQuery context.
    """
    violation_data = {
        "dataset": dataset,
        "table": table,
        "rule_id": rule_id,
        "rule_type": rule_type,
        "metric_value": metric_value,
    }
    logger.info(f"Diagnosing violation: {violation_data}")

    evidence = _gather_evidence(dataset, table)

    client = get_genai_client()
    prompt = f"""
    {SYSTEM_PROMPT}

    Violation:
    {json.dumps(violation_data, indent=2, default=str)}

    Evidence:
    {json.dumps(evidence, indent=2, default=str)}
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    try:
        result = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        result = {"status": "diagnosed", "summary": response.text}

    return {
        "status": result.get("status", "diagnosed"),
        "violation": violation_data,
        "evidence": evidence,
        "summary": result.get("summary", response.text),
    }
