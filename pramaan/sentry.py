from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel
from pramaan.bq import execute_query, execute_query_job, get_table_columns
from pramaan.contracts import load_active_contract
from pramaan.rules import compile_sweep_query, RuleUnion, NullRateRule, UniquenessRule, ReferentialIntegrityRule, RangeRule, SetMembershipRule, FreshnessRule, RowCountDriftRule, RegexConformanceRule, SchemaConformanceRule

class SweepResult(BaseModel):
    rule_id: str
    rule_type: str
    metric_value: float
    threshold: float
    is_violation: bool

def parse_rule_dict(d: dict) -> RuleUnion:
    rtype = d.get("rule_type")
    if rtype == "null_rate":
        return NullRateRule(**d)
    elif rtype == "uniqueness":
        return UniquenessRule(**d)
    elif rtype == "referential_integrity":
        return ReferentialIntegrityRule(**d)
    elif rtype == "range":
        return RangeRule(**d)
    elif rtype == "set_membership":
        return SetMembershipRule(**d)
    elif rtype == "freshness":
        return FreshnessRule(**d)
    elif rtype == "row_count_drift":
        return RowCountDriftRule(**d)
    elif rtype == "regex_conformance":
        return RegexConformanceRule(**d)
    elif rtype == "schema_conformance":
        return SchemaConformanceRule(**d)
    raise ValueError(f"Unknown rule type: {rtype}")

def check_schema_conformance(rule: SchemaConformanceRule, actual_columns: List[str]) -> List[SweepResult]:
    """Emits one violation row per expected column that is missing (dropped or
    renamed) from `actual_columns`. No row is emitted for columns that are present."""
    missing = [c for c in rule.expected_columns if c not in actual_columns]
    return [
        SweepResult(
            rule_id=f"{rule.rule_id}:{col}",
            rule_type=rule.rule_type,
            metric_value=1.0,
            threshold=0.0,
            is_violation=True,
        )
        for col in missing
    ]

def execute_sweep(dataset: str, table: str) -> List[SweepResult]:
    """Loads active approved contract and runs its rules: SQL-compilable rules as a
    single UNION ALL query, schema_conformance rules against the live column list."""
    contract = load_active_contract(dataset, table)
    parsed_rules = [parse_rule_dict(r) for r in contract.rules]

    sql_rules = [r for r in parsed_rules if r.rule_type != "schema_conformance"]
    schema_rules = [r for r in parsed_rules if r.rule_type == "schema_conformance"]

    results: List[SweepResult] = []

    if sql_rules:
        sweep_sql = compile_sweep_query(sql_rules, dataset, table)
        rows = execute_query(sweep_sql)
        results.extend(SweepResult(**dict(row)) for row in rows)

    if schema_rules:
        actual_columns = [c["column_name"] for c in get_table_columns(dataset, table)]
        for rule in schema_rules:
            results.extend(check_schema_conformance(rule, actual_columns))

    return results

def execute_sweep_with_timing(dataset: str, table: str) -> Tuple[List[SweepResult], Optional[Any], Optional[Any]]:
    """Like execute_sweep, but also returns (sql_job_ended, schema_job_ended) --
    the BigQuery job completion timestamps for the SQL sweep query and the
    schema_conformance column-list query, respectively (None if that branch
    didn't run). Lets callers measure detection latency without folding in
    snapshot/restore time."""
    contract = load_active_contract(dataset, table)
    parsed_rules = [parse_rule_dict(r) for r in contract.rules]

    sql_rules = [r for r in parsed_rules if r.rule_type != "schema_conformance"]
    schema_rules = [r for r in parsed_rules if r.rule_type == "schema_conformance"]

    results: List[SweepResult] = []
    sql_job_ended = None
    schema_job_ended = None

    if sql_rules:
        sweep_sql = compile_sweep_query(sql_rules, dataset, table)
        job = execute_query_job(sweep_sql)
        sql_job_ended = job.ended
        results.extend(SweepResult(**dict(row)) for row in job.result())

    if schema_rules:
        actual_columns, schema_job_ended = get_table_columns(dataset, table, return_job=True)
        actual_column_names = [c["column_name"] for c in actual_columns]
        for rule in schema_rules:
            results.extend(check_schema_conformance(rule, actual_column_names))

    return results, sql_job_ended, schema_job_ended
