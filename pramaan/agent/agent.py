import logging
from typing import Dict, Any

from pramaan.config import get_genai_client

logger = logging.getLogger(__name__)

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

    client = get_genai_client()
    prompt = f"""
    You are a data quality diagnostician. A data quality rule was violated:
    - Dataset: {dataset}
    - Table: {table}
    - Rule ID: {rule_id}
    - Rule type: {rule_type}
    - Observed metric value: {metric_value}

    In 2-3 sentences, give a plausible root-cause hypothesis for this violation
    and a suggested next investigative step.
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    return {
        "status": "diagnosed",
        "violation": violation_data,
        "summary": response.text,
    }
