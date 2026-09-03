import pytest
from pramaan.rules import NullRateRule, UniquenessRule, compile_sweep_query

def test_sql_injection_rejection():
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        NullRateRule(rule_id="r1", column="user_id; DROP TABLE users;--", max_null_rate=0.01)

def test_union_all_sweep_compilation():
    r1 = NullRateRule(rule_id="r1", column="id", max_null_rate=0.0)
    r2 = UniquenessRule(rule_id="r2", column="email", max_duplicate_rate=0.0)
    
    query = compile_sweep_query([r1, r2], "pramaan_demo", "users")
    assert "UNION ALL" in query
    assert "pramaan_demo.users" in query
