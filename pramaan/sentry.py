from typing import List, Dict, Any
from pydantic import BaseModel
from pramaan.bq import execute_query, get_table_columns
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
