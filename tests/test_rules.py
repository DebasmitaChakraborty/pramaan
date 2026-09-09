import pytest
from pramaan.rules import NullRateRule, UniquenessRule, RowCountDriftRule, compile_rule_to_sql, compile_sweep_query

def test_sql_injection_rejection():
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        NullRateRule(rule_id="r1", column="user_id; DROP TABLE users;--", max_null_rate=0.01)

def test_union_all_sweep_compilation():
    r1 = NullRateRule(rule_id="r1", column="id", max_null_rate=0.0)
    r2 = UniquenessRule(rule_id="r2", column="email", max_duplicate_rate=0.0)
    
    query = compile_sweep_query([r1, r2], "pramaan_demo", "users")
    assert "UNION ALL" in query
    assert "pramaan_demo.users" in query

def test_row_count_drift_anchors_to_last_complete_day_not_latest_present_day():
    """The most recent calendar day present in the data is always partial (still
    being written to on a live table, a trailing slice on a frozen snapshot) --
    the check must use MAX(DATE(column)) minus one day as \"latest\", never
    MAX(DATE(column)) itself, or it would chronically read as a collapse."""
    rule = RowCountDriftRule(rule_id="r13", column="created_at", min_ratio=0.5)
    sql = compile_rule_to_sql(rule, "pramaan_demo", "orders")
    assert "DATE_SUB(MAX(DATE(created_at)), INTERVAL 1 DAY)" in sql
