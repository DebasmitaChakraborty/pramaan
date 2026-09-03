from typing import List, Dict, Any
from pydantic import BaseModel
from pramaan.bq import execute_query
from pramaan.contracts import load_active_contract
from pramaan.rules import compile_sweep_query, RuleUnion, NullRateRule, UniquenessRule, ReferentialIntegrityRule, RangeRule, SetMembershipRule, FreshnessRule, RowCountDriftRule, RegexConformanceRule

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
    raise ValueError(f"Unknown rule type: {rtype}")

def execute_sweep(dataset: str, table: str) -> List[SweepResult]:
    """Loads active approved contract and runs N rules as single UNION ALL query."""
    contract = load_active_contract(dataset, table)
    parsed_rules = [parse_rule_dict(r) for r in contract.rules]
    
    sweep_sql = compile_sweep_query(parsed_rules, dataset, table)
    rows = execute_query(sweep_sql)
    
    return [SweepResult(**dict(row)) for row in rows]
