import re
from typing import Literal, Union, List, Optional
from pydantic import BaseModel, Field, field_validator

IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

def validate_identifier(name: str) -> str:
    """Rejects SQL injection by validating table and column identifiers."""
    if not IDENTIFIER_REGEX.match(name):
        raise ValueError(f"Invalid SQL identifier: '{name}'")
    return name

class BaseRule(BaseModel):
    rule_id: str
    rule_type: str
    column: str
    rationale: Optional[str] = None

    @field_validator("column")
    def check_column(cls, v):
        return validate_identifier(v)

class NullRateRule(BaseRule):
    rule_type: Literal["null_rate"] = "null_rate"
    max_null_rate: float = Field(..., ge=0.0, le=1.0)

class UniquenessRule(BaseRule):
    rule_type: Literal["uniqueness"] = "uniqueness"
    max_duplicate_rate: float = Field(0.0, ge=0.0, le=1.0)

class ReferentialIntegrityRule(BaseRule):
    rule_type: Literal["referential_integrity"] = "referential_integrity"
    parent_table: str
    parent_column: str

    @field_validator("parent_table", "parent_column")
    def check_parent_identifiers(cls, v):
        parts = v.split(".")
        for part in parts:
            validate_identifier(part)
        return v

class RangeRule(BaseRule):
    rule_type: Literal["range"] = "range"
    min_value: Optional[float] = None
    max_value: Optional[float] = None

class SetMembershipRule(BaseRule):
    rule_type: Literal["set_membership"] = "set_membership"
    allowed_values: List[str]

class FreshnessRule(BaseRule):
    rule_type: Literal["freshness"] = "freshness"
    max_lag_hours: float

class RowCountDriftRule(BaseRule):
    rule_type: Literal["row_count_drift"] = "row_count_drift"
    min_ratio: float = Field(..., ge=0.0)

class RegexConformanceRule(BaseRule):
    rule_type: Literal["regex_conformance"] = "regex_conformance"
    pattern: str

class SchemaConformanceRule(BaseModel):
    """Checks the table's actual columns against an expected list. Compiles to
    nothing in the sweep SQL -- sentry.execute_sweep checks it separately via
    bq.get_table_columns, since it isn't a per-row metric a SELECT can produce."""
    rule_id: str
    rule_type: Literal["schema_conformance"] = "schema_conformance"
    expected_columns: List[str]
    rationale: Optional[str] = None

RuleUnion = Union[
    NullRateRule,
    UniquenessRule,
    ReferentialIntegrityRule,
    RangeRule,
    SetMembershipRule,
    FreshnessRule,
    RowCountDriftRule,
    RegexConformanceRule,
    SchemaConformanceRule
]

def compile_rule_to_sql(rule: RuleUnion, dataset: str, table: str) -> str:
    validate_identifier(table)
    full_table = f"`{dataset}.{table}`"

    if rule.rule_type == "null_rate":
        return f"""
        SELECT 
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            SAFE_DIVIDE(COUNTIF({rule.column} IS NULL), COUNT(*)) AS metric_value,
            {rule.max_null_rate} AS threshold,
            (SAFE_DIVIDE(COUNTIF({rule.column} IS NULL), COUNT(*)) > {rule.max_null_rate}) AS is_violation
        FROM {full_table}
        """
    elif rule.rule_type == "uniqueness":
        return f"""
        SELECT 
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            SAFE_DIVIDE(COUNT(*) - COUNT(DISTINCT {rule.column}), COUNT(*)) AS metric_value,
            {rule.max_duplicate_rate} AS threshold,
            (SAFE_DIVIDE(COUNT(*) - COUNT(DISTINCT {rule.column}), COUNT(*)) > {rule.max_duplicate_rate}) AS is_violation
        FROM {full_table}
        """
    elif rule.rule_type == "referential_integrity":
        return f"""
        SELECT 
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            SAFE_DIVIDE(COUNTIF(parent.{rule.parent_column} IS NULL AND child.{rule.column} IS NOT NULL), COUNT(*)) AS metric_value,
            0.0 AS threshold,
            (COUNTIF(parent.{rule.parent_column} IS NULL AND child.{rule.column} IS NOT NULL) > 0) AS is_violation
        FROM {full_table} AS child
        LEFT JOIN `{rule.parent_table}` AS parent ON child.{rule.column} = parent.{rule.parent_column}
        """
    elif rule.rule_type == "range":
        conditions = []
        if rule.min_value is not None:
            conditions.append(f"{rule.column} < {rule.min_value}")
        if rule.max_value is not None:
            conditions.append(f"{rule.column} > {rule.max_value}")
        where_clause = " OR ".join(conditions) if conditions else "FALSE"
        return f"""
        SELECT 
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            SAFE_DIVIDE(COUNTIF({where_clause}), COUNT(*)) AS metric_value,
            0.0 AS threshold,
            (COUNTIF({where_clause}) > 0) AS is_violation
        FROM {full_table}
        """
    elif rule.rule_type == "set_membership":
        formatted_set = ", ".join([f"'{v}'" for v in rule.allowed_values])
        return f"""
        SELECT 
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            SAFE_DIVIDE(COUNTIF({rule.column} NOT IN ({formatted_set})), COUNT(*)) AS metric_value,
            0.0 AS threshold,
            (COUNTIF({rule.column} NOT IN ({formatted_set})) > 0) AS is_violation
        FROM {full_table}
        """
    elif rule.rule_type == "freshness":
        return f"""
        SELECT 
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX({rule.column}), HOUR) AS metric_value,
            {rule.max_lag_hours} AS threshold,
            (TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX({rule.column}), HOUR) > {rule.max_lag_hours}) AS is_violation
        FROM {full_table}
        """
    elif rule.rule_type == "row_count_drift":
        # Anchored to the data's own timeline, not the wall clock, so this is
        # correct both for a live pipeline and for a frozen/static snapshot
        # (e.g. a demo table copied from a public dataset): "latest day" is
        # the most recent COMPLETE calendar day present in `column` --
        # MAX(DATE(column)) minus one day, never MAX(DATE(column)) itself.
        # The single most recent day present is always a partial day (still
        # being written to on a live table; a trailing partial slice on a
        # frozen snapshot copied mid-day) and would chronically read as a
        # collapse otherwise. metric_value is the ratio of that complete
        # day's row count to the AVERAGE row count of the days immediately
        # before it (the baseline) -- a value near 1.0 is normal, a value
        # near 0 means the latest complete day's load collapsed relative to
        # recent history.
        latest_day_expr = f"(SELECT DATE_SUB(MAX(DATE({rule.column})), INTERVAL 1 DAY) FROM {full_table})"
        latest_count_expr = f"""(
            SELECT COUNT(*) FROM {full_table}
            WHERE DATE({rule.column}) = {latest_day_expr}
        )"""
        baseline_avg_expr = f"""(
            SELECT AVG(daily_cnt) FROM (
                SELECT COUNT(*) AS daily_cnt
                FROM {full_table}
                WHERE DATE({rule.column}) < {latest_day_expr}
                  AND DATE({rule.column}) >= DATE_SUB({latest_day_expr}, INTERVAL 3 DAY)
                GROUP BY DATE({rule.column})
            )
        )"""
        metric_expr = f"SAFE_DIVIDE({latest_count_expr}, {baseline_avg_expr})"
        return f"""
        SELECT
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            {metric_expr} AS metric_value,
            {rule.min_ratio} AS threshold,
            ({metric_expr} < {rule.min_ratio}) AS is_violation
        FROM (SELECT 1)
        """
    elif rule.rule_type == "regex_conformance":
        escaped_pattern = rule.pattern.replace("'", "\\'")
        return f"""
        SELECT 
            '{rule.rule_id}' AS rule_id,
            '{rule.rule_type}' AS rule_type,
            SAFE_DIVIDE(COUNTIF(NOT REGEXP_CONTAINS(CAST({rule.column} AS STRING), r'{escaped_pattern}')), COUNT(*)) AS metric_value,
            0.0 AS threshold,
            (COUNTIF(NOT REGEXP_CONTAINS(CAST({rule.column} AS STRING), r'{escaped_pattern}')) > 0) AS is_violation
        FROM {full_table}
        """
    raise ValueError(f"Unsupported rule type: {rule.rule_type}")

def compile_sweep_query(rules: List[RuleUnion], dataset: str, table: str) -> str:
    if not rules:
        raise ValueError("No rules provided for sweep compilation.")
    # schema_conformance has no per-row metric a SELECT can produce; sentry checks
    # it separately against bq.get_table_columns instead.
    sql_rules = [r for r in rules if r.rule_type != "schema_conformance"]
    if not sql_rules:
        raise ValueError("No SQL-compilable rules provided for sweep compilation.")
    subqueries = [compile_rule_to_sql(r, dataset, table) for r in sql_rules]
    return "\nUNION ALL\n".join(subqueries)
