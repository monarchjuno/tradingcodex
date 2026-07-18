# Dataset and Calculation Runtime Contract

## Use progressive disclosure

1. Use `search_datasets` or `search_calculations` to obtain compact cards.
2. Read `get_dataset_manifest` or `get_calculation_run` only for shortlisted
   objects.
3. Use `profile_dataset` for bounded statistics or samples when suitability is
   still uncertain.
4. Use `materialize_dataset_slice` only after choosing exact columns, time
   bounds, and instrument filters.

Do not request canonical payloads merely to inspect availability. Treat the
SQLite/FTS catalog as a regenerable discovery projection, never as the source
of truth.

## Preserve Dataset provenance

- Record a reusable table or time series with `record_dataset_snapshot` only
  after its Source Snapshot exists and its schema, units, timezone, vintage,
  cutoff, adjustment policy, and license posture are known.
- Treat Dataset manifests and Parquet payloads as immutable content-addressed
  objects. Derive a new Dataset with explicit parent lineage for every semantic
  transformation.
- Never register transient search results, failed downloads, secrets, account
  state, positions, orders, approval data, or Investor Context as Datasets.
- Treat a missing or withdrawn payload as unavailable even when a historical
  card remains discoverable.
- Reuse a Dataset only when provider/query, schema, instrument identity,
  units/currency, timezone/frequency, adjustment policy, vintage/cutoff,
  freshness, and license terms fit the assignment.

Use only the Dataset column types accepted by the service: `string`, `bool`,
`int64`, `float64`, `date32`, `timestamp`, or `decimal128(p,s)` with valid
precision and scale. Do not guess JSON-schema labels such as `date`, `number`,
`float`, or `integer`. A materialized time filter requires a Dataset column of
type `timestamp` plus timezone-aware RFC 3339 `start` and `end` values. A
`date32` or string date column cannot be used as `time_column`; omit the time
selector or create a governed derived Dataset with a real timestamp column.

## Prepare and record calculations

- Declare calculation kind/version, script, normalized parameters, output
  schema, Dataset materializations, private-input hashes, and cutoff through
  `prepare_calculation`.
- Each declared input `kind` is exactly one of `dataset_slice`,
  `private_account`, `private_ledger`, or `private_portfolio`. A Dataset
  materialization uses `dataset_slice`; do not invent labels such as
  `dataset_materialization`.
- Accept automatic reuse only for a successful exact fingerprint match. Bind
  the new workflow's reuse Run and its source Run; never convert similarity
  search into automatic reuse.
- In prepared mode, read only declared materialized inputs and write only
  declared basename outputs under scratch.
- Call `tcx_emit_result()` exactly once with typed metrics, units, currency and
  precision where relevant, diagnostics, assumptions, warnings, and declared
  output files. Do not emit NaN, Infinity, pickle, joblib, or executable data.
- Call `record_calculation_run` for success and failure. Preserve bounded
  stdout/stderr hashes and failure diagnostics; only successful exact runs may
  be reused.
- Store tabular or time-series outputs as derived Datasets. Keep a Calculation
  Run card and summary compact; retrieve full diagnostics or logs only for
  review and reproduction.

### Emit the exact typed result

`tcx_emit_result` is a global injected by the prepared runner. Do not import it
from `tcx_calculation` or any other module, and do not call it with keyword
arguments. Copy this shape and keep metric order and declared metadata aligned
with `prepare_calculation.output_schema.metrics`:

```python
tcx_emit_result({
    "metrics": [
        {
            "name": "return_20d",
            "value": 0.125,
            "value_type": "number",
            "unit": "ratio",
            "currency": None,
            "precision": 6,
        }
    ],
    "diagnostics": {"observations": 21},
    "assumptions": [],
    "warnings": [],
    "output_files": [],
})
```

The top-level object may contain only `metrics`, `diagnostics`, `assumptions`,
`warnings`, and `output_files`. `metrics` must be a non-empty array. Every
metric must contain exactly `name`, `value`, `value_type`, `unit`, `currency`,
and `precision`, even when the last three are `None`. Use an exact finite string
for `decimal`; use ordinary finite JSON values for the other declared types.

### Correct and retry a failed run

1. Record the failed envelope with `record_calculation_run`; do not discard it.
2. Read the returned `error_code` and static `error_message`. Never infer a
   missing package from `ValueError` alone or expose input-bearing exception
   text.
3. Correct the indicated script contract, input schema/window, arithmetic, or
   declared I/O issue. Keep the method, governed inputs, and cutoff unchanged
   unless the error proves one of them invalid.
4. Stage the correction under a new `.py` basename, increment the calculation
   version when method code changed, call `prepare_calculation` again, execute
   the new generated command, and record the new Run.
5. Retry only after a concrete correction. If the same `error_code` repeats
   once, stop the loop, preserve both failed Runs, lower readiness, and hand off
   the exact code/message as `waiting`. Bind only a successful or exact-reused
   Run to a conclusion.

## Stay within the runtime boundary

- Use only the packages provisioned by the TradingCodex calculation runtime:
  NumPy, Pandas, SciPy, Statsmodels, NumPy Financial, PyArrow, and the Python
  standard library.
- Do not use `pip`, `uv`, network clients, data-provider SDKs, Django, MCP, the
  service runtime, the central DB, local/private services, or credential files
  from calculation code.
- Treat `tcx-calc` as a constrained calculation runner inside the Codex OS
  sandbox, not as a replacement security sandbox.
- Keep exploratory sidecar-free executions out of artifact evidence. Re-run
  through prepared mode and record the Calculation Run before using the result
  in a conclusion.
