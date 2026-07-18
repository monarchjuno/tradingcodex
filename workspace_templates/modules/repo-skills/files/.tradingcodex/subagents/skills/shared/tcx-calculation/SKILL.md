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
7. Call the runner-injected `tcx_emit_result` global exactly once with one
   positional object. Never import it, pass keyword arguments, invent wrapper
   fields, or emit a metrics mapping. Copy the exact typed shape from
   [references/data-runtime.md](references/data-runtime.md).
8. Record success or failure with `record_calculation_run`. On failure, read
   its safe `error_code` and `error_message`, make one concrete correction,
   stage a new script basename, prepare a new immutable spec, and retry. Never
   overwrite a prepared script/result, repeat the same failed code unchanged,
   install packages, or reuse a failed Run. Stop and hand off as `waiting` if
   the same error code recurs after its targeted correction.
9. Bind accepted `calculation_run_ids`, Dataset lineage, assumptions,
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
