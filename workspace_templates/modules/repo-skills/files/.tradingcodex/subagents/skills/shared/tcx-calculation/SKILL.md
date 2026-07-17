---
name: tcx-calculation
description: "Run reproducible financial calculations from governed Dataset slices or declared private inputs, reuse exact prior results, and bind decision-relevant metrics to recorded Calculation Runs. Use for returns, risk, valuation, regression, optimization, scenario, or portfolio math that can affect an investment artifact."
---

# Reproducible Financial Calculation

Use the smallest governed input and leave an auditable Calculation Run whenever
the result can affect a conclusion. Keep quick arithmetic that does not support
a conclusion explicitly exploratory.

## Follow the calculation workflow

1. Search Dataset and Calculation cards before fetching data or computing.
2. Inspect only the relevant manifest or run summary. Confirm source lineage,
   units, currency, timezone, adjustment policy, and `knowledge_cutoff`.
3. Reuse a prior result only when `prepare_calculation` reports an exact
   fingerprint match. Treat similar runs as references, not cached answers.
4. Materialize only the needed columns, instruments, and time range. Keep
   private portfolio or ledger inputs run-scoped; never register them as a
   Dataset or copy them into scripts, logs, or artifacts.
5. Create one direct basename-only `.py` file under
   `$TRADINGCODEX_SCRATCH` with native `apply_patch`. For a conclusion-relevant
   calculation, call `prepare_calculation` before execution and use only its
   declared inputs and outputs.
6. Run exactly `./tcx-calc <filename.py>` from the workspace root on POSIX or
   `.\\tcx-calc.cmd <filename.py>` on Windows. Do not invoke system Python,
   install packages, use heredocs, or pass `-c`, `-m`, paths, or extra args.
7. Emit one typed result with `tcx_emit_result()`. Record success or failure
   with `record_calculation_run`; do not retry silently or reuse failed runs.
8. Bind accepted `calculation_run_ids`, Dataset lineage, assumptions,
   diagnostics, and warnings into the role artifact. Do not cite an
   exploratory sidecar-free execution as decision evidence.

Use `search_datasets`, `get_dataset_manifest`, `profile_dataset`, and
`materialize_dataset_slice` progressively. Avoid loading complete payloads or
long calculation logs into context.

## Apply the quality floor

- Separate observed inputs, derived values, assumptions, and judgment.
- Preserve point-in-time posture; never substitute revised or current data for
  the requested vintage without disclosure.
- Make units, sign conventions, compounding, annualization, timing, missing
  values, and sample definitions explicit.
- Set deterministic seeds for stochastic work and report solver convergence,
  statistical diagnostics, and sensitivity where relevant.
- Reject NaN, Infinity, silent coercion, executable serialization, and results
  whose inputs or method cannot be reconstructed.
- Lower readiness when data quality, method assumptions, sample size,
  convergence, or sensitivity does not support the claim.

Read [references/finance-methods.md](references/finance-methods.md) before DCF,
IRR, return/risk, regression, optimization, or portfolio calculations. Read
[references/data-runtime.md](references/data-runtime.md) before registering,
materializing, reusing, or recording Dataset and Calculation objects.
