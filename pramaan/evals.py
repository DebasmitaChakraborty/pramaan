from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from pramaan.chaos import get_ground_truth_logs
from pramaan.sentry import SweepResult

class EvalMetrics(BaseModel):
    pass

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
