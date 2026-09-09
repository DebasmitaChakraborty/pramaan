import inspect
from typing import Dict, Any, Callable, List, Optional
from pydantic import BaseModel
from pramaan.chaos import INJECT_FUNCS, get_ground_truth_logs, restore
from pramaan.sentry import SweepResult, execute_sweep

class EvalMetrics(BaseModel):
    pass

# Order the historical fault-matrix runs used (see git history): orders' three
# faults first, then order_items' three, then users' one. historical_mass_delete
# is new -- grouped next to row_count_collapse since both target orders and both
# exercise row_count_drift's blind spot (one inside it, one outside).
FAULT_ORDER = [
    "silent_duplicate_load",
    "stalled_partition",
    "row_count_collapse",
    "historical_mass_delete",
    "currency_swap",
    "referential_orphan",
    "schema_drift",
    "null_flood",
]

def evaluate_sweep_results(
    sweep_results_by_table: Dict[str, List[SweepResult]],
    run_id: str,
    ground_truths: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Scores precision/recall against ground truth, scoped to a single run_id and
    counted per fault_id rather than per violation:
      - a fault whose expected (table, rule_type) fires at least once is one TP,
        no matter how many matching violation rows it produces;
      - a fault whose expected (table, rule_type) never fires is one FN;
      - a violation whose (table, rule_type) matches no fault_id in this run is
        one FP.
    Ground truth entries from other runs (or logged before run_id existed) are
    excluded from the pool entirely.
    """
    if ground_truths is None:
        ground_truths = get_ground_truth_logs()
    ground_truths = [g for g in ground_truths if g.get("run_id") == run_id]

    expected_pairs = {(g["table"], g["expected_rule_type"]) for g in ground_truths}

    tp = 0
    fn = 0
    for g in ground_truths:
        table_results = sweep_results_by_table.get(g["table"], [])
        hit = any(
            r.is_violation and r.rule_type == g["expected_rule_type"]
            for r in table_results
        )
        if hit:
            tp += 1
        else:
            fn += 1

    fp = 0
    total_violations = 0
    for table, results in sweep_results_by_table.items():
        for r in results:
            if not r.is_violation:
                continue
            total_violations += 1
            if (table, r.rule_type) not in expected_pairs:
                fp += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    return {
        "run_id": run_id,
        "confusion_matrix": {"TP": tp, "FP": fp, "FN": fn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "total_ground_truth_faults": len(ground_truths),
        "total_sweep_violations": total_violations,
    }

def run_isolated_fault(
    dataset: str,
    table: str,
    inject_func: Callable[..., Dict[str, Any]],
    run_id: str,
) -> Dict[str, Any]:
    """Runs exactly one fault fully isolated: inject -> sweep the faulted table ->
    score against only this fault's own ground-truth entry -> restore the table,
    before returning. This is the fix for the batched-matrix bug: injecting all
    N faults back to back and evaluating once at the end let faults on the same
    table stack (e.g. a row-count collapse deleting the evidence of an
    already-injected duplicate), corrupting both the score and, for
    stacked DML/DDL, the underlying data. Isolation means the table this fault
    touched is back at its pre-fault snapshot before the next fault can start.
    """
    entry = inject_func(dataset, table, run_id=run_id)
    sweep_results = execute_sweep(dataset, table)
    scored = evaluate_sweep_results({table: sweep_results}, run_id=run_id, ground_truths=[entry])
    restore(dataset, table)
    return {
        "fault_id": entry["fault_id"],
        "scenario": entry["scenario"],
        "table": table,
        "target_column": entry["target_column"],
        "expected_rule_type": entry["expected_rule_type"],
        "caught": scored["confusion_matrix"]["TP"] == 1,
        "confusion_matrix": scored["confusion_matrix"],
        "violations": [r.model_dump() for r in sweep_results if r.is_violation],
    }

def run_fault_matrix(
    dataset: str,
    run_id: str,
    fault_kinds: Optional[List[str]] = None,
    inject_funcs: Optional[Dict[str, Callable]] = None,
) -> Dict[str, Any]:
    """Runs each fault kind in full isolation (see run_isolated_fault) -- one
    fault injected, swept, scored, and restored before the next one starts, so
    faults never stack on the same table. Returns a run_id-scoped report: a
    per-fault breakdown (so a miss can be explained, not just counted) plus an
    aggregate confusion matrix summed across all per-fault isolated sweeps.
    """
    fault_kinds = fault_kinds or FAULT_ORDER
    inject_funcs = inject_funcs or INJECT_FUNCS

    per_fault = []
    for fault_kind in fault_kinds:
        func = inject_funcs[fault_kind]
        table = inspect.signature(func).parameters["table"].default
        per_fault.append(run_isolated_fault(dataset, table, func, run_id))

    tp = sum(f["confusion_matrix"]["TP"] for f in per_fault)
    fp = sum(f["confusion_matrix"]["FP"] for f in per_fault)
    fn = sum(f["confusion_matrix"]["FN"] for f in per_fault)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    return {
        "run_id": run_id,
        "per_fault": per_fault,
        "confusion_matrix": {"TP": tp, "FP": fp, "FN": fn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "total_ground_truth_faults": len(per_fault),
        "total_sweep_violations": sum(len(f["violations"]) for f in per_fault),
    }
