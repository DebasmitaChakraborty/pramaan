# Pramaan — Agentic Data Quality & Trust Framework

Pramaan profiles a BigQuery table, uses Gemini to draft a data-quality
contract grounded in the table's own observed statistics, and — once a human
approves it — enforces that contract with SQL sweeps. A chaos layer injects
realistic data faults into a demo dataset so the whole loop (draft → approve
→ sweep → detect) can be evaluated end to end instead of taken on faith.

## Architecture

| Module | Responsibility |
|---|---|
| `pramaan/profiler.py` | Profiles a table's schema + per-column stats (null rate, distinct ratio, p1/p99, timestamp recency, daily volume) and verified FK candidates, then asks Gemini to draft rules — every proposed rule is checked against an observed stat before being accepted; a threshold with no backing stat is rejected, not silently kept. |
| `pramaan/rules.py` | Typed rule models (`NullRateRule`, `UniquenessRule`, `RangeRule`, `FreshnessRule`, `RowCountDriftRule`, `ReferentialIntegrityRule`, `SetMembershipRule`, `RegexConformanceRule`, `SchemaConformanceRule`) and their compilation to a single `UNION ALL` SQL sweep. |
| `pramaan/contracts.py` | Draft → approve lifecycle with an immutable, versioned file store (`.contracts_store/`). Sweeps can only execute an *approved* contract, never a draft. |
| `pramaan/sentry.py` | Executes a table's approved contract: runs the compiled SQL sweep plus a separate schema-conformance check against `INFORMATION_SCHEMA.COLUMNS` (schema conformance isn't a per-row SQL metric). |
| `pramaan/chaos.py` | Snapshot/restore plus 8 fault injectors (`inject_*`) that corrupt a copy of the demo data and log a ground-truth entry (expected table + rule type) before injecting, so detection can be scored honestly. |
| `pramaan/evals.py` | Scores sweep violations against ground truth (precision/recall, TP/FP/FN) and runs the isolated fault matrix (see below). |
| `pramaan/cli.py` | Thin CLI over all of the above: `propose`, `approve`, `sweep`, `inject`, `revert`, `eval`, `eval-matrix`. |
| `pramaan/server.py` | FastAPI service (`/health`, `/contracts`, `/sweep`, `/diagnose`) deployed to Cloud Run. |

## Setup

```bash
pip install -e .
export GCP_PROJECT_ID=pramaan-506517
python scripts/bootstrap.py build   # (re)builds pramaan_demo.{orders,users,products,order_items}
                                      # from bigquery-public-data.thelook_ecommerce,
                                      # which is continuously updated -- re-run this
                                      # whenever the demo slice has gone stale.
pytest                               # 26 unit tests, no BigQuery access required
```

## CLI

```bash
python -m pramaan.cli propose orders                 # draft a contract from live stats
python -m pramaan.cli approve pramaan_demo:orders:4   # promote a draft to approved
python -m pramaan.cli sweep orders                    # run the approved contract once
python -m pramaan.cli inject null_flood users          # inject one fault (prints a run_id)
python -m pramaan.cli revert users                     # restore from the pre-fault snapshot
python -m pramaan.cli eval-matrix                      # run the full isolated fault matrix
```

## Rule design notes

**`row_count_drift` is anchored to the data, not the wall clock.** It takes
the latest *complete* calendar day present in the timestamp column —
`MAX(DATE(column))` minus one day, never `MAX(DATE(column))` itself, because
the single most recent day present is always partial (still being written to
on a live table; a trailing slice on a frozen snapshot) — and compares that
day's row count to the average of the days immediately before it:
`metric = latest_complete_day / avg(prior_N_days)`. A ratio near 1.0 is
normal; a ratio near 0 means that day's load came in far short. This is
correct on both a live, continuously growing table and a static snapshot,
which an anchor to `CURRENT_DATE()` is not (a frozen snapshot may have no
data for "today" at all).

**A rule with no supporting window doesn't crash the sweep.** If a rule's
window has nothing to compare against (e.g. `row_count_drift` on a table
with fewer than `N+1` days of history), BigQuery returns `NULL` for that
rule's metric. `sentry._row_to_sweep_result` turns that into
`is_violation=True, detail="insufficient data for rule"` — "we can't tell" is
never a silent pass, and one rule with no data can't fail the whole table's
sweep with a pydantic validation error.

**Freshness (`orders.created_at`, 25h) is a real check on a real staleness
problem, not a threshold to be padded.** Investigating this contract, the
rule was found tripping at baseline (158h stale) because `pramaan_demo.orders`
is a frozen snapshot copied on 2026-09-03 — six days before this work, well
past the 25h window. The fix was **not** to loosen the threshold; it was to
re-run `scripts/bootstrap.py`, since the underlying `thelook_ecommerce`
public dataset is continuously updated. After the refresh, `hours_since_max`
was 15h, comfortably under 25h. The threshold was correctly reporting a
stale demo, not a broken rule.

## Fault matrix: isolation, not batching

Earlier fault-matrix runs (see git history) injected all faults for a run
back to back and evaluated once at the end. That's a bug: faults sharing a
table (e.g. `orders`) stacked on top of each other with no restore in
between, so a later fault could destroy the evidence of an earlier one — a
`row_count_collapse` deleting 90% of rows after a `silent_duplicate_load` had
inserted duplicates, for instance, can coincidentally wipe out every
surviving duplicate pair and turn a real detection into a false miss.

`evals.run_fault_matrix` (via `pramaan eval-matrix`) fixes this by running
each fault in full isolation: **inject → sweep → score → restore**, before
the next fault starts. No two faults are ever live on the same table at the
same time. `evals.run_isolated_fault` does the single-fault version of this
and is covered by a test asserting the table's row count equals the
snapshot's row count the instant the *next* fault's inject call fires
(`tests/test_evals.py`) — i.e. restore actually ran, and ran before the next
injection, not after.

`chaos.inject_row_count_collapse` was also fixed as part of this: it
previously deleted 90% of rows uniformly across the *whole table's history*
(`WHERE RAND() < 0.9`, no date filter). Since `row_count_drift`'s metric is a
day-over-day *ratio*, a uniform proportional shrink across every day leaves
that ratio unchanged — numerator and denominator shrink together — so the
old fault could never actually trip the rule it was supposed to test. It now
deletes 90% of rows from the latest *complete* day only, which is the actual
"one day's incremental load came in short" failure mode the rule exists to
catch.

The old whole-table deletion wasn't a bad idea, just a different failure
mode (a mass historical deletion / bad backfill, not a short load) that no
current rule covers. Rather than discard it, it's kept as its own fault,
`inject_historical_mass_delete`, logged against `expected_rule_type: "none"`
— an honest, documented detection gap rather than a rule bug.

### Latest isolated run (`f0ddcd46f92b`, [`results/eval_f0ddcd46f92b.json`](results/eval_f0ddcd46f92b.json))

| Fault | Table | Expected rule | Caught |
|---|---|---|---|
| silent_duplicate_load | orders | uniqueness | ✅ |
| stalled_partition | orders | freshness | ✅ |
| row_count_collapse | orders | row_count_drift | ✅ |
| historical_mass_delete | orders | *(none — documented gap)* | ❌ (by design) |
| currency_swap | order_items | range | ✅ |
| referential_orphan | order_items | referential_integrity | ✅ |
| schema_drift | order_items | schema_conformance | ✅ |
| null_flood | users | null_rate | ✅ |

**TP=7, FP=0, FN=1 · precision=1.0 · recall=0.875 (7/8)**

All 7 faults that a contract rule is meant to catch were caught, with zero
false positives, under full per-fault isolation. The one miss is the
intentionally uncovered `historical_mass_delete` gap described above, not a
detection failure.

## Baseline

With no faults injected, all three tables' approved contracts sweep clean:
`orders` (15 rules, v4), `users` (1 rule), `order_items` (3 rules) — 0
violations. Verified after the `scripts/bootstrap.py` refresh described
above.

## Deployment

```bash
bash infra/deploy.sh   # gcloud builds submit + gcloud run deploy (Cloud Run, public)
```

Service: `pramaan-engine` (`us-central1`), public (`--allow-unauthenticated`).
The container image bundles `.contracts_store/`, so a contract approved
locally isn't live until the next deploy.
