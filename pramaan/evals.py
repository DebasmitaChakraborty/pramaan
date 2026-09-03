from typing import List, Dict, Any
from pramaan.chaos import get_ground_truth_logs
from pramaan.sentry import SweepResult

class EvalMetrics(BaseModel if 'BaseModel' in globals() else object):
    pass

def evaluate_sweep_results(sweep_results: List[SweepResult]) -> Dict[str, Any]:
    """Scores precision, recall, and confusion matrix against ground truth."""
    ground_truths = get_ground_truth_logs()
    
    violations = [r for r in sweep_results if r.is_violation]
    
    tp = 0
    fp = 0
    fn = 0

    matched_faults = set()
    
    for v in violations:
        # Match violation back to a logged ground truth fault
        match = next(
            (g for g in ground_truths if g["expected_rule_type"] == v.rule_type and g["fault_id"] not in matched_faults),
            None
        )
        if match:
            tp += 1
            matched_faults.add(match["fault_id"])
        else:
            fp += 1
            
    fn = len(ground_truths) - len(matched_faults)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    return {
        "confusion_matrix": {"TP": tp, "FP": fp, "FN": fn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "total_ground_truth_faults": len(ground_truths),
        "total_sweep_violations": len(violations)
    }
