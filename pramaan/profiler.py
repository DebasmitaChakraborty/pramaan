import json
import os
from typing import List, Dict, Any
from pydantic import BaseModel
from google.genai import types

from pramaan.bq import execute_query
from pramaan.config import get_genai_client
from pramaan.rules import RuleUnion

class DraftContract(BaseModel):
    dataset: str
    table: str
    version: int = 1
    rules: List[RuleUnion]

def fetch_table_schema_metadata(dataset: str, table: str) -> List[Dict[str, Any]]:
    """Retrieves field path details including nested STRUCT and ARRAY fields."""
    query = f"""
    SELECT 
        field_path,
        data_type,
        description
    FROM `{dataset}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
    WHERE table_name = '{table}'
    """
    rows = execute_query(query)
    return [dict(row) for row in rows]

def generate_draft_contract(dataset: str, table: str) -> DraftContract:
    """Uses Gemini 2.5 Flash to profile table schema metadata and propose a data contract."""
    schema_info = fetch_table_schema_metadata(dataset, table)

    client = get_genai_client()
    
    prompt = f"""
    You are an automated data profiling engine. Analyze the following BigQuery table schema metadata for table `{dataset}.{table}`:
    {json.dumps(schema_info, indent=2)}

    Propose a set of data quality rules for this table. Return ONLY a valid JSON object matching this strict schema:
    {{
      "dataset": "{dataset}",
      "table": "{table}",
      "version": 1,
      "rules": [
        {{
          "rule_id": "rule_1",
          "rule_type": "null_rate",
          "column": "column_name",
          "max_null_rate": 0.05
        }}
      ]
    }}

    Supported rule_types and required fields:
    - null_rate: column, max_null_rate (float 0.0 to 1.0)
    - uniqueness: column, max_duplicate_rate (float 0.0 to 1.0)
    - freshness: column, max_lag_hours (float)
    - row_count_drift: column, min_row_count (int)

    Return ONLY the JSON. No markdown code blocks, no preamble, no commentary.
    """
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )
    )

    try:
        raw_json = json.loads(response.text)
        draft = DraftContract.model_validate(raw_json)
        return draft
    except Exception as e:
        raise ValueError(f"Profiler failed to produce a valid typed contract. Error: {e}\nRaw Output: {response.text}")
