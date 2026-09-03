import pytest
from pramaan.rules import NullRateRule, SchemaConformanceRule, compile_sweep_query
from pramaan.sentry import check_schema_conformance


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
