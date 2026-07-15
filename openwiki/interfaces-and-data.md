# Interfaces And Data

Use this page before changing Web, Admin, API, MCP, CLI command behavior, model ownership, research or decision memory, investor context, or data flow. Human-facing detail lives in [docs/interfaces-and-surfaces.md](../docs/interfaces-and-surfaces.md), [docs/system-architecture.md](../docs/system-architecture.md), [docs/research-memory-and-artifacts.md](../docs/research-memory-and-artifacts.md), and [docs/decision-memory.md](../docs/decision-memory.md).

## Interface Rule

Every interface is a caller of the service layer. No interface should create a parallel policy, order, approval, execution, portfolio, broker, research, or audit path.

| Surface | Main files | Boundary |
| --- | --- | --- |
| Product web | `frontend/*`, `tradingcodex_service/static/tradingcodex_web/*`, `tradingcodex_service/web.py`, `tradingcodex_service/viewer_api.py`, `tradingcodex_service/application/viewer.py` | Read-only Library/Skills/System viewer with a left-rail selector limited to registered, validated attached workspaces. It never starts Codex or mutates workspace state. |
| Django Admin | `apps/*/admin.py` | Local/staff DB inspection. Order/execution ledgers and external MCP router launch configuration are read-only; no custom bypass path. |
| Ninja API | `tradingcodex_service/api.py` | Typed local/staff control endpoints that call services; no final execution mutation route. |
| MCP | `tradingcodex_service/mcp_runtime.py` | Role-scoped research, preparation, approval, status, proof-protected Build services, and scoped `manage_investment_brain`/`manage_strategy` lifecycle. Root Head Manager alone also sees `use_order_turn_grant`, which is inert without current hook proof; no raw submit/cancel/refresh mutation, REST mirror, broker proxy, or model-visible runtime credentials. |
| Root native action hook | `workspace_templates/modules/codex-base/files/.codex/hooks/tradingcodex_hook.py`, `application/execution_gateway.py`, `application/build_gateway.py` | Exact immediate user submit/cancel plus exact-first-line `$tcx-order-allow`, `$tcx-build`, `$tcx-brain`, and `$tcx-strategy` admission, scope checks, revocation, and proof injection. |
| CLI | `tradingcodex_cli/commands/*` | Operator and generated-wrapper interface. Should call services. |

Generated CLI wrappers project the workspace service address. Default service
status/ensure/stop/runserver operations must honor it; do not reintroduce a
hard-coded `127.0.0.1:48267` path that bypasses development isolation.

Build customization surfaces live in the same service-layer rule:
`/build/` and `tcx build ...` summarize Codex config discovery, managed MCP
config writes, optional skills, additional instructions, and pending external
MCP permissions without creating a parallel MCP registry.
Generic Codex-originated Build mutations require an exact `$tcx-build`
current-turn grant. Brain and Strategy management instead use exact
`$tcx-brain` or `$tcx-strategy` root turns in `trading-research`, with grants
limited to the matching native source/staging path and proof-protected
management tool. Research keeps the generated CLI and attached runtime denied;
the actual sandbox still decides whether writes are possible. External MCP
consent moved to the explicit operator command `tcx mcp permission`; direct
terminal mutation remains separate operator authority.

Unsafe Ninja requests authenticated by a staff cookie require CSRF. API-key
requests do not, but role-authored mutations use the canonical MCP tool
allowlist, active-principal, capability, schema, and transport-identity checks.
Staff-only overlay administration remains distinct and never grants an agent
role to an arbitrary staff username, including one that collides with a
canonical agent principal id. Role-authored mutations require an API-key-bound
principal.

Viewer API:

- `GET /api/viewer/` returns the canonical `{generated_at, sections}` snapshot;
  each section is exactly `{ok, data}` or `{ok, error}`.
- `GET /api/viewer/skills/{skill_id}` and
  `GET /api/viewer/artifacts/{artifact_id}` return sanitized detail.
- An explicit or session-bound workspace id must resolve to a registered current
  v1 workspace. Invalid selections fail instead of falling back to the default.
- No viewer POST, PATCH, DELETE, preview, run, follow-up, or subprocess route
  exists. Native Codex owns agent execution.

Research and artifact service contract:

- Children and Head Manager retrieve exact returned bodies by artifact id
  through authenticated `get_research_artifact`. Head Manager synthesis must
  name at least one verified run-local input artifact; shell/glob discovery is
  outside the runtime contract.
- Each run-bound write receives an HMAC-authenticated service receipt that
  binds the workspace id, run record, regular non-symlink artifact file/body,
  authenticated producer, exact input ids/hashes/versions, and sealed
  Brain/Strategy/Investor Context lineage. Synthesis, forecasts, and Decision
  Memory reverify it; caller-authored metadata and plain recomputed hashes are
  not provenance. The global-state signing key is installation-local:
  missing/replaced keys and workspace-only clones fail verification and are
  never silently re-keyed or re-signed. Forecasts derive a Markdown origin's
  recorded run id when the caller omits the redundant argument.
- A run-bound write validates intended bytes and commits its receipt under the
  research lock before atomically replacing the stable pointer. Normal failure
  rolls back the receipt/new archive and leaves stable/index state unchanged;
  a process death can leave only a harmless unpublished future receipt/archive.
- A run-bound append revalidates the current receipt plus recursive inputs and
  source snapshots both before and inside the research lock, then rechecks the
  current full-file hash. Artifact ids stay pinned to their canonical paths:
  append path declarations must match exactly, and create cannot relocate an
  existing id or overwrite a destination occupied by another artifact. These
  checks fail before artifact/index/receipt mutation and repeat under the lock.
  Version archives and ancestors must be symlink-free;
  pre-existing archives must be regular files with exact prior stable bytes.
  Downstream verification uses receipt-sealed input versions to resolve and
  recursively authenticate historical archives after an upstream advances.
- A producing role calls `record_source_snapshot` before artifact storage when
  reproducibility requires one and uses only the exact returned `snapshot_id`;
  invented/missing ids fail closed, while no recorded snapshot means an empty
  `source_snapshot_ids` list plus explicit locator/retrieval/coverage posture.
  Normal agent calls omit service-owned `snapshot_id`, `retrieved_at`, and
  `recorded_at`; the service derives safe receipt times and a bounded ID.
  `known_at` is supplied only when genuinely known and timezone-qualified.
- Persist or return only normalized, redacted, allowlisted activity—never raw
  reasoning, tool inputs/outputs, stderr, or raw final output.
- Any read-only activity projection must stay evidence-derived and must not
  manufacture a DAG, percent complete, predefined role team, or assignment
  rationale. Narrow layouts use list-or-detail navigation for Library and
  Skills instead of stacking both panes.
- Run and artifact reads reject symlink escapes and project allowlisted fields.
  Final synthesis also requires sealed run lineage, authenticated artifact
  receipts, complete input and body hashes, accepted handoff readiness, and
  the applicable strict quality gate.

## Research Memory

Research is workspace-file-native. Canonical files:

- `trading/research/*.md`
- `trading/research/*.evidence.md`
- `trading/reports/<role>/*.md`
- `trading/research/source-snapshots/*.json`
- `trading/research/specs/*.json`
- `trading/research/replay-manifests/*.json`
- `trading/research/experiments/*.json`
- `trading/research/analyses/*.json`
- `trading/research/judgment-priors/*.json` and `judgment-reviews/*.json`
- `*.run-card.json` beside research, report, or decision artifacts
- `*.validation-card.json` beside research, report, or decision artifacts
- `trading/forecasts/forecast-ledger.jsonl`
- `trading/decisions/*.md` and `trading/decisions/*.decision-snapshot.json`
- `trading/reports/postmortem/*.postmortem_report.json`
- `trading/evaluations/{corpora,runs,blind-review-assignments,blind-reviews,comparisons}/*.json`

Research service calls may index, validate, search, preview, version, and write
these files, but the markdown, JSON, or JSONL file is the source of truth.
Input markdown staged under `trading/research/.drafts/` is excluded from the
index until `research create` writes a canonical artifact. Indexed markdown
requires explicit `artifact_id`, `artifact_type`, and `universe`; path-based
identity inference is not part of v1. Markdown run/validation cards use their
own validators and are not research-index entries.
Order tickets, approval receipts, order-turn grants, broker orders, fills, and
execution state are central-DB records accessed through canonical services, not
research artifacts. Final submit/cancel enters through either a parser-issued
immediate root-native mandate or a current `$tcx-order-allow` grant reserved and
proven by `PreToolUse`. Public REST and generic CLI expose
read/preparation/status surfaces only; direct MCP calls cannot use the protected
grant consumer without current hook proof.
Forecast resolution is independent from forecast authorship; causal analysis
loads numeric inputs only from a hash-verified replay snapshot; paired model
evaluation remains research-only and cannot promote itself into order or
execution authority. Research MCP calls intentionally skip DB tool-call ledger
rows.

Wiki pages, temporal or claim graphs, similarity links, and dashboards are
rebuildable read projections. Historical replay, historical holdout, and live
forward evidence are distinct. Historical filing work binds the filing or
accession, accepted/published time, form, period, units, and amendment posture;
current aggregates are discovery-only unless that cutoff identity is known.
Historical macro work binds first-release, vintage, or realtime data rather
than silently using a revised current series. Selection evidence records every
variant, the observed/effective trial count, the frozen selection rule, and
single-use holdout posture; multiple-testing diagnostics apply when their
assumptions and inputs are actually available, not as a universal gate. The optional investor suitability file lives at
`.tradingcodex/user/investor-context.md`; internal paper account scope remains
separate and execution-sensitive state stays in the central DB.
Harness/API projections expose it only as `investor_context` and read only the
canonical snake_case keys stored in that file.

ResearchSpec is profile-based: `general_evidence_v1`, `event_research_v1`,
`quant_signal_v1`, and `listed_equity_fcff_dcf_v1` add only method-appropriate
requirements. Evaluation corpora bind `core_investment_v1` or a bounded
corpus-declared profile; paired runs also bind an extension-profile hash and
map reported unregistered extension use to a hard failure. Current run digests
and check outcomes are caller-attested, so comparisons force `hold` until a
trusted evaluation runner verifies provenance. Do not infer a universal quant
or FCFF contract from one profile.

## Central DB Data

Central DB model families:

- policy: `Principal`, `Capability`, `RestrictedSymbol`, `PolicyDecision`
- orders and native grants: `OrderTicket`, `OrderCheckRun`, `ApprovalReceipt`,
  `OrderTurnGrant`, compatibility-named scoped `BuildTurnGrant`, `ExecutionResult`, `OrderEvent`,
  `BrokerOrder`, `Fill`
- portfolio: `PortfolioSnapshot`, `Position`, `CashBalance`, `PortfolioLedgerEvent`, `BrokerSyncRun`, `ReconciliationRun`
- integrations: `AdapterDefinition`, `BrokerConnection`, `BrokerAccount`, `InstrumentMap`
- MCP: tool definitions/calls and external MCP registry/review/call models
- harness: `WorkspaceContext`
- audit: `AuditEvent`

`BrokerConnection` owns the required canonical `provider_id` and transport.
Paper is `paper` / `paper`, External MCP is `external-mcp` / `mcp`, and
registered native providers use their exact lowercase connector-safe provider
id with `api`; mismatched
identity/transport pairs fail closed and no `adapter_type` alias exists.

## Edit Checklist

When changing this area:

- put durable behavior in `tradingcodex_service/application/*`
- update `docs/interfaces-and-surfaces.md` for user/admin/API/MCP/CLI behavior changes
- update `docs/system-architecture.md` for app/model/service ownership changes
- update `docs/research-memory-and-artifacts.md` for research file contracts
- run focused tests plus `python manage.py check` for Django surface changes
- run frontend test/build plus desktop, narrow, keyboard, and error-state browser
  checks for viewer changes
- run MCP smoke for MCP registry, handler, bridge, or role allowlist changes
