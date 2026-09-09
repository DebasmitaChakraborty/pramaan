import pytest
from pramaan.profiler import validate_rule_has_supporting_stat
from pramaan.rules import (
    FreshnessRule,
    NullRateRule,
    RangeRule,
    ReferentialIntegrityRule,
    RowCountDriftRule,
    SchemaConformanceRule,
)

COLUMN_STATS = {
    "email": {"null_rate": 0.02, "distinct_ratio": 0.99, "p1": None, "p99": None, "hours_since_max": None, "rows_per_day_avg": None},
    "sale_price": {"null_rate": 0.0, "distinct_ratio": 0.3, "p1": 2.5, "p99": 899.0, "hours_since_max": None, "rows_per_day_avg": None},
    "created_at": {"null_rate": 0.0, "distinct_ratio": 1.0, "p1": None, "p99": None, "hours_since_max": 19.0, "rows_per_day_avg": 450.0},
    "user_id": {"null_rate": 0.0, "distinct_ratio": 0.5, "p1": None, "p99": None, "hours_since_max": None, "rows_per_day_avg": None},
}

FK_CANDIDATES = [
    {"column": "user_id", "parent_table": "pramaan_demo.users", "parent_column": "id", "match_rate": 1.0},
]


def test_null_rate_rule_with_supporting_stat_is_accepted():
    rule = NullRateRule(rule_id="r1", column="email", max_null_rate=0.05)
    validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)  # should not raise


def test_rule_on_unprofiled_column_is_rejected():
    rule = NullRateRule(rule_id="r1", column="not_a_real_column", max_null_rate=0.05)
    with pytest.raises(ValueError, match="no supporting stat"):
        validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_range_rule_without_p1_p99_is_rejected():
    """email has no numeric stats (it's a string column) -- a range rule on it
    has no supporting stat even though the column itself was profiled."""
    rule = RangeRule(rule_id="r2", column="email", min_value=0.0, max_value=1.0)
    with pytest.raises(ValueError, match="no p1/p99 stat"):
        validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_range_rule_with_p1_p99_is_accepted():
    rule = RangeRule(rule_id="r2", column="sale_price", min_value=0.0, max_value=1000.0)
    validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_freshness_rule_without_timestamp_stat_is_rejected():
    rule = FreshnessRule(rule_id="r3", column="sale_price", max_lag_hours=36.0)
    with pytest.raises(ValueError, match="no hours_since_max stat"):
        validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_row_count_drift_without_daily_volume_stat_is_rejected():
    rule = RowCountDriftRule(rule_id="r4", column="email", min_ratio=0.5)
    with pytest.raises(ValueError, match="no rows_per_day stat"):
        validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_referential_integrity_without_verified_fk_is_rejected():
    rule = ReferentialIntegrityRule(
        rule_id="r5", column="sale_price",
        parent_table="pramaan_demo.products", parent_column="id",
    )
    with pytest.raises(ValueError, match="not verified as a >99% FK match"):
        validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_referential_integrity_with_verified_fk_is_accepted():
    rule = ReferentialIntegrityRule(
        rule_id="r5", column="user_id",
        parent_table="pramaan_demo.users", parent_column="id",
    )
    validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_schema_conformance_with_unprofiled_column_is_rejected():
    rule = SchemaConformanceRule(rule_id="r6", expected_columns=["email", "made_up_column"])
    with pytest.raises(ValueError, match="not profiled"):
        validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)


def test_schema_conformance_with_profiled_columns_is_accepted():
    rule = SchemaConformanceRule(rule_id="r6", expected_columns=["email", "sale_price", "created_at"])
    validate_rule_has_supporting_stat(rule, COLUMN_STATS, FK_CANDIDATES)
