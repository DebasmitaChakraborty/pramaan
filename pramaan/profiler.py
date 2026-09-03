import json
import os
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel
from google.genai import types

from pramaan.bq import execute_query, execute_query_job, get_partition_info
from pramaan.config import get_genai_client
from pramaan.contracts import get_next_version
from pramaan.rules import RuleUnion

class DraftContract(BaseModel):
    dataset: str
    table: str
    version: int = 1
    rules: List[RuleUnion]

NUMERIC_TYPES = {"INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
TIMESTAMP_TYPES = {"TIMESTAMP", "DATE", "DATETIME"}
DISTINCT_UNSAFE_TYPES = {"GEOGRAPHY", "JSON"}

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

def _compile_column_profile_sql(dataset: str, table: str, column_name: str, data_type: str) -> str:
    """One row of stats for a single column: null_rate/distinct_ratio always;
    min/max/p1/p99 for numeric columns; max_timestamp/hours_since_max/
    rows_per_day_(min|avg) for timestamp columns. Fields that don't apply to
    this column's type come back NULL so every SELECT shares one schema and
    can be UNION ALL'd together."""
    full_table = f"`{dataset}.{table}`"
    is_numeric = data_type in NUMERIC_TYPES
    is_timestamp = data_type in TIMESTAMP_TYPES
    is_distinct_safe = data_type not in DISTINCT_UNSAFE_TYPES

    distinct_expr = (
        f"SAFE_DIVIDE(COUNT(DISTINCT {column_name}), COUNT(*))"
        if is_distinct_safe else "CAST(NULL AS FLOAT64)"
    )

    if is_numeric:
        min_expr = f"CAST(MIN({column_name}) AS FLOAT64)"
        max_expr = f"CAST(MAX({column_name}) AS FLOAT64)"
        p1_expr = f"CAST(APPROX_QUANTILES({column_name}, 100)[OFFSET(1)] AS FLOAT64)"
        p99_expr = f"CAST(APPROX_QUANTILES({column_name}, 100)[OFFSET(99)] AS FLOAT64)"
    else:
        min_expr = max_expr = p1_expr = p99_expr = "CAST(NULL AS FLOAT64)"

    if is_timestamp:
        max_ts_expr = f"MAX({column_name})"
        hours_since_expr = f"TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX({column_name}), HOUR)"
        daily_subquery = f"""
            SELECT COUNT(*) AS daily_cnt
            FROM {full_table}
            WHERE DATE({column_name}) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
              AND DATE({column_name}) < CURRENT_DATE()
            GROUP BY DATE({column_name})
        """
        rows_min_expr = f"(SELECT MIN(daily_cnt) FROM ({daily_subquery}))"
        rows_avg_expr = f"(SELECT AVG(daily_cnt) FROM ({daily_subquery}))"
    else:
        max_ts_expr = "CAST(NULL AS TIMESTAMP)"
        hours_since_expr = "CAST(NULL AS FLOAT64)"
        rows_min_expr = rows_avg_expr = "CAST(NULL AS FLOAT64)"

    return f"""
    SELECT
        '{column_name}' AS column_name,
        '{data_type}' AS data_type,
        SAFE_DIVIDE(COUNTIF({column_name} IS NULL), COUNT(*)) AS null_rate,
        {distinct_expr} AS distinct_ratio,
        {min_expr} AS min_value,
        {max_expr} AS max_value,
        {p1_expr} AS p1,
        {p99_expr} AS p99,
        {max_ts_expr} AS max_timestamp,
        {hours_since_expr} AS hours_since_max,
        {rows_min_expr} AS rows_per_day_min,
        {rows_avg_expr} AS rows_per_day_avg
    FROM {full_table}
    """

def compile_profile_query(dataset: str, table: str, columns: List[Dict[str, Any]]) -> str:
    """UNION ALL of one profile row per top-level column (nested STRUCT/ARRAY
    sub-fields, i.e. field_paths containing '.', are skipped)."""
    subqueries = [
        _compile_column_profile_sql(dataset, table, c["field_path"], c["data_type"])
        for c in columns
        if "." not in c["field_path"]
    ]
    if not subqueries:
        raise ValueError(f"No profilable columns found for {dataset}.{table}")
    return "\nUNION ALL\n".join(subqueries)

def profile_table(dataset: str, table: str, schema_info: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Optional[int]]:
    """Runs ONE bounded UNION ALL query producing per-column observed stats.
    Returns (stats keyed by column name, total_bytes_billed)."""
    query = compile_profile_query(dataset, table, schema_info)
    job = execute_query_job(query)
    column_stats = {row["column_name"]: dict(row) for row in job.result()}
    return column_stats, job.total_bytes_billed

def detect_fk_candidates(dataset: str, table: str, column_stats: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Name-matches *_id columns against other tables in the dataset (base
    name + 's', looking for an 'id' or '<base>_id' column on the candidate
    parent), then verifies the join actually holds: only candidates where
    >99% of non-null child values match a parent row are returned."""
    dataset_columns_rows = execute_query(f"""
        SELECT table_name, column_name
        FROM `{dataset}.INFORMATION_SCHEMA.COLUMNS`
    """)
    columns_by_table: Dict[str, set] = {}
    for row in dataset_columns_rows:
        columns_by_table.setdefault(row["table_name"], set()).add(row["column_name"])

    def _find_parent(column_name: str) -> Optional[Tuple[str, str]]:
        base = column_name[: -len("_id")]
        for parent_table in (f"{base}s", base):
            if parent_table == table or parent_table not in columns_by_table:
                continue
            for parent_column in ("id", column_name):
                if parent_column in columns_by_table[parent_table]:
                    return parent_table, parent_column
        return None

    candidates = []
    for column_name in column_stats:
        if not column_name.endswith("_id"):
            continue
        parent = _find_parent(column_name)
        if parent is None:
            continue
        parent_table, parent_column = parent
        rows = list(execute_query(f"""
            SELECT SAFE_DIVIDE(COUNTIF(parent.{parent_column} IS NOT NULL), COUNT(*)) AS match_rate
            FROM `{dataset}.{table}` AS child
            LEFT JOIN `{dataset}.{parent_table}` AS parent ON child.{column_name} = parent.{parent_column}
            WHERE child.{column_name} IS NOT NULL
        """))
        match_rate = rows[0]["match_rate"] if rows else None
        if match_rate is not None and match_rate > 0.99:
            candidates.append({
                "column": column_name,
                "parent_table": f"{dataset}.{parent_table}",
                "parent_column": parent_column,
                "match_rate": match_rate,
            })
    return candidates

def normalize_invariant_thresholds(rules: List[RuleUnion], column_stats: Dict[str, Dict[str, Any]]) -> None:
    """Margin is for measured rates, not for invariants: a column observed at
    null_rate 0.0 gets max_null_rate forced to 0.0, and a column observed at
    distinct_ratio 1.0 (a key) gets max_duplicate_rate forced to 0.0 --
    overriding whatever margin the model proposed. Mutates `rules` in place;
    doesn't trust prompt compliance for this any more than for anything else
    in this file."""
    for rule in rules:
        stats = column_stats.get(getattr(rule, "column", None))
        if stats is None:
            continue
        if rule.rule_type == "null_rate" and stats.get("null_rate") == 0.0:
            rule.max_null_rate = 0.0
        elif rule.rule_type == "uniqueness" and stats.get("distinct_ratio") == 1.0:
            rule.max_duplicate_rate = 0.0

def validate_rule_has_supporting_stat(
    rule: RuleUnion,
    column_stats: Dict[str, Dict[str, Any]],
    fk_candidates: List[Dict[str, Any]],
) -> None:
    """Raises ValueError if `rule`'s threshold has no corresponding observed
    stat behind it. schema_conformance is checked against the actual profiled
    column list instead of a per-column numeric stat."""
    if rule.rule_type == "schema_conformance":
        unknown = [c for c in rule.expected_columns if c not in column_stats]
        if unknown:
            raise ValueError(
                f"schema_conformance rule '{rule.rule_id}' references columns {unknown} "
                f"that were not profiled -- no supporting stat"
            )
        return

    stats = column_stats.get(rule.column)
    if stats is None:
        raise ValueError(
            f"Rule '{rule.rule_id}' ({rule.rule_type}) references column '{rule.column}', "
            f"which was not profiled -- no supporting stat"
        )

    if rule.rule_type == "null_rate":
        if stats.get("null_rate") is None:
            raise ValueError(f"Rule '{rule.rule_id}': no null_rate stat for column '{rule.column}'")
    elif rule.rule_type == "uniqueness":
        if stats.get("distinct_ratio") is None:
            raise ValueError(f"Rule '{rule.rule_id}': no distinct_ratio stat for column '{rule.column}'")
    elif rule.rule_type == "range":
        if stats.get("p1") is None or stats.get("p99") is None:
            raise ValueError(f"Rule '{rule.rule_id}': no p1/p99 stat for column '{rule.column}' (not a numeric column)")
    elif rule.rule_type == "freshness":
        if stats.get("hours_since_max") is None:
            raise ValueError(f"Rule '{rule.rule_id}': no hours_since_max stat for column '{rule.column}' (not a timestamp column)")
    elif rule.rule_type == "row_count_drift":
        if stats.get("rows_per_day_avg") is None:
            raise ValueError(f"Rule '{rule.rule_id}': no rows_per_day stat available to support row_count_drift")
    elif rule.rule_type == "referential_integrity":
        matched = any(
            fk["column"] == rule.column
            and fk["parent_table"] == rule.parent_table
            and fk["parent_column"] == rule.parent_column
            for fk in fk_candidates
        )
        if not matched:
            raise ValueError(
                f"Rule '{rule.rule_id}': {rule.column} -> {rule.parent_table}.{rule.parent_column} "
                f"was not verified as a >99% FK match"
            )
    else:
        raise ValueError(f"Rule '{rule.rule_id}': rule_type '{rule.rule_type}' has no supporting-stat category in this profiler")

def generate_draft_contract(dataset: str, table: str) -> Tuple[DraftContract, Optional[int]]:
    """Profiles table schema + observed stats (one bounded UNION ALL query),
    partition metadata, and verified FK candidates, then asks Gemini to
    propose rules grounded in them. Every proposed rule is checked against
    the same stats before being returned -- a rule with no supporting stat
    raises ValueError rather than being silently included.

    Returns (draft, profile_query_bytes_billed).
    """
    schema_info = fetch_table_schema_metadata(dataset, table)
    column_stats, bytes_billed = profile_table(dataset, table, schema_info)
    partitions = get_partition_info(dataset, table)
    fk_candidates = detect_fk_candidates(dataset, table, column_stats)
    next_version = get_next_version(dataset, table)

    client = get_genai_client()

    prompt = f"""
    You are an automated data profiling engine. Analyze this observed data for
    table `{dataset}.{table}`.

    Schema:
    {json.dumps(schema_info, indent=2, default=str)}

    Observed per-column statistics (null_rate and distinct_ratio are always
    present; min/max/p1/p99 are only present for numeric columns;
    max_timestamp/hours_since_max/rows_per_day_min/rows_per_day_avg are only
    present for timestamp columns; a field is null when it doesn't apply to
    that column's type):
    {json.dumps(column_stats, indent=2, default=str)}

    Partition metadata:
    {json.dumps(partitions, indent=2, default=str)}

    Verified foreign-key candidates (name-matched *_id columns whose join to
    the named parent table/column holds for over 99% of non-null values):
    {json.dumps(fk_candidates, indent=2, default=str)}

    Propose a set of data quality rules for this table, using ONLY the rule
    types listed below, and ONLY when you have a specific observed statistic
    backing the threshold. NEVER invent a threshold with no supporting stat --
    if a plausible rule's stat isn't in the data above, don't propose it.

    Every rule below REQUIRES a "column" field (a string), even
    row_count_drift, which doesn't use it in its check but still needs one --
    use the timestamp column whose rows_per_day stat you used.

    - null_rate: fields rule_id, rule_type, column, max_null_rate. Derive
      max_null_rate from the column's observed null_rate plus a small margin
      (e.g. observed null_rate 0.18 -> max_null_rate around 0.25). EXCEPTION:
      if the observed null_rate is exactly 0.0, propose max_null_rate = 0.0
      with NO margin -- that's an invariant (this column is never null), not
      a measured rate to pad. max_null_rate MUST be clamped to 0.0-1.0.
    - uniqueness: fields rule_id, rule_type, column, max_duplicate_rate.
      Derive max_duplicate_rate from the column's observed distinct_ratio
      (roughly 1 - distinct_ratio, plus a small margin). EXCEPTION: if the
      observed distinct_ratio is exactly 1.0 (the column is a key), propose
      max_duplicate_rate = 0.0 with NO margin -- that's an invariant (this
      column is always unique), not a measured rate to pad. Only propose
      uniqueness for columns that are meant to be unique or near-unique
      (keys), not for low-cardinality/categorical columns where duplication
      is normal. max_duplicate_rate MUST be clamped to 0.0-1.0.
    - range: fields rule_id, rule_type, column, min_value, max_value. Use the
      column's observed p1/p99, widened by a small margin. Only for numeric
      columns that hold real measured values, not identifiers.
    - freshness: fields rule_id, rule_type, column, max_lag_hours. Derive
      max_lag_hours from the column's observed hours_since_max, plus a small
      margin. Only for timestamp columns where hours_since_max is POSITIVE
      (the observed max value is in the past, so "staleness" is meaningful).
      If hours_since_max is negative (the column's max value is in the
      future -- e.g. a planned/placeholder date like a not-yet-happened
      shipment), do NOT propose a freshness rule for it; there is no
      meaningful staleness threshold for a column whose values are inherently
      forward-dated.
    - row_count_drift: fields rule_id, rule_type, column, min_row_count. The
      check compares min_row_count against the AVERAGE row count over the
      last 3 fully-completed days (grouped by DATE(column)), NOT the table's
      total row count -- so min_row_count MUST come from the observed
      rows_per_day stats (a conservative floor a bit below
      rows_per_day_min), never from a total-row-count idea. "column" must be
      the timestamp column whose rows_per_day stats you're using -- the
      check groups by DATE(column). Only propose this if rows_per_day stats
      are available for some timestamp column.
    - referential_integrity: fields rule_id, rule_type, column, parent_table,
      parent_column. Only for columns listed in the verified foreign-key
      candidates above. Use parent_table/parent_column exactly as given
      there.
    - schema_conformance: fields rule_id, rule_type, expected_columns (a list
      -- this type has NO "column" field). expected_columns must be exactly
      the observed top-level column names from the schema above.

    Do not propose set_membership or regex_conformance rules -- no supporting
    stat is computed for them.

    For EVERY rule, include a "rationale" field: one sentence naming the exact
    observed stat (with its value) the threshold was derived from.

    Return ONLY a valid JSON object matching this schema:
    {{
      "dataset": "{dataset}",
      "table": "{table}",
      "version": {next_version},
      "rules": [
        {{
          "rule_id": "rule_1",
          "rule_type": "null_rate",
          "column": "column_name",
          "max_null_rate": 0.05,
          "rationale": "observed null_rate was 0.02 for this column; 0.05 gives headroom."
        }}
      ]
    }}

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
        raw_json["version"] = next_version
        draft = DraftContract.model_validate(raw_json)
    except Exception as e:
        raise ValueError(f"Profiler failed to produce a valid typed contract. Error: {e}\nRaw Output: {response.text}")

    normalize_invariant_thresholds(draft.rules, column_stats)

    for rule in draft.rules:
        validate_rule_has_supporting_stat(rule, column_stats, fk_candidates)

    return draft, bytes_billed
