from pramaan.evals import evaluate_sweep_results
from pramaan.sentry import SweepResult


def _gt(fault_id, table, expected_rule_type, run_id="run-1"):
    return {
        "fault_id": fault_id,
        "run_id": run_id,
        "scenario": "test",
        "table": table,
        "target_column": "col",
        "expected_rule_type": expected_rule_type,
        "injected_at": "2026-01-01T00:00:00+00:00",
    }


def _violation(rule_id, rule_type):
    return SweepResult(rule_id=rule_id, rule_type=rule_type, metric_value=1.0, threshold=0.0, is_violation=True)


def test_fault_tripping_the_same_rule_twice_is_one_tp():
    """A single fault (e.g. schema_drift dropping two columns) can produce
    multiple violation rows of its own expected rule_type -- still one TP."""
    ground_truths = [_gt("f1", "order_items", "schema_conformance")]
    sweep_results_by_table = {
        "order_items": [
            _violation("check_schema:a", "schema_conformance"),
            _violation("check_schema:b", "schema_conformance"),
        ]
    }
    result = evaluate_sweep_results(sweep_results_by_table, run_id="run-1", ground_truths=ground_truths)
    assert result["confusion_matrix"] == {"TP": 1, "FP": 0, "FN": 0}


def test_fault_not_caught_is_one_fn():
    ground_truths = [_gt("f1", "orders", "uniqueness")]
    sweep_results_by_table = {"orders": []}
    result = evaluate_sweep_results(sweep_results_by_table, run_id="run-1", ground_truths=ground_truths)
    assert result["confusion_matrix"] == {"TP": 0, "FP": 0, "FN": 1}


def test_violation_with_no_fault_in_run_is_one_fp():
    ground_truths = [_gt("f1", "orders", "uniqueness")]
    sweep_results_by_table = {
        "orders": [
            _violation("r1", "uniqueness"),
            _violation("r2", "freshness"),  # no fault in this run expects freshness
        ]
    }
    result = evaluate_sweep_results(sweep_results_by_table, run_id="run-1", ground_truths=ground_truths)
    assert result["confusion_matrix"] == {"TP": 1, "FP": 1, "FN": 0}


def test_old_entries_outside_run_id_are_excluded():
    ground_truths = [
        _gt("stale", "orders", "uniqueness", run_id="old-run"),
        _gt("fresh", "orders", "uniqueness", run_id="run-1"),
    ]
    sweep_results_by_table = {"orders": [_violation("r1", "uniqueness")]}
    result = evaluate_sweep_results(sweep_results_by_table, run_id="run-1", ground_truths=ground_truths)
    assert result["confusion_matrix"] == {"TP": 1, "FP": 0, "FN": 0}
    assert result["total_ground_truth_faults"] == 1


def test_repeat_detection_of_a_persistent_condition_does_not_inflate_fp():
    """A recurring baseline condition (e.g. a standing referential_integrity
    violation) re-detected across multiple sweeps of the same table should not
    generate extra FPs as long as some fault in the run expects that rule."""
    ground_truths = [_gt("f1", "order_items", "referential_integrity")]
    sweep_results_by_table = {
        "order_items": [
            _violation("r1", "referential_integrity"),
            _violation("r1", "referential_integrity"),
            _violation("r1", "referential_integrity"),
        ]
    }
    result = evaluate_sweep_results(sweep_results_by_table, run_id="run-1", ground_truths=ground_truths)
    assert result["confusion_matrix"] == {"TP": 1, "FP": 0, "FN": 0}
