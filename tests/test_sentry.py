import pytest
from pramaan.rules import NullRateRule, SchemaConformanceRule, compile_sweep_query
from pramaan.sentry import check_schema_conformance, _row_to_sweep_result


def test_schema_conformance_compiles_to_nothing_in_sweep_sql():
    sql_rule = NullRateRule(rule_id="r1", column="email", max_null_rate=0.01)
    schema_rule = SchemaConformanceRule(rule_id="r2", expected_columns=["email", "id"])

    query = compile_sweep_query([sql_rule, schema_rule], "pramaan_demo", "users")

    assert "null_rate" in query
    assert "schema_conformance" not in query


def test_compile_sweep_query_rejects_schema_only_rule_list():
    schema_rule = SchemaConformanceRule(rule_id="r2", expected_columns=["email"])
    with pytest.raises(ValueError, match="No SQL-compilable rules"):
        compile_sweep_query([schema_rule], "pramaan_demo", "users")


def test_check_schema_conformance_flags_missing_column():
    rule = SchemaConformanceRule(rule_id="check_schema", expected_columns=["a", "b", "c"])

    results = check_schema_conformance(rule, actual_columns=["a", "c"])

    assert len(results) == 1
    assert results[0].rule_id == "check_schema:b"
    assert results[0].rule_type == "schema_conformance"
    assert results[0].is_violation is True


def test_check_schema_conformance_no_violation_when_columns_present():
    rule = SchemaConformanceRule(rule_id="check_schema", expected_columns=["a", "b"])

    results = check_schema_conformance(rule, actual_columns=["a", "b", "c"])

    assert results == []


def test_row_with_null_metric_becomes_violation_with_detail_not_a_crash():
    """A rule with no supporting window (e.g. row_count_drift with nothing to
    average) comes back from BigQuery as NULL metric/is_violation -- that
    must not raise a pydantic validation error, and must never read as a
    silent pass."""
    row = {"rule_id": "r13", "rule_type": "row_count_drift", "metric_value": None, "threshold": 0.5, "is_violation": None}

    result = _row_to_sweep_result(row)

    assert result.is_violation is True
    assert result.detail == "insufficient data for rule"


def test_row_with_all_fields_present_passes_through_unchanged():
    row = {"rule_id": "r1", "rule_type": "null_rate", "metric_value": 0.01, "threshold": 0.05, "is_violation": False}

    result = _row_to_sweep_result(row)

    assert result.is_violation is False
    assert result.detail is None
