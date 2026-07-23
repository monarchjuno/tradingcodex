# Research Memory And Artifacts

This document owns TradingCodex research handoff memory, source/as-of posture,
artifact paths, report quality floor, the two readiness axes, and artifact contracts.
Research memory is part of the harness Improvement subsystem; see
[improvement-loop.md](./improvement-loop.md). Ledger-first decision episodes,
historical replay, lessons, strategy snapshots, and generated Wiki/graph views
are defined in [decision-memory.md](./decision-memory.md).

Agent-maintained reusable background knowledge is not Research Memory. It lives
under `wikis/` and follows [Knowledge Wikis](knowledge-wikis.md). Research
completion never auto-promotes an Artifact, Snapshot, or Dataset into that
Wiki; explicit promotion reads the source without modifying or binding it.

## File-Native Research Memory

Research handoff memory is workspace-file-native. Codex agents and humans must
be able to inspect the same markdown files without requiring hidden DB state.

Canonical research files live under:

- `trading/research/*.md`
- `trading/research/*.evidence.md`
- `trading/reports/<role>/*.md`

The service layer may index, validate, search, preview, version, and write
these files, but the file is the source of truth for handoff-ready research.
If a workspace has no matching markdown file, the product web and CLI should
not pretend a canonical research artifact exists only because a DB row exists.

Markdown bodies may be prepared under `trading/research/.drafts/` and supplied
inside the v2 JSON request accepted by `tcx research create`. Drafts are
deliberately excluded from the research index until the service writes the
canonical artifact envelope. Indexed research markdown must declare non-empty
`artifact_id`, `artifact_type`, and `universe` fields. The one exception is a
Head Manager synthesis: the service derives its id and canonical path from the
analysis run.
Markdown run cards and validation cards are validated by their own card
contracts and are not research-index entries.

Non-artifact research freshness records are also file-native:

- `trading/research/source-snapshots/*.json` for provider/as-of/retrieved metadata
- `trading/research/datasets/manifests/*.json` for immutable Dataset metadata,
  source bindings, schema, quality, lineage, license, retention, and exact
  payload hashes
- `trading/research/datasets/objects/<payload-sha256>.parquet` for canonical
  table payloads; these are permanent-local workspace evidence but are ignored
  by the managed Git privacy block
- `trading/research/datasets/withdrawals/*.json` for append-only user-directed
  license or legal withdrawal events
- `trading/research/calculations/specs/*.json` and
  `trading/research/calculations/runs/*.json` for immutable CalculationSpec and
  CalculationRun records; prepared sidecars and result envelopes remain in
  scratch
- `trading/research/specs/*.json` for immutable ResearchSpec records
- `trading/research/replay-manifests/*.json` for frozen point-in-time evidence
  sets
- `trading/research/experiments/*.json` for immutable ExperimentRun records
- `trading/research/analyses/*.json`, `judgment-priors/*.json`, and
  `judgment-reviews/*.json` for deterministic causal valuation and two-pass
  independent review
- `trading/research/.index/research-object-catalog-v3.sqlite3` for the current
  rebuildable structured and FTS research-object projection
- `trading/research/.index/research-index.json` and
  `trading/research/.index/artifact-catalog-v3.json` for rebuildable
  compatibility exports emitted from the same projection rules; files remain
  canonical
- `trading/evaluations/{corpora,runs,blind-review-assignments,blind-reviews,comparisons}/*.json`
  for the frozen model-upgrade evaluation lab
- `*.run-card.json` beside research artifacts, reports, decision packages, or
  other workflow artifacts for reproducibility metadata
- `*.validation-card.json` beside research artifacts, reports, decision
  packages, or other workflow artifacts for evidence-quality validation
  metadata
- `trading/forecasts/*.jsonl` for append-only forecast ledger records
- `trading/decisions/*.judgment-snapshot.json` for frozen evaluable synthesis,
  `*.decision-adoption.json` for explicit immutable user-adoption events, and
  legacy `*.decision-snapshot.json` records retained read-only
- `trading/reports/postmortem/*.postmortem_report.json` for outcome-separated
  episode review and lesson candidates

Research artifact creation, source snapshot recording, search, get, list, and
export do not create Django research model rows or research-owned DB tables.
They also do not write `AuditEvent` or `McpToolCall` rows. Research MCP calls
are intentionally excluded from the DB call ledger so markdown, frontmatter,
source metadata, and payloads stay in workspace files only.
The same file-only call-ledger exclusion applies to evaluation tools and their
research-only corpora/run/review/comparison payloads.

Wiki pages, temporal graphs, claim indexes, similarity links, and dashboards
are rebuildable read projections over these files. They are never canonical
research or decision memory. A derived view must retain source artifact ids and
hashes and may not erase contrary, superseded, retired, or failed cases.

## Compact Artifact v2

New writes use a small common frontmatter envelope. It contains identity
(`artifact_schema_version`, id, type, title, universe, optional symbol), run
(`workflow_run_id`, producer, version, recorded time, cutoff), status, one
shared `summary`, canonical input/Snapshot/Dataset/Calculation IDs, inherited
requirements, gaps, and body `content_hash`. Empty optional values are omitted.

Readiness has two independent axes:

- evidence: `factual`, `screen`, `decision-grade`, or `insufficient`;
- action: `research-only`, `portfolio-review`, `draft-eligible`, or `blocked`.

Portfolio review and draft eligibility require decision-grade evidence. Draft
eligibility additionally requires accepted portfolio and risk inputs plus an
applicable Investor Context. `handoff_state: accepted` means the report is a
complete handoff; it never grants portfolio, order, approval, or execution
authority. Confidence is `low`, `medium`, or `high` and always has a separate
basis. Requirements are the inherited union of `decision_quality`, `forecast`,
`investor_context`, and `anti_overfit` and cannot be lowered.

Optional compact blocks are `decision_quality`, `memory`, `forecast`,
`valuation`, `anti_overfit`, and `follow_up_requests`. Detailed scenarios,
contrary evidence, trust explanation, and analysis belong in Markdown or the
owning reviewer artifact. Actual probabilities, base rates, resolution, and
scores live only in the Forecast ledger. Improvements live in Judgment Review
or Postmortem, not generic artifact metadata.

Receipt v4 owns what frontmatter intentionally omits: exact input versions and
hashes, Snapshot/Dataset/Calculation hashes, reuse origin, sealed Strategy,
Investment Brain and Investor Context lineage, creator/role authentication, and
Memory reference hashes. The writer normalizes one internal envelope, validates
lineage, renders compact Markdown, creates the receipt from the internal
envelope, records the receipt, and atomically replaces the stable Markdown.
Verification combines Markdown identity/body hash with the receipt. Existing
v1 Markdown, archives, and signed receipts are never rewritten or re-signed;
public reads adapt them into the v2 response shape.

A canonical Head Manager synthesis uses
`synthesis-<workflow_run_id>` at
`trading/reports/head-manager/synthesis-<workflow_run_id>.md`. The service
derives both values and requires at least one verified accepted run-local input.
Corrections append a version to the same run identity. A materially different
cutoff, run context, or evidence basis starts a new run.

The Markdown body remains professional free-form analysis. A synthesis is a
point-in-time integrated judgment, not a role report digest: it directly
answers the question, connects load-bearing evidence to implications and
judgment, preserves conflicts and uncertainty, defines update and invalidation
triggers, and states readiness and allowed next steps. The service validates
structured lineage and gates, not prose headings or keywords.

## Source Snapshot Discipline

Search results are discovery aids, not evidence records. Before extracting a
precise fact or storing a populated Source Snapshot, the producing role opens or
fetches the cited primary page, filing, release, dataset, or provider record. If
direct retrieval is blocked, the artifact preserves the locator and retrieval
gap with `screen` evidence readiness instead of promoting a search snippet into a
precise payload.

Market-sensitive and source-sensitive claims should record:

- source name/provider
- source URL or stable locator where available
- retrieval timestamp
- as-of date/time
- publication date/time when relevant
- content hash or request/result hash where practical
- coverage limitations
- stale/missing warnings
- data-boundary warnings for OHLC invariant failures, non-positive prices,
  duplicate timestamps, sparse or missing bars, timezone/as-of ambiguity,
  adjusted versus unadjusted price ambiguity, stale sources, invalid JSON
  constants such as NaN/Infinity, and explicit source fallback policy gaps

Content-addressed source snapshots also record stable locator and provider
query, observed/effective/published/retrieved/known-at timestamps, timezone,
revision and vintage, schema and payload hashes, corporate-action and price
adjustment policy, point-in-time universe membership, delisting policy, and
coverage/licensing limitations. Every snapshot uses `schema_version: 1`, has a
non-empty `snapshot_id`, and carries verified payload and envelope hashes.
`known_at`, `retrieved_at`, `recorded_at`, and `system_recorded_at` are required,
timezone-aware timestamps and must satisfy `known_at <= retrieved_at <=
recorded_at <= system_recorded_at`. The writer and all research-spec,
forecasting, and investment-analysis consumers enforce this same snapshot contract.
An artifact or replay manifest rejects a snapshot that does not exist or whose
`known_at` is later than its declared `knowledge_cutoff`.
Source-specific point-in-time evidence also preserves the identity needed to
reconstruct what was actually knowable. Filing-derived aggregates retain the
underlying filing or disclosure identifier, accepted/published time, form,
fiscal period, units, and amendment posture; a current company-facts or
calendar-frame view is only screening evidence unless the exact cutoff-valid
filing is bound. Historical macro work binds a first-release, vintage, or
real-time-period observation when available. A currently revised series is
hindsight evidence and cannot silently stand in for the historical value.
Artifact-producing agents therefore use a full timezone-aware RFC 3339
`knowledge_cutoff`. It must cover the maximum service-returned snapshot
`known_at` and bound Dataset `knowledge_cutoff`, preferably their exact maximum.
Date-only cutoffs are invalid;
rejection reports the exact conflicting lineage timestamp so the caller can
correct the contract without guessing.
The service also rejects an artifact cutoff later than its service-owned
`recorded_at`. Agents never expand a date request to an unobserved end-of-day or
other future time; when neither a snapshot nor an exact current timestamp gives
a defensible bound, they omit the optional cutoff.

Normal API and MCP callers omit `snapshot_id`, `retrieved_at`, and
`recorded_at`. The service records receipt/storage time and derives a bounded,
portable ID. Its response returns the exact `known_at`, `retrieved_at`,
`recorded_at`, and `system_recorded_at` values alongside that ID, so downstream
artifacts can bind the service-owned cutoff without inspecting files or
guessing. A caller supplies `known_at` only when the evidence's actual
knowable time is supported by the source or retrieval tool and includes an
explicit timezone; otherwise it is derived from receipt time. Explicit
timestamp overrides remain available for controlled replay/import paths, but
naive, future, and misordered values fail rather than being repaired or ignored.

These time fields are independent: `as_of`, `observed_at`, `published_at`, and
`known_at` are never substituted for one another. V2 artifact readiness comes
from `status.evidence_readiness` and `status.action_readiness`; lineage cutoff
comes from `lineage.knowledge_cutoff`. The authenticated principal becomes the
single stored `producer_role`.

Research quality focuses on source/as-of discipline, retrieved-at metadata,
stale-data warnings, versioning, and invalidation rather than long-lived
embedding memory.

## SourceSnapshots And Datasets

A SourceSnapshot is the canonical record for every external source: its provider,
locator, timing posture, hashes, warnings, payload, and necessary excerpt.
Filings, news, IR material, HTML/PDF extracts, and other unstructured evidence
normally need only this record.

A Dataset is optional and exists only for reusable tables, time series, OHLCV,
or financial tables. `record_dataset_snapshot` promotes a validated
scratch-local CSV, JSONL, or Parquet file into an immutable Dataset and preserves
the rows used by the analysis. Its manifest binds payload and source lineage.

A ResearchArtifact cites exact `source_snapshot_ids` and `dataset_ids`; the
service derives their hashes and validates the artifact cutoff against their
known-at and Dataset cutoff values. There is no DataNeed, external-call receipt,
or service-side fallback state to bind. Source routing and any repeat avoidance
are the concise `tcx-source-gate` skill procedure documented in
[data-sources-and-openbb.md](./data-sources-and-openbb.md).

## Immutable Dataset Layer

Do not create both objects merely to duplicate a small, non-tabular response;
keep that evidence in the SourceSnapshot payload instead.

`record_dataset_snapshot` accepts one basename-only CSV, JSONL, or Parquet file
already staged in the role's private scratch directory. The service requires an
explicit typed column contract and source lineage, then validates the file as a
single-link regular file, rejects path traversal, symlinks, hard links,
oversized inputs, schema mismatch, non-finite JSON values, and invalid point-in-
time ordering. The service, not the agent, derives the Dataset id, payload hash,
manifest hash, recorded timestamps, and creator. Repeating the same semantic
input is idempotent; an existing id can never be overwritten with different
content.

Canonical table payloads use the package-pinned PyArrow writer with UTC
timestamps, stable column ordering, no implicit dataframe index, and ZSTD
compression. The payload filename is its SHA-256. The JSON manifest records:

- exact Source Snapshot ids and hashes, provider and provider query;
- `as_of`, observed/published/known-at, vintage, knowledge cutoff, timezone,
  `period_start`/`period_end`, and frequency posture;
- instrument ids and symbols;
- ordered column name, type, nullability, unit, and currency metadata;
- point-in-time `universe_membership_policy`/membership plus adjustment,
  corporate-action, and delisting policy;
- row count, byte size, null counts, duplicate count, quality warnings, and the
  exact Parquet/PyArrow identity;
- parent Dataset lineage and transformation-code hash when applicable; and
- data classification, redistribution notes, and retention policy.

The normal retention policy is `permanent_local`. Dataset payloads stay in the
workspace but the managed `.gitignore` excludes
`trading/research/datasets/objects/`; manifests and withdrawal events remain
reviewable and versionable. Agents have no delete tool. Only an explicit user
purge for a license or legal reason may append a withdrawal event and remove an
otherwise-unreferenced payload blob. Shared blobs remain while another active
manifest still references them, and a missing or withdrawn payload remains
visible as unavailable rather than making the historical manifest disappear.

`get_dataset_rows` reads immutable payloads in selector-bound pages of at most
120 rows; a cursor cannot be reused with different columns or time filters.
`export_dataset_csv` is non-destructive and works only when the manifest's
redistribution field explicitly permits export. `get_source_snapshot` returns
metadata by default and a bounded payload only on request. These surfaces let a
later task reconstruct all rows actually used without carrying raw provider
output through every role brief.

Portfolio, account, position, order, approval, Investor Context, and other
sensitive central-ledger state are not copied into Dataset manifests or
payloads. A private calculation binds only the service-issued, DB-canonical
ledger snapshot id and hash. Preparation and recording both revalidate that
binding against the current central ledger. Its materialized values stay
run-scoped in scratch and must not be copied into Dataset, script, artifact, or
calculation metadata.

## Progressive Dataset Retrieval

Agents retrieve reusable data in increasing-cost layers:

1. `search_datasets` returns at most 20 L0 cards by default and 200 when
   explicitly requested. A card contains identity, title/summary, tags,
   source/cutoff posture, symbols, status, warnings, and payload availability;
   it never contains table rows or a long schema.
2. `get_dataset_manifest` returns metadata and lineage without reading the
   payload. Use it to verify source, vintage, units, policy, and suitability for
   the current cutoff.
3. `profile_dataset` reads only the requested columns, returns structured
   statistics, and includes no more than 20 sample rows. Text cells in the
   bounded sample are truncated after 512 characters.
4. `materialize_dataset_slice` accepts typed columns, time bounds,
   instrument/symbol filters, and an output basename. It never accepts arbitrary
   SQL. The default hard ceiling is 1,000,000 rows or 256 MiB; a broader request
   fails with guidance to narrow the selector.

A materialized slice is a scratch-local Parquet file. The tool response returns
only its materialization id, Dataset id, typed selector, row count, and content
hash. The slice is not a second durable Dataset unless an eligible analysis role
deliberately records it with complete lineage. This card-to-manifest/profile-to-
slice flow keeps raw rows out of Head Manager and downstream context until a
role has a concrete analytical need.

The service writes an immutable HMAC-authenticated materialization proof beside
the slice. Calculation preparation accepts a Dataset slice only when that proof,
materialization id, current manifest/payload hashes, file identity, and full
recursive Source Snapshot/parent-Dataset lineage still agree. Recording repeats
those checks and rejects a slice whose Dataset was withdrawn, payload changed,
or lineage became unavailable after preparation.

## Calculation Memory

Conclusion-relevant numerical work is an immutable pair: a CalculationSpec
prepared before execution and a CalculationRun recorded from the runner's
result envelope. The workflow is:

1. Search Dataset and Calculation cards before fetching rows or computing.
2. Materialize only the Dataset slice or private ledger input required by the
   role-owned question.
3. Stage one scratch-local Python script.
4. Call `prepare_calculation` with the calculation kind/version, script,
   declared inputs, canonical parameters, cutoff, and typed output schema.
5. If no exact prior success exists, run the unchanged generated command
   `./tcx-calc <script.py>` or `.\tcx-calc.cmd <script.py>` on native Windows.
6. Call `record_calculation_run` with the prepared identity and bounded runner
   envelope, including after a failed execution. On failure, use the returned
   safe `error_code` and static `error_message` to make one concrete correction,
   stage a new script basename and CalculationSpec version, prepare again, and
   retry. Do not overwrite the failed attempt or repeat it unchanged; if the
   same code recurs after the targeted correction, stop and preserve the
   evidence for review. Declared tabular/time-series outputs are recorded as
   derived Datasets with parent/transformation lineage. Bind only a successful
   or reused Run id into a conclusion.

Preparation hashes the code, Dataset slices and private inputs, canonical
parameters and output schema, Python and package lock, OS and architecture, and
numerical-library/BLAS-LAPACK posture. Only a successful Run with the complete
same fingerprint is reusable. An exact hit still creates a current-workflow
`status: reused` Run linked to the original successful Run so provenance is
local to the current analysis. A one-row, one-parameter, cutoff, code, schema,
runtime, platform, or numerical-backend change is a cache miss. Similar
fingerprints may be displayed as comparison candidates but are never
automatically substituted.

Prepared execution uses a service-authored runner sidecar. It permits only the
declared scratch-local input files, the declared output basenames, the pinned
runtime, stdlib, and required system libraries. The runner supplies one
injected `tcx_emit_result` global; scripts must not import it and must call it
exactly once with one positional object. That object permits only `metrics`,
`diagnostics`, `assumptions`, `warnings`, and `output_files`. Every metric is an
object with exactly `name`, `value`, `value_type`, `unit`, `currency`, and
`precision`, aligned to the schema returned by preparation. NaN, Infinity,
pickle, joblib, executable serialization,
undeclared file access, and undeclared output are rejected. Success and failure
both retain bounded stdout/stderr byte counts and hashes. Failed Runs expose a
runner-authored code and service-owned static guidance rather than raw exception
text or input values; only success is eligible for reuse.

Direct `tcx-calc` without a prepared sidecar remains an exploratory
compatibility mode. It is useful for investigation, but its output cannot
support an accepted artifact and is never entered into Calculation Memory.
Likewise, only calculations that affect a conclusion must be recorded; scratch
exploration is intentionally disposable.

## MCP And CLI Research Tools

Codex and subagents should use service-layer tools when available:

- `create_research_artifact`
- `get_research_artifact`
- `list_research_artifacts`
- `search_research_artifacts`
- `append_research_artifact_version`
- `export_research_artifact_md`
- `record_source_snapshot`
- `search_datasets`, `get_dataset_manifest`, and `profile_dataset`
- `record_dataset_snapshot` and `materialize_dataset_slice`
- `search_calculations`, `get_calculation_run`, and
  `compare_calculation_runs`
- `prepare_calculation` and `record_calculation_run`
- `rebuild_research_index`
- `list_artifact_catalog`, `search_artifact_catalog`, and
  `rebuild_artifact_catalog`
- `create_research_spec`, `get_research_spec`, and `list_research_specs`
- `create_replay_manifest`
- `record_experiment_run`
- `create_causal_equity_analysis`, `record_blind_judgment_prior`, and
  `complete_judgment_review`
- `issue_forecast`, `revise_forecast`, `resolve_forecast`, and `score_forecast`
- `get_forecast`, `list_forecasts`, and `get_forecast_calibration_report`
- `create_evaluation_corpus`, `record_evaluation_run`,
  `create_blind_review_assignment`, `get_blind_review_packet`,
  `record_blind_human_review`, and `compare_evaluation_runs`

For forecast issue and revision, agents normally omit `issued_at` and
`revised_at`. The service assigns the event time and `recorded_at` from one
receipt timestamp, avoiding client-clock drift and preserving the invariant
that an event cannot occur after its system record. Explicit caller timestamps
remain timezone-aware and fail closed when they are in the future.

`source_snapshot_ids` are service-issued references, not agent-authored labels.
When reproducibility requires a snapshot, the producing role calls
`record_source_snapshot` first and copies only the exact returned `snapshot_id`
into the artifact. It must not predict an id from provider, URL, payload, or a
local naming convention. If no snapshot was recorded, the list is empty and
the artifact preserves the stable source locator, retrieval posture, and any
coverage gap instead. Artifact creation fails closed when a referenced snapshot
does not exist, its content-addressed id no longer matches its current
envelope, its current hash differs from the authenticated artifact binding, or
it violates the recorded knowledge cutoff. Callers never author
`source_snapshot_hashes`; the service derives them from the exact accepted
snapshot files and seals them into the artifact and HMAC receipt.

`tcx quality-check <artifact-path>` gives a friendly diagnostic pass/fail for
empty files and invalid JSON while surfacing warnings for weak research
metadata and context size. For v2 research markdown, strict validation checks
the compact envelope, non-empty `summary`, both readiness axes, confidence and
its basis, requirements, lineage, and body hash. Empty optional fields and
receipt-only hashes are omitted. Legacy v1 artifacts retain their original
strict checks and are never rewritten merely to satisfy the v2 contract.

Forecast ledger records under `trading/forecasts/forecast-ledger.jsonl` are
managed as immutable issue, revision, resolution, and score events. Binary,
categorical, and continuous targets use distinct prediction/base-rate shapes.
Issuance requires a point-in-time base-rate source, evidence and contrary
evidence, update and invalidation conditions, a resolution rule, model/tool
provenance fields when available, and a timezone-aware knowledge cutoff.
Scoreable binary forecasts should carry a point probability; an optional range
may express uncertainty around it. Range-only binary records remain exploratory
diagnostics and do not contribute to proper-score calibration.
The horizon is normalized to a UTC date or datetime and must not precede
issuance. Only the author may revise an open forecast; revisions preserve
earlier events and neither revision time nor knowledge cutoff may move backward.
The resolver must differ from the author, resolution cannot precede the
horizon, and the cited source snapshot must become known between observation
and resolution. Disputed outcomes are not scored until an explicit
`resolve_dispute` event supersedes the disputed resolution.

Scoring uses Brier and log scores against the base rate for point binary and
categorical targets, and scale error, interval coverage/width, interval score
when nominal coverage is declared, or quantile loss for continuous targets as
applicable. A range-only binary forecast records Brier bounds as a diagnostic;
it is not labeled a proper point forecast and is excluded from calibration.
Quantile values must be noncrossing. Calibration statistics are withheld until
at least 20 eligible scored binary point forecasts exist by default, then show
Wilson intervals and stratification by role/model, horizon, universe, and
regime. Forecast and calibration artifacts remain `evidence_only`; they do not
trigger workflows or authorize portfolio, order, approval, or execution
actions.

Historical replay, historical holdout, and live forward evidence remain
separate labels and metrics. A replay begins with an immutable ResearchSpec and
cutoff, freezes its decision and forecast before revealing the next period, and
then resolves and reviews the episode. A result reconstructed from history does
not become live-forward evidence, even when the replay is point-in-time clean.
For a current decision, preserve an independent initial view before retrieving
similar prior episodes so memory does not silently become an anchoring prior.
The initial view may use one explicitly selected Investment Brain as an inquiry
and interpretation overlay, but it must still be grounded in authenticated
current-run evidence. After memory retrieval, preserve the pre-memory view and
record the explicit decision delta.

## ResearchSpec, Replay, And Experiment Gates

An empirical or decision-ready prediction workflow begins with an immutable
ResearchSpec under `trading/research/specs/`. Every spec freezes its method
profile, hypothesis, economic mechanism, falsifiers, point-in-time universe
rule, target/horizon, validation plan, resolution rule, knowledge cutoff, and
analysis-plan hash. The bundled profiles keep method-specific fields separate:

- `general_evidence_v1` covers qualitative and general evidence research;
- `event_research_v1` covers time-bounded event research;
- `quant_signal_v1` additionally requires a benchmark, signal definition,
  parameter-trial budget, costs, capacity, and the full typed anti-overfit
  validation stack; and
- `listed_equity_fcff_dcf_v1` additionally requires a driver tree, defined
  base-rate cohort, implied-expectations plan, coherent FCFF scenario drivers,
  method reconciliation, and independent review.

These are bundled method adapters, not external skill dependencies. General or
event research is not forced through signal/backtest fields, and the FCFF
profile is not a universal listed-equity valuation rule.

A replay manifest binds that spec and cutoff to exact content hashes of source
snapshots that were knowable at the cutoff. It fails closed when required
point-in-time metadata is absent, when semantic source dates exceed the cutoff,
or when known/retrieved/recorded chronology is inconsistent. A causal base-rate
cohort's `as_of` must also be timezone-aware and no later than the spec cutoff.
Market-data replay additionally requires explicit adjustment, corporate-action,
delisting, and historical universe policies.

An ExperimentRun binds immutable spec, method-profile, and replay hashes to
code, data, config, model/prompt/tool hashes, data splits, metrics, trial count,
and evidence-backed validation checks. Checks use `pass`, `fail`, or
`not_applicable`, each with a reason and existing SHA-256-bound evidence refs.
Only `quant_signal_v1` applies the fixed anti-overfit check set, cumulative
parameter-trial budget, and typed conclusions `keep_researching`,
`conditionally_promising`, `likely_overfit`, `implementation_weak`, or
`reject`. Other profiles retain their declared evidence checks and conclusion
without inheriting a quant-signal contract. This validates manifests and
evidence; it is not an embedded backtest engine or proof that reported external
metrics were recomputed.
The data-snooping check records the observed or defensible effective trial
count, candidate-selection rule, chronological split, holdout exposure, and
multiple-testing adjustment. Reusing a holdout turns it into training feedback.
Probability-of-overfitting, reality-check, or deflated-Sharpe statistics are
useful only when the required inputs and assumptions are supported; none is a
universal prerequisite for non-quant research.

Validation Cards use the same typed evidence posture. `not_assessed` never
supports an `evidence_quality_label` of `validated`; validated cards require
every check to be `pass` or justified `not_applicable` with evidence refs.

## Causal Equity Analysis And Independent Review

For a `listed_equity_fcff_dcf_v1` ResearchSpec, the valuation analyst can run
the bundled `fcff_revenue_margin_dcf_v1` deterministic causal-analysis method.
It binds to the frozen spec and replay manifest, keeps all arithmetic in
`Decimal`, solves reverse-DCF implied revenue growth, calculates coherent
weighted forward scenarios, and preserves the spread between reverse DCF,
forward DCF, and any source-cited additional methods. It emits its protocol,
method id, formula, input, scenario-config, and source-snapshot hashes instead
of treating language-model arithmetic as evidence. Questions or instruments
for which that method does not fit must select another profile or report a
support gap.

High-impact review is two-pass. `judgment-reviewer` first records a blind prior
against the specification, evidence quality, drivers, and falsifiers without
the producer's final analysis. A second review binds that prior to the causal
analysis and records changed views, remaining disagreements, and
`accepted`/`revise`/`blocked`. The reviewer must differ from the ResearchSpec
owner. Both artifacts remain evidence-only and cannot grant downstream action
authority.

## Frozen Model-Upgrade Evaluation Lab

The evaluation lab stores research-only corpora, control/candidate runs, blind
human reviews, and comparisons under `trading/evaluations/`. The bundled
`core_investment_v1` profile requires the documented point-in-time,
null-signal, overfit, cost/capacity, hidden-factor, scenario,
malformed/revised forecast, conflicting-source, multilingual-negation, and
paired-model replay cases. A corpus-defined profile may instead declare its own
required case tags and metric dimensions for bounded non-quant or specialized
evaluation. Each case binds to a replay-manifest hash and forbids order,
approval, and execution actions.

Runs immutably store model/effort and caller-attested
prompt/config/tool/calculation/extension hashes alongside every frozen case,
deterministic check, verified workspace artifact, metric dimension, operations
data, and budget. Aggregate metrics and hard-failure records are mechanically
derived from the complete set of unique per-case values rather than accepted as
caller summaries, but the truth of current check booleans and environment
digests is not yet trusted-runner-verified. Promotion criteria are frozen after
baseline measurement, and each required case tag must have distinct-case
coverage.

Control and candidate comparison requires identical effort, prompt, config,
tool-profile, deterministic-calculation, extension-profile, and budget hashes.
Reported unregistered extension use maps to a hard failure, but its absence
cannot prove pristine execution while runtime discovery and invocation traces
remain unverified. Blind review begins
with a head-manager assignment that randomly maps the two runs to opaque A/B
labels, snapshots identity-redacted UTF-8 artifact content, and binds the packet
to one active authenticated `judgment-reviewer` principal. Review submissions
must follow the assignment chronologically, cannot override reviewer identity in
their payload, and at least two distinct assigned principals are required.
Comparison then requires the exact same corpus, zero candidate hard
safety/evidence/scope/tool failures, passing deterministic checks, metric
non-inferiority, independent blinded human review, and trusted-runner
provenance. Current caller-attested runs always produce `hold`, even when every
other criterion passes. Creating the harness does not itself establish a
GPT-5.6 quality win.

## Concurrency, Versions, And Search Index

Research artifact writes use a serialized workspace research-artifact lock,
compare-and-swap through `expected_content_hash`, atomic replacement, and
immutable prior copies under
`trading/research/.versions/<artifact>/`. The stable markdown path remains the
latest pointer. Callers that race against a newer version receive a conflict
instead of silently overwriting it.

List and search maintain a rebuildable v3 SQLite catalog at
`trading/research/.index/research-object-catalog-v3.sqlite3`. It unifies
existing artifacts and Source Snapshots with Dataset manifests/withdrawals and
Calculation specs/runs. Structured tables hold object cards, relations,
Dataset-column metadata, Calculation metrics, and file-projection state. FTS5
indexes only titles, summaries, warnings, and tags; it never indexes or embeds
numeric Dataset rows. When Python's SQLite lacks FTS5, all structured filters
continue to work and text search falls back to bounded `LIKE`; `tcx doctor`
reports that degraded search posture as a warning.

Calculation cards project their input and derived Dataset relations plus bounded
instrument/symbol metadata from those manifests. A role can therefore find a
calculation by its calculation type/metric or by the related instrument without
loading the CalculationSpec, Dataset manifest, or payload rows.

Projection state is keyed by canonical source path, mtime, size, source hash,
and projection hash, so an ordinary refresh reads and reprojects only changed,
new, or removed files. A failed SQLite integrity check, schema mismatch, or
explicit rebuild removes only the derived database and recreates it from
canonical workspace files. The catalog never becomes evidence, policy, or
execution authority.

For one compatibility release, the same projector rules continue emitting
`research-index.json` and `artifact-catalog-v3.json` with their current public
CLI/API/MCP response shapes. These JSON files are compatibility exports rather
than parallel search truth. Catalog search retains lexical relevance plus
artifact type, universe, symbol, workflow, readiness, handoff, compatibility,
and point-in-time cutoff filters. A cutoff search uses the object's explicit
temporal field (`knowledge_cutoff` for research, decisions, forecasts,
Datasets, and Calculations; `known_at` for source and outcome records) and
excludes records whose qualifying field is missing, malformed, or later than
the requested cutoff instead of silently treating them as historical evidence.

New service-created artifacts and research objects keep their existing strict type-specific writer
contracts and normally project as `full`. Pre-existing files are indexed lazily:
records missing canonical identity metadata project as `legacy_partial` with a
stable path-derived catalog id, while malformed records project as `invalid`
and remain excluded from normal search. Neither status mutates the source file.
`rebuild_artifact_catalog` discards only the derived catalog and recreates it
under a lock. This forward-strict/backward-lazy posture lets updated workspaces
search old evidence without falsely upgrading missing provenance.

Evidence Run Cards are small evidence-only JSON artifacts written beside an
existing artifact path with a `.run-card.json` suffix. They capture config hash,
input refs, data-source refs, artifact hashes, metrics or validation summary,
warnings, source limitations, generated-at time, and the related artifact path.
`tcx quality-check <path>.run-card.json --strict` validates the shape. A run
card never grants order drafting, approval, execution, broker, or policy
authority; it is review metadata only.

Validation Cards are small evidence-only JSON artifacts written beside an
existing artifact path with a `.validation-card.json` suffix. They capture the
related artifact path, evidence-quality label, input/source refs, artifact
hashes, metrics, warnings, source limitations, and anti-overfit evidence
metadata for leakage, survivorship bias, data snooping, out-of-sample posture,
walk-forward consistency, Monte Carlo permutation, bootstrap Sharpe confidence
intervals, costs, capacity, and live friction. A validation card is not a
backtest engine and never grants order drafting, approval, execution, broker,
or policy authority.

Markdown that requires anti-overfit review sets
`anti_overfit_required: true` and supplies a structured
`anti_overfit_checks` object with the same canonical check keys, typed status,
reason, and exact evidence references. Body-language keyword matching never
activates or proves this gate.

`thesis_lifecycle` metadata uses the states `exploring`, `testing`,
`validated`, `rejected`, and `monitoring`. State changes are evidence-gated in
artifact quality checks: `testing` needs source or evidence refs, `validated`
needs an Evidence Run Card, Validation Card, and reviewer acceptance,
`rejected` needs an invalidation note, and `monitoring` needs a monitoring
artifact or review cadence. This is metadata inside existing artifacts, not a
separate hypothesis registry.

These tools read and write workspace markdown files for research artifacts.
They still use the Django service boundary for validation, provenance, audit,
and MCP role/capability checks. Product web renders the markdown body with a
maintained parser/sanitizer and displays frontmatter separately as metadata;
frontmatter must not be mixed into the rendered markdown body.

Research file path inputs are workspace-contained. Service-layer path
arguments must be relative paths, must not contain `..`, must not resolve through
symlinks outside the workspace, and must remain under the artifact directory
allowed for the operation: research markdown under `trading/research/` or
`trading/reports/`, source snapshots under
`trading/research/source-snapshots/`, and decision artifacts under
`trading/decisions/`. Order tickets, approval receipts, broker orders, fills,
and execution state are central-DB records addressed through service tools, not
workspace artifact paths.

`create_research_artifact` creates a workspace file. It must not silently
overwrite an existing artifact id with different content in the same workspace.
Use `append_research_artifact_version` or `./tcx research append
<payload.json|-> --principal <role>` for an intentional same-run version.
Changing the cutoff or sealed Strategy, Brain, Investor Context, or material
evidence starts a new analysis run instead of mutating the old judgment point.

`get_research_artifact` always returns the public v2 shape: `artifact`
(identity, summary, subject, status, lineage, requirements, and applicable
decision/memory blocks), `path`, `authentication`, `markdown_window`, and
`markdown`. Detail levels only control how much verified Markdown and optional
review material enters that shape. Cards omit Markdown and remain bounded;
review/full reads can request deterministic character windows with
`markdown_start` and `markdown_max_chars`. The service verifies the complete
canonical file and receipt before any projection. Lists and searches return
v2 cards under `items` with page metadata, including when the source file is an
immutable v1 artifact adapted by the compatibility reader.

Successful authenticated writes return exact `artifact_id`, `path`,
`handoff_state`, and content identity. Producers pass those service-returned
values without reconstruction. Fixed children receive exact upstream IDs rather
than broad artifact discovery. Narrow answers do not need a write; persist when
the result supports a decision, reuse, audit, or downstream handoff.

In a Codex-native analysis, a producing role supplies report/source metadata,
the assigned `workflow_run_id`, and exact `input_artifact_ids` when it consumes
upstream work. Its Research profile keeps `trading/` read-only even though
user-owned paths outside that tree are writable, so the final role report is
written only through the authenticated MCP writer. The service authenticates
the producer, verifies every input belongs to the same run, writes the compact
Markdown envelope, and seals exact hashes, versions, and run overlay lineage
in receipt v4. For a Brain-bound run, it derives
`investment_brain_id`, `investment_brain_version`, and
`investment_brain_content_digest`; callers cannot supply or override those
fields. It applies the same service-derived rule to `strategy_name`,
`strategy_hash`, `investor_context_applied`, and `investor_context_hash`. The
current version's declared `input_artifact_ids` replace the prior version's
input lineage on append; when an append declares none, the service preserves
the prior version's lineage. This lets a same-owner correction bind the exact
cross-role artifacts newly consumed without confusing a trigger artifact with
the target being versioned. The
service does not own a plan, stage, task, or dispatch state. A
receiving role fetches an exact artifact id through authenticated
`get_research_artifact`; it does not discover report files or use
coordinator-transcribed artifact bodies.

Every successful run-bound MCP write also creates a service receipt under the
sealed run directory. Receipt-backed verification first requires the workspace
id to match its centrally recorded canonical workspace root, then validates the
receipt's run-record hash, artifact id/path/version, body and full-file hashes,
authenticated producer, exact input ids, hashes, and versions, and sealed
Brain, Strategy, and Investor Context lineage. When present, it also validates
exact CalculationRun ids and service-derived hashes plus the reuse
Run-to-origin mapping. Cutoff and workflow binding are revalidated before the
artifact is published. Its integrity value is an HMAC made with a
service-owned key under the
global TradingCodex state directory, outside the workspace; a caller cannot
turn matching frontmatter or a recomputed plain hash into a service receipt.
The key is created once on the first authenticated write and is not part of the
workspace or its Git history. Historical receipts require that original key.
Verification never creates a missing key, replaces it, or silently re-signs an
old receipt: a missing or changed key fails closed. Consequently, a workspace
copy at another resolved root reports existing run-bound receipts as unverified
even when it shares the local home and trusted key. A current export sidecar
also authenticates that resolved root, so a copied or replayed sidecar is not
accepted as an export marker. A clone on another host likewise fails unless
that host already has the original trusted installation key and central path
binding. A deliberate move to a separate runtime database likewise does not
transfer either value, so existing run-bound receipts are not verified there;
v1 does not copy or export service keys or central provenance with a workspace.
Receipt, run-record, artifact, and version-archive paths must remain regular
non-symlink files. Under the research lock, the writer validates the exact
intended bytes, creates the signed receipt first, and atomically replaces the
stable artifact pointer last. A normal receipt, run, input, snapshot, archive,
or stable-write failure rolls back the receipt and any new archive while
leaving the prior stable artifact and rebuildable index unchanged. A process
death can therefore leave only an unpublished future receipt or exact archive,
never a stable version with no matching receipt; a later identical write may
safely reuse or replace that signed orphan. Authenticated reads return the same
snapshot they verified rather than rereading mutable workspace content afterward.
Appending a run-bound artifact first revalidates the current receipt,
source-snapshot bindings, and recursive input lineage before it derives body or
metadata for the next version. The append path uses a read-only artifact lookup
and repeats receipt and recursive-lineage verification under the research lock,
then rechecks the verified current full-file hash before archiving or replacing
anything. An artifact id is permanently pinned to its canonical workspace path:
append does not advertise relocation, and any top-level, metadata, or Markdown
frontmatter `path`/`export_path` declaration must exactly equal the stored path.
Create likewise refuses to relocate an existing identity or overwrite an
occupied destination unless that regular file parses as the same canonical
artifact identity; these checks happen before artifact, index, archive, or
receipt writes and are repeated under the research lock. A pre-existing archive
is accepted only when its lexical path and all
ancestors are symlink-free, it is a regular file, and its bytes exactly equal
the current stable version. Tampering, removed run provenance, missing or
invalid receipts, and lineage changes therefore fail before stable artifact or
index writes; append never turns modified workspace Markdown into newly
authenticated evidence. When an upstream stable pointer advances, downstream
verification resolves its receipt-sealed exact input version from the
symlink-free archive, verifies that version's signed receipt, and recursively
does the same for its inputs. Historical lineage therefore remains verifiable
without silently substituting the newest upstream version.
Reads, synthesis, forecasts, and Decision Memory snapshots reverify the
receipt. Forecast issuance also detects the run id recorded in a Markdown
origin and verifies it when the caller omits the redundant run-id argument.
Manually written Markdown, copied frontmatter, direct service calls, and CLI
writes may still create ordinary non-run-bound research, but they cannot claim
trusted run lineage or enter an authenticated synthesis chain.

For a head-manager synthesis, the service also derives the exact artifact id,
`trading/reports/head-manager/synthesis-<workflow_run_id>.md` path, and complete
run-local input hashes from the exact consumed `input_artifact_ids`. The
coordinator may not file-edit state or producer artifacts. It retrieves only
exact returned artifacts through authenticated `get_research_artifact`, then
decides dynamically whether to revise, add a role, challenge, wait, block, or
synthesize. Shell reads, glob discovery, latest pointers, and server terminal
state are not synthesis authority. Malformed unmanaged markdown is reported as
invalid repository content but cannot poison lookup or creation of an
unrelated canonical artifact. An authenticated input is still ineligible for
synthesis until its receipt-bound handoff state is `accepted`.

Synthesis retains the service-derived Brain lineage and explains how the Brain
materially affected inquiry or interpretation, any conflict with Strategy,
current evidence, or Decision Memory, and the post-memory delta when memory was
consulted. Brain instructions never authorize a lower evidence grade or widen
policy, approval, broker, order, or execution scope.

Two workspaces may use the same `artifact_id` for different local research
files. That is expected because research handoff state is workspace-native.

## Artifact Paths

| Artifact | Path |
| --- | --- |
| Evidence packs | `trading/research/*.evidence.md` |
| Research memos | `trading/research/*.md` |
| Fundamental reports | `trading/reports/fundamental/` |
| Technical reports | `trading/reports/technical/` |
| News reports | `trading/reports/news/` |
| Macro reports | `trading/reports/macro/` |
| Instrument reports | `trading/reports/instrument/` |
| Valuation reports | `trading/reports/valuation/` |
| Portfolio reports | `trading/reports/portfolio/` |
| Risk/policy reports | `trading/reports/risk/`, `trading/reports/policy/` |
| Evidence Run Cards | `*.run-card.json` beside the related artifact under `trading/research/`, `trading/reports/`, or `trading/decisions/` |
| Validation Cards | `*.validation-card.json` beside the related artifact under `trading/research/`, `trading/reports/`, or `trading/decisions/` |
| Forecast ledger records | `trading/forecasts/*.jsonl` |
| Dataset manifests | `trading/research/datasets/manifests/*.json` |
| Dataset payload objects | `trading/research/datasets/objects/<payload-sha256>.parquet` |
| Dataset withdrawal events | `trading/research/datasets/withdrawals/*.json` |
| Calculation specifications | `trading/research/calculations/specs/*.json` |
| Calculation runs | `trading/research/calculations/runs/*.json` |
| Research specifications | `trading/research/specs/*.json` |
| Replay manifests | `trading/research/replay-manifests/*.json` |
| Experiment runs | `trading/research/experiments/*.json` |
| Causal analyses and judgment reviews | `trading/research/analyses/*.json`, `trading/research/judgment-priors/*.json`, `trading/research/judgment-reviews/*.json` |
| Model-upgrade evaluation artifacts | `trading/evaluations/{corpora,runs,blind-review-assignments,blind-reviews,comparisons}/*.json` |
| Rebuildable research index | `trading/research/.index/research-index.json` |
| Rebuildable cross-artifact catalog | `trading/research/.index/artifact-catalog-v3.json` |
| Rebuildable research-object catalog | `trading/research/.index/research-object-catalog-v3.sqlite3` |
| Order tickets | central DB `OrderTicket` records |
| Postmortems | `trading/reports/postmortem/*.postmortem_report.json` |
| Lesson event ledger and chain heads | `.tradingcodex/mainagent/improve.jsonl`, `.tradingcodex/mainagent/lesson-chain-heads.json` |
| Skill change proposals | `.tradingcodex/mainagent/skill-change-proposals/*.yaml` |

Execution-sensitive state is updated by the MCP/service layer. Policy
decisions, approvals, executions, portfolio snapshots, and audit ledgers remain
service-layer decisions.

## Quality Harness Floor

Investment reports, role handoffs, and final syntheses share a quality floor.

| Rule | Application |
| --- | --- |
| Fact/analysis/assumption clarity | Distinguish sourced observations, analytical conclusions, and scenario or model assumptions in readable prose. |
| Anti-fabrication | Do not invent metrics, factor loadings, transaction costs, validation results, source dates, market prices, filings, approvals, executions, or artifact content. |
| Uncertainty disclosure | Disclose small samples, thin regime coverage, high parameter sensitivity, and weak source coverage. |
| Suggestive vs conclusive | If evidence is suggestive, say so instead of turning it into a conclusion. |
| Empirical vs economic | Separate empirical stability from economic plausibility. |
| Paper vs live | State when paper alpha may disappear under live implementation friction. |
| Confidence | Lower confidence when data quality, source coverage, sample size, regime coverage, or validation setup is weak. |
| Source/as-of posture | For market-sensitive inputs, record source date, as-of, retrieved-at, provider/tool, and missing/stale warnings. |
| Hero/support artifact split | Choose the user-facing report, tracker, workbook, or synthesis first; keep CSV/JSON/run log/source indexes as support/audit layers. |
| Claim-fit readiness | Choose readiness from support for conclusion-driving claims, freshness, material conflicts, and downstream use—not from whether every source is primary. |
| Handoff acceptance | Mark whether a role artifact is `accepted`, `revise`, `blocked`, or `waiting`; downstream roles should not repair upstream work outside their owned question. |
| Source trust | Identify official records, management claims, provider/market data, reputable secondary reporting, stale evidence, and unsupported assumptions; weight each for the claim rather than applying a universal source ladder. |
| Judgment review | Decision-oriented artifacts should preserve contrary evidence, update triggers, invalidation conditions, source trust notes, and thesis lifecycle notes when decision quality is required. |
| Forecast discipline | Required forecasts must be horizon-bound, evidence-aware, updateable, and either valid for the ledger or blocked with `forecast_block_reason`. |
| Anti-overfit validation | When explicitly required, structured `anti_overfit_checks` must address leakage, survivorship bias, data snooping, out-of-sample coverage, costs, capacity, regime sensitivity, and live friction with typed status, reason, and evidence refs. |

## Readiness Axes

Evidence readiness describes how strongly the evidence supports the claims;
action readiness describes what may happen next. Acceptance means a report is
complete, not that an action is authorized.

| Evidence value | Meaning |
| --- | --- |
| `factual` | Supported descriptive facts without a decision posture. |
| `screen` | Useful for triage, but a material decision input remains weak or absent. |
| `decision-grade` | Conclusion-driving claims are current, attributable, and fit for the stated judgment. |
| `insufficient` | A material conflict, stale input, unsupported assumption, or missing anchor prevents the stated judgment. |

| Action value | Meaning |
| --- | --- |
| `research-only` | Continue analysis or monitoring; no portfolio implication is asserted. |
| `portfolio-review` | Portfolio and risk roles may review the decision-grade judgment. |
| `draft-eligible` | Accepted portfolio/risk evidence and applicable Investor Context allow order-ticket drafting, still subject to policy and approval. |
| `blocked` | A named evidence, policy, role, or user boundary blocks the next action. |

`portfolio-review` and `draft-eligible` require `decision-grade` evidence.
Valuation without a real current-price or market-as-of anchor is
`insufficient`; placeholder text never substitutes for an anchor.

## File Behavior

Research file writes should be deterministic enough for review:

- preserve artifact ID and version
- include source/as-of metadata
- include one concise `summary` shared by agents and users
- include handoff state, both readiness axes, confidence and its basis,
  missing evidence, blocked actions, requirements, and source snapshot IDs
- use only exact service-returned source snapshot IDs, or an empty list when no
  snapshot was recorded
- include content hash
- include the exact run and input IDs in Markdown while sealing exact input
  versions/hashes and Brain/Strategy/Investor Context lineage in receipt v4
- include exact current-workflow `calculation_run_ids` for every numerical
  result used in a conclusion; an exact reuse preserves both the reuse Run and
  original successful Run lineage
- avoid raw secrets
- use stable paths for role-owned reports
- save head-manager final synthesis reports as
  `trading/reports/head-manager/synthesis-<workflow_run_id>.md`
- make stale/missing source warnings visible
- keep file content readable by Codex without a DB lookup
