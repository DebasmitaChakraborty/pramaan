from pramaan.evals import evaluate_sweep_results, run_fault_matrix, run_isolated_fault
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


def test_isolated_fault_restores_row_count_to_snapshot_before_returning(monkeypatch):
    """run_isolated_fault must leave the table back at its pre-fault row count
    before it returns -- the whole point of running one fault at a time instead
    of injecting a batch and evaluating once at the end."""
    state = {"row_count": 1000, "snapshot_row_count": 1000}

    def fake_inject(dataset, table, run_id):
        state["row_count"] = 87  # simulate a collapse
        return _gt("f1", table, "row_count_drift", run_id=run_id)

    def fake_execute_sweep(dataset, table):
        assert state["row_count"] == 87, "sweep must observe the injected (dirty) state"
        return [_violation("r13", "row_count_drift")]

    def fake_restore(dataset, table):
        state["row_count"] = state["snapshot_row_count"]

    monkeypatch.setattr("pramaan.evals.execute_sweep", fake_execute_sweep)
    monkeypatch.setattr("pramaan.evals.restore", fake_restore)

    result = run_isolated_fault("pramaan_demo", "orders", fake_inject, run_id="r1")

    assert result["caught"] is True
    assert state["row_count"] == state["snapshot_row_count"]


def test_fault_matrix_restores_before_the_next_fault_starts(monkeypatch):
    """Two faults sharing a table: if the first fault's restore ran before the
    second fault's inject (isolation held, not stacking), the table's row count
    equals the snapshot's the instant the second inject function is called."""
    state = {"row_count": 1000, "snapshot_row_count": 1000}
    row_count_at_second_inject = {}

    def first_inject(dataset, table="orders", run_id=None):
        state["row_count"] -= 500
        return _gt("first", table, "none", run_id=run_id)

    def second_inject(dataset, table="orders", run_id=None):
        row_count_at_second_inject["value"] = state["row_count"]
        state["row_count"] -= 900
        return _gt("second", table, "none", run_id=run_id)

    monkeypatch.setattr("pramaan.evals.execute_sweep", lambda dataset, table: [])
    monkeypatch.setattr(
        "pramaan.evals.restore",
        lambda dataset, table: state.__setitem__("row_count", state["snapshot_row_count"]),
    )

    run_fault_matrix(
        "pramaan_demo",
        run_id="r1",
        fault_kinds=["first", "second"],
        inject_funcs={"first": first_inject, "second": second_inject},
    )

    assert row_count_at_second_inject["value"] == state["snapshot_row_count"]


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
