"""Command-line entry point for Pramaan. Thin wrapper over the library
functions in profiler/contracts/sentry/chaos/evals -- no business logic here.

Usage: python -m pramaan.cli <subcommand> ...
"""
import argparse
import getpass
import json
import os
import sys
import uuid

from pramaan.chaos import (
    inject_currency_swap,
    inject_null_flood,
    inject_referential_orphan,
    inject_row_count_collapse,
    inject_schema_drift,
    inject_silent_duplicate_load,
    inject_stalled_partition,
    restore,
)
from pramaan.contracts import CONTRACTS_DIR, approve_contract, list_active_contract_tables, save_draft_contract
from pramaan.evals import evaluate_sweep_results
from pramaan.profiler import generate_draft_contract
from pramaan.sentry import execute_sweep

DEFAULT_DATASET = os.getenv("DEMO_DATASET", "pramaan_demo")

INJECT_FUNCS = {
    "null_flood": inject_null_flood,
    "silent_duplicate_load": inject_silent_duplicate_load,
    "currency_swap": inject_currency_swap,
    "stalled_partition": inject_stalled_partition,
    "referential_orphan": inject_referential_orphan,
    "row_count_collapse": inject_row_count_collapse,
    "schema_drift": inject_schema_drift,
}


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_propose(args: argparse.Namespace) -> None:
    draft, bytes_billed = generate_draft_contract(args.dataset, args.table)
    path = save_draft_contract(draft)
    contract_id = f"{draft.dataset}:{draft.table}:{draft.version}"
    print(f"Draft contract saved to {path}")
    print(f"contract_id: {contract_id}")
    print(f"profile query bytes billed: {bytes_billed}")
    _print_json(draft.model_dump())


def cmd_approve(args: argparse.Namespace) -> None:
    try:
        dataset, table, version = args.contract_id.split(":")
    except ValueError:
        sys.exit(
            f"Invalid contract_id '{args.contract_id}'. "
            "Expected format dataset:table:version (as printed by `propose`)."
        )
    approved_by = args.by or os.getenv("USER") or getpass.getuser()
    approved = approve_contract(dataset, table, int(version), approved_by)
    _print_json(approved.model_dump())


def cmd_sweep(args: argparse.Namespace) -> None:
    results = execute_sweep(args.dataset, args.table)
    _print_json([r.model_dump() for r in results])


def cmd_inject(args: argparse.Namespace) -> None:
    func = INJECT_FUNCS.get(args.fault_kind)
    if func is None:
        sys.exit(f"Unknown fault_kind '{args.fault_kind}'. Choose from: {', '.join(INJECT_FUNCS)}")
    run_id = args.run_id or uuid.uuid4().hex[:12]
    entry = func(args.dataset, args.table, run_id=run_id)
    print(f"Injected {args.fault_kind} into {args.dataset}.{args.table}")
    print(f"run_id: {run_id}  (pass --run-id {run_id} to `sweep`/`eval` to group with this fault)")
    _print_json(entry)


def cmd_revert(args: argparse.Namespace) -> None:
    restore(args.dataset, args.table)
    print(f"Restored {args.dataset}.{args.table} from its pre-fault snapshot and dropped the snapshot.")


def cmd_eval(args: argparse.Namespace) -> None:
    if not args.run_id:
        sys.exit("`eval` requires --run-id (the id shared with your `inject` calls)")

    dataset = args.dataset
    tables = list_active_contract_tables(dataset)

    if not tables:
        sys.exit(f"No approved contracts found in {CONTRACTS_DIR} for dataset '{dataset}'")

    sweep_results_by_table = {table: execute_sweep(dataset, table) for table in tables}

    _print_json(evaluate_sweep_results(sweep_results_by_table, run_id=args.run_id))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pramaan", description="Pramaan data trust CLI")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help=f"BigQuery dataset (default: {DEFAULT_DATASET})")
    parser.add_argument("--run-id", default=None, help="Shared id correlating inject/sweep/eval into one run. `inject` generates one if omitted and prints it; `eval` requires it.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_propose = sub.add_parser("propose", help="Profile a table and draft a contract")
    p_propose.add_argument("table")
    p_propose.set_defaults(func=cmd_propose)

    p_approve = sub.add_parser("approve", help="Approve a draft contract")
    p_approve.add_argument("contract_id", help="dataset:table:version, as printed by `propose`")
    p_approve.add_argument("--by", help="Approver name (default: $USER)")
    p_approve.set_defaults(func=cmd_approve)

    p_sweep = sub.add_parser("sweep", help="Run the approved contract's rules against a table")
    p_sweep.add_argument("table")
    p_sweep.set_defaults(func=cmd_sweep)

    p_inject = sub.add_parser("inject", help="Inject a chaos fault")
    p_inject.add_argument("fault_kind", choices=sorted(INJECT_FUNCS))
    p_inject.add_argument("table")
    p_inject.set_defaults(func=cmd_inject)

    p_revert = sub.add_parser("revert", help="Restore a table from its pre-fault snapshot")
    p_revert.add_argument("table")
    p_revert.set_defaults(func=cmd_revert)

    p_eval = sub.add_parser("eval", help="Sweep every approved contract and score against ground truth")
    p_eval.set_defaults(func=cmd_eval)

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
