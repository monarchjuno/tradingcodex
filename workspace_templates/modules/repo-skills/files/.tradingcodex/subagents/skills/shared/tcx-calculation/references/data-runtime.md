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

## Prepare and record calculations

- Declare calculation kind/version, script, normalized parameters, output
  schema, Dataset materializations, private-input hashes, and cutoff through
  `prepare_calculation`.
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
