# System Architecture

This document owns TradingCodex architecture, Django app boundaries, central DB
ownership, service-layer use cases, runtime planes, and core model ownership.

## Architecture Summary

```text
Browser / multiple Codex projects / subagents / local CLI
  -> read-only React workspace viewer, stdio MCP bridge, or service API
Native root Codex action prompt
  -> deterministic UserPromptSubmit parser -> native execution gateway
Native root Codex Build prompt
  -> normalized first-meaningful-line parser -> current-turn build gateway + PreToolUse gate
Both service-facing paths
  -> Django service layer for TradingCodex-owned capabilities and state
  -> workspace-file agent/skill/research state plus central Django DB-backed policy, orders, portfolio, audit, harness, integrations
  -> approved action boundary; paper is built in and live providers require separate installation, policy approval, explicit confirmation, sync, and audit gates
```

The app boundary is modular-monolith ownership, not a distributed-service
boundary. Admin, Ninja, MCP, CLI, generated hooks, and the React viewer call
shared application services for durable behavior. Django does not launch Codex,
implement a second role scheduler, or directly spawn fixed roles.

TradingCodex is the top-level investment OS. Its core kernel owns scope,
evidence, point-in-time, uncertainty, artifact, forecast, policy, approval,
audit, and execution invariants. A bundled investment capability pack supplies
the pristine research and analysis methods. Project-local instructions,
optional role skills, and `strategy-*` skills are managed user overlays. The
harness coordinates those layers; it is not the product definition and an
overlay never replaces the kernel.

## Source Tree

```text
pyproject.toml
manage.py
frontend/
  package.json
  src/
  vite.config.ts
tradingcodex_service/
  static/tradingcodex_web/
  application/
    brokers.py
    build_gateway.py
    common.py
    components.py
    execution_gateway.py
    runtime.py
    orders.py
    portfolio.py
    policy.py
    research.py
    research_specs.py
    investment_analysis.py
    forecasting.py
    evaluation_lab.py
    audit.py
    harness.py
    analysis_runs.py
    investment_brains.py
    viewer.py
    workspace_git.py
    workspaces.py
    health.py
tradingcodex_cli/
  commands/
apps/
  audit/
  harness/
  integrations/
  mcp/
  orders/
  policy/
  portfolio/
  research/
  workflows/
workspace_templates/
tests/
docs/
```

The source tree above is conceptual. Large service modules may be refactored
into packages under `tradingcodex_service/application/` when that improves
maintainability. For the v1 release contract, implementation should split
by durable service use case or harness component rather than by interface
surface. Web, API, CLI, MCP, and generated hooks should continue calling the
same application services instead of growing separate policy, execution,
research, projection, or audit paths.

The source tree has one intentional Node build root at `frontend/`. React 19,
TypeScript, and Vite 8 compile deterministic committed files under
`tradingcodex_service/static/tradingcodex_web/`. Django and WhiteNoise serve
that build. Do not add a production Node server, package workspace, Node MCP
runtime, or Node dependency to generated workspaces; `tcx attach` and `tcx
update` never run npm.

Durable service implementation lives under
`tradingcodex_service/application/`. CLI command implementations live under
`tradingcodex_cli/commands/`. The v1 codebase uses these canonical modules
directly.

## Runtime Planes

| Plane | Responsibility | Durable state |
| --- | --- | --- |
| Codex control plane | Role prompts, hooks, skills, dynamic Head Manager coordination, lightweight run bindings, generated project config, exact immediate-action interception, and `$tcx-order-allow` turn-grant admission/proof injection | Generated workspace files and Codex session state |
| Django service plane | Policy, brokers, orders, approvals, portfolio, audit, harness, TradingCodex MCP registry, Admin, REST, read-only React viewing, native Codex capability inventory, and file-native research/artifact catalog indexing | Central Django DB for non-research runtime records plus operational service state |
| Workspace system plane | Agent TOML, skill files, research markdown, schemas, local wrapper, MCP config, artifact directories | Codex-native workspace files and provenance |

The control plane can request actions. The service plane decides and records
durable outcomes. The workspace system plane makes those outcomes readable and
repeatable for Codex and humans.

Control-plane maintainability depends on clear ownership:

- `.codex/prompts/base_instructions/*` owns durable coordinator identity,
  routing fail-closed rules, and cross-cutting safety/context-efficiency rules.
- `.codex/agents/*.toml` owns fixed-role identity, model/tool defaults,
  role-projected skill source lists, and assigned skill projection.
- `.agents/skills/*` owns head-manager procedures, including Decision Memory and
  Investor Context, strategy procedures, the explicit-only `tcx-build` bundle,
  and the three native execution protocol bundles;
  `.tradingcodex/subagents/skills/*` owns role procedures and output shape.
  Skill files do not own durable role eligibility or MCP authority.
- `.codex/hooks/*` owns transport/run binding, exact explicit extension syntax
  reporting, hook audit, guidance context, and deterministic interception of
  the three root-native execution skills plus normalized first-meaningful-line
  `$tcx-build`, `$tcx-brain`, and `$tcx-strategy` contracts. It does not classify natural language, select roles,
  or build a DAG. The two complete immediate action
  protocols create a mandate and call the service-owned execution gateway
  before a model runs. Exact first-meaningful-line `$tcx-order-allow` instead issues/revokes a
  bounded current-turn grant and injects proof only into its protected tool;
  all lasting enforcement remains in the service kernel.
- `tradingcodex_service/application/execution_gateway.py` owns the exact action
  parser, workspace-bound immediate mandate, `OrderTurnGrant`
  issue/reservation/consumption/revocation, `native-user` authorization, safe
  result projection, and dispatch into the canonical order services.
- `tradingcodex_service/application/build_gateway.py` owns exact `$tcx-build`,
  `$tcx-brain`, and `$tcx-strategy` parsing, DB-canonical scoped
  workspace/session/turn/cwd/prompt grants, protected-call
  proof reservation and consumption, expiry/revocation, and redacted audit. It
  does not elevate the Codex sandbox or grant execution authority. The
  compatibility-named `BuildTurnGrant` records `build`, `brain`, or `strategy`
  authority scope.
- `.tradingcodex/config.yaml` owns the exact workspace execution-policy input;
  `.tradingcodex/policies/restricted-list.yaml` adds the file-native restricted
  list to the canonical DB list. There are no generated principal, role,
  capability, access-policy, or information-barrier authority copies.
- `tradingcodex_service/application/*` owns durable service behavior used by
  CLI, API, MCP, web, Admin, and generated hooks.
- `frontend/*` owns read-only viewer presentation and client interaction; its committed
  build is generated output, not a second business-logic layer.
- `tradingcodex_service/application/agents.py` is the service registry for
  role labels, display groups, handoff contracts, forbidden action summaries,
  built-in skills, and MCP allowlists. Together with the MCP tool registry, it
  is the runtime capability authority; generated indexes are diagnostic
  projections, and retired `capabilities.yaml`, `roles.yaml`, and
  `policy-bindings.yaml` copies are not runtime inputs.
- `tradingcodex_service/application/investment_brains.py` owns strict Brain
  bundle validation, immutable source/version records, activation, rollback,
  and Head Manager-only projection. `analysis_runs.py` and the research service
  own sealed run and artifact lineage.
- `tradingcodex_service/application/workspace_git.py` owns generated-workspace
  Git membership, privacy-first managed ignore rules, and read-only repository
  diagnostics; it never stages, commits, creates a remote, or publishes. Git
  subprocesses ignore inherited repository and config overrides, disable
  executable remote helpers, and omit credentials, query data, and fragments
  from the reported origin URL while still reading the repository's local
  configuration. `TRADINGCODEX_HOME` must resolve outside the generated
  workspace: attach, runtime resolution, and doctor fail closed otherwise.
  This keeps receipt-signing keys and other private runtime authority
  physically separate from versionable Brain, Strategy, research, decision,
  lesson, and run-provenance files.

Adding a future subagent should therefore add or update the role registry,
role TOML, role-skill projection, MCP allowlist, docs, and tests together
instead of burying role-specific behavior inside generic skills. Analysis
filesystem posture remains one project-wide `trading-research` profile rather
than per-role filesystem authority. A separate user-selected `trading-build`
profile is reserved for root Build turns and never propagates through analysis
dispatch.

## Central DB Ownership

The default runtime DB is the central local SQLite ledger under one canonical
platform home:

| Platform | Default `TRADINGCODEX_HOME` |
| --- | --- |
| macOS | `~/Library/Application Support/TradingCodex` |
| native Windows | `%LOCALAPPDATA%\TradingCodex` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/tradingcodex` |

Each home keeps `preferences/`, `backups/`, `state/`, and versioned managed
Python environments under `runtime/python/` together; the default database is
`state/tradingcodex.sqlite3`. Managed Python environments are package runtime,
not user state: they keep generated launchers and MCP independent of removable
uv caches and remain outside every versioned workspace. The canonical resolver in
`tradingcodex_service.application.runtime` is also used by Django settings,
generated workspaces, service lifecycle code, and diagnostics.

Overrides:

- `TRADINGCODEX_HOME`
- `TRADINGCODEX_DB_NAME`

`TRADINGCODEX_HOME` wins explicitly and is reported as
`environment_override`. Without it, a clean installation uses
`platform_default`. `tcx home status` and `tcx home check` report that single
selection without probing alternate locations. `TRADINGCODEX_DB_NAME` remains
an independent DB-only override; v1 does not move or merge runtime homes.

`TRADINGCODEX_WORKSPACE_ROOT` selects the Codex workspace for file-native
agent, skill, and research state and supplies workspace provenance. It is not a
DB selector or paper-account identifier. `.tradingcodex/workspace.json` stores
the immutable workspace id; `path_hash` remains path provenance and may change
if a workspace moves.

Two generated workspaces have separate research handoff markdown and source
snapshot JSON because those files belong to the workspace. Their service
records live in the same central DB by default, but the storage location is not
a cross-workspace user context: records retain workspace provenance and
execution-sensitive operations bind to the selected internal paper-account
scope. Paper portfolio state is scoped by an internal active paper-account record
(`portfolio_id`, `account_id`, `strategy_id`), not by workspace path.
Order-ticket listing and ticket-addressed service actions enforce the same internal
scope so a user reviewing the current account/strategy does not see, check,
approve, or submit drafts from another scope as current work. New workspaces
start with a workspace-id-derived isolated paper account. Additional account
scopes must be explicitly created in that workspace. Each scope owns a
validated three-letter base currency used by paper
cash initialization and order-policy notional comparison; instrument currency
remains explicit and cross-currency orders require point-in-time FX evidence.

Investor suitability context is separate file-native state at
`.tradingcodex/user/investor-context.md`. It is created on confirmed update,
records an enable/disable default and content hash, and is the only Investor
Context source. It never replaces account scope or enters the central DB as
research memory.

Filesystem identity uses resolved, normalized paths before DB/service and
workspace-provenance comparisons. This normalizes macOS temporary-directory
symlinks and Windows case/separator aliases. Generated files use same-directory
atomic replacement and native advisory locks (`flock` on POSIX and `msvcrt`
byte-range locking on Windows); network-filesystem lock semantics are not
claimed. Service child creation uses OS-specific process flags, and service stop
refuses remote hosts or an unverified listener PID.

## Django App Boundaries

| App | Responsibility |
| --- | --- |
| `harness` | Workspace identity, workspace provenance, internal paper-account metadata, investor-context binding, and file-native agent/skill projection helpers. |
| `policy` | Principals, capabilities, restricted list, limits, policy decisions. |
| `orders` | Canonical order tickets, order checks, approval receipts, current-turn order grants, broker order timeline, fills, and execution attempts/results. |
| `portfolio` | Cash, positions, exposure snapshots, normalized ledger events, broker sync runs, reconciliation runs, paper portfolio state. |
| `research` | Workspace markdown research artifacts, artifact versions, evidence packs, report metadata, and file-native source/as-of snapshots. No Django DB models or Admin DB surface. |
| `audit` | Append-only audit events, request hashes, result hashes, policy/action provenance. |
| `mcp` | Protocol adapter metadata, tool registry, and non-research tool call ledger. |
| `integrations` | Broker connections, broker accounts, instrument maps, paper and validation-only execution paths, read-only data adapters, future broker adapter definitions. |

## Service Layer Rules

Interfaces must call shared service functions rather than duplicating durable
logic. This applies to:

- product web routes
- read-only viewer snapshot and detail endpoints
- Django Admin default model registry
- Django Ninja endpoints
- MCP tool handlers
- CLI commands
- generated workspace wrappers
- generated hooks that need durable state

Executable actions flow through:

```text
exact root native prompt -> deterministic hook parse -> native-user permission
  -> policy -> payload validation -> approval/duplicate-request check
  -> mandatory intent audit -> connection -> finalized/uncertain audit
```

Policy and approval are revalidated immediately before non-live connection use.

The product web assembles a read-only selected-workspace snapshot and reads
sanitized skill and artifact detail. Workspace ids must resolve through the
registered workspace store and invalid or stale selections fail closed. The
browser has no preview, run-start, follow-up, cancellation, skill-management,
or Codex subprocess path. Native Codex owns agent execution; authenticated
service and MCP calls retain durable-state and execution boundaries.

## Service Use Cases

Order and execution use cases:

- `create_order_ticket`
- `run_order_checks`
- `request_order_approval`
- `issue_order_turn_grant`
- `reserve_order_turn_grant`
- `execute_reserved_order_turn_grant`
- `revoke_order_turn_grants`
- `submit_approved_order`
- `discard_draft_order`
- `cancel_submitted_order`
- `_refresh_broker_order_status_for_reconciliation`
- `get_order_status`
- `simulate_policy`
- `record_execution_result`

`submit_approved_order`, `cancel_submitted_order`, and
`_refresh_broker_order_status_for_reconciliation` are internal application services rather than
raw public MCP or REST mutations. Final submit/cancel enters them through either
a parser-issued immediate native mandate from the generated root
`UserPromptSubmit` hook or one consumed `OrderTurnGrant` whose protected
`use_order_turn_grant` call carries current `PreToolUse` proof. Root Head
Manager has no raw submit/cancel tool, and direct MCP callers cannot supply the
proof. Status refresh remains service-owned recovery/reconciliation behavior.
`OrderTicket` is the canonical product and Codex workflow root. Public order
preparation/read surfaces address central DB tickets directly. Approval
receipts, broker orders, fills, and execution state are likewise DB-canonical
and never represented as authoritative workspace order files.

Broker and portfolio use cases:

- `list_broker_connections`
- `get_broker_connection_status`
- `sync_broker_account`
- `list_reconciliation_runs`

Read/write research and audit use cases:

- `create_research_artifact`
- `get_research_artifact`
- `list_research_artifacts`
- `search_research_artifacts`
- `append_research_artifact_version`
- `export_research_artifact_md`
- `create_evidence_run_card`
- `create_validation_card`
- `record_source_snapshot`
- `rebuild_research_index`
- `create_research_spec`
- `get_research_spec`
- `list_research_specs`
- `create_replay_manifest`
- `record_experiment_run`
- `create_causal_equity_analysis`
- `record_blind_judgment_prior`
- `complete_judgment_review`
- `issue_forecast`
- `revise_forecast`
- `resolve_forecast`
- `score_forecast`
- `get_forecast`
- `list_forecasts`
- `calibration_report`
- `create_evaluation_corpus`
- `record_evaluation_run`
- `record_blind_human_review`
- `compare_evaluation_runs`
- `record_audit_event`

Research artifact writes preserve workspace markdown as the source of truth and
carry handoff metadata for source/as-of posture, claim type discipline,
confidence, missing evidence, next-recipient routing, blocked actions, and
source snapshots. `quality-check --strict` validates the markdown handoff
contract, Evidence Run Card shape, and Validation Card shape without moving
research memory into the central DB.

ResearchSpec, replay-manifest, ExperimentRun, forecast, score, and calibration
operations are evidence-only file-native services. They cannot draft, approve,
or execute orders. Point-in-time manifests reject evidence known after the
frozen cutoff; experiment checks require typed outcomes and hash-bound evidence;
forecast resolution must be independent from forecast authorship.

ResearchSpec validation is method-profile-specific. General evidence, event
research, quant-signal validation, and listed-equity FCFF DCF share only the
common evidence contract; quant-only trial and anti-overfit fields and
DCF-specific driver/scenario fields are not universal requirements. Evaluation
corpora likewise bind either the bundled `core_investment_v1` profile or a
corpus-declared bounded profile. Paired runs also bind an extension-profile
hash and map reported unregistered extension use to a hard failure. Current
run digests remain caller-attested and comparisons force `hold` until a trusted
evaluation runner verifies provenance.

Skill projection is a managed-workspace inventory, not a complete inventory of
the host Codex process. Projection records layer, trust, implicit-invocation
posture, and resolved source path, while doctor checks exact enabled paths and
same-name host-global collisions. Host-global and plugin skills enter the
investment methodology only through explicit workflow opt-in or managed
activation. Runtime-wide non-discovery remains an attested property, not an
architectural assumption.

Analysis runs store only identity, request hash/size, timestamps, and sealed
strategy/Investor Context provenance. Head Manager owns semantic interpretation
and dynamic role orchestration. Codex/subagent events and authenticated
artifacts expose progress; no Django routing envelope, DAG, or workflow reducer
exists. The central DB continues to own execution-sensitive state.

Run-bound artifact provenance is authenticated with service receipts under
`.tradingcodex/mainagent/runs/<analysis-run-id>/artifact-bindings/`. A receipt
binds the sealed run-record hash, artifact path/version/body and file hashes,
producer, exact inputs, and Brain/Strategy/Investor Context lineage. Synthesis,
forecasts, and Decision Memory reverify receipts instead of trusting Markdown
frontmatter or caller-supplied lineage.

The viewer adds no run state beside the lightweight native run under
`.tradingcodex/mainagent/runs/<analysis-run-id>/` and launches no Codex process.
It reads canonical activity and accepted artifacts through the shared service
layer without exposing raw stderr, reasoning, tool payloads, or raw final
output. Reader-facing final analysis remains an ordinary accepted workspace
artifact, such as the existing Head Manager report path, and gains no order or
execution authority from browser display.

Read-only/status use cases:

- `get_harness_topology`
- `get_role_detail`
- `get_harness_health`
- `list_recent_activity`
- `list_policy_overview`
- `list_positions`
- `get_portfolio_snapshot`
- `list_workflow_artifacts`
- `inspect_harness_state`

## Core Models

| Model | Owns |
| --- | --- |
| `Principal` | Requester identity used for policy and MCP calls. |
| `Capability` | Explicit action permission inputs. |
| `PolicyDecision` | Deterministic policy outcomes and reasons. |
| `RestrictedSymbol` | Restricted security/instrument entries. |
| `WorkspaceContext` | Calling workspace provenance. |
| `OrderTicket` | Canonical user-facing draft/check/approval/submission state machine and payload hash owner. |
| `OrderCheckRun` | Schema, policy, cash/position, market, broker-validation, and risk check results. |
| `ApprovalReceipt` | Approval evidence, approver, exact order payload hash, broker/account scope, and policy context. |
| `OrderTurnGrant` | Single-use workspace/session/turn/prompt/mode admission, reservation, proof binding, consumption, expiry, and revocation state. |
| `BuildTurnGrant` | Compatibility-named multi-use workspace/session/turn/cwd/prompt grant with explicit `build`, `brain`, or `strategy` authority scope, plus protected-call reservation, expiry, revocation, and cross-scope denial state. |
| `OrderEvent` | Order ticket state and broker timeline events. |
| `BrokerOrder` | Broker-side order id and status mapping. |
| `Fill` | Fill quantity/price/fee record linked to an order ticket. |
| `ExecutionResult` | Adapter submission outcome and idempotency record. |
| `BrokerConnection` | Required provider identity, transport, credential reference, capabilities, status, and drift state. |
| `BrokerAccount` | Discovered account metadata and trading-enabled lock per broker connection. |
| `InstrumentMap` | Canonical-to-broker symbol mapping and order sizing metadata. |
| `PortfolioSnapshot` | Point-in-time portfolio state. |
| `PaperPortfolioState` | Versioned current paper state and compare-and-swap key per portfolio/account/strategy. |
| `Position` | Instrument position state. |
| `CashBalance` | Cash state by currency/account context. |
| `PortfolioLedgerEvent` | Normalized cash, position, fill, fee, FX, adjustment, and other broker/portfolio events. |
| `BrokerSyncRun` | Read-only broker sync attempt, counts, warnings, error, and payload hash. |
| `ReconciliationRun` | Broker/local snapshot comparison and drift summary. |
| `McpToolDefinition` | Admin-visible synced MCP registry entry. |
| `McpToolCall` | DB-visible MCP call ledger for non-research tools. |
| `AuditEvent` | Append-only audit record. |
| `AdapterDefinition` | Adapter availability, risk posture, and enabled state. |

## Adapter Boundary

Paper and validation-only adapters are shipped first as experimental local
harness surfaces. Live broker adapters remain disabled and unimplemented in the
initial core. Any future live adapter must be separately installed and must
still pass the same service-layer policy, approval, duplicate-request,
connection, and audit boundary.

Each connection stores one required `provider_id` and one transport. The valid
built-in pair is `paper` / `paper`; registered provider adapters use their
exact lowercase connector-safe provider id with `api`. Unknown providers,
unsupported transports, mismatched pairs, and profile/provider identity drift
fail closed. There is no persisted or public `adapter_type` alias.

Broker adapters sit behind a registry-like service interface. The built-in
paper adapter supports account discovery, cash/position reads, validation, and
paper submission. User-installed Codex capabilities are not broker adapters and
cannot be imported into this connection registry. A broker provider must use
the canonical provider adapter and service path before it can participate in
TradingCodex order, approval, execution, reconciliation, or audit guarantees.
