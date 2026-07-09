# System Architecture

This document owns TradingCodex architecture, Django app boundaries, central DB
ownership, service-layer use cases, runtime planes, and core model ownership.

## Architecture Summary

```text
Multiple Codex projects / subagents / local CLI
  -> product web review dashboard, stdio MCP bridge, or service API
  -> Django service layer, including managed External MCP Gate checks
  -> workspace-file agent/skill/research state plus central Django DB-backed policy, orders, portfolio, audit, harness, integrations
  -> approved action boundary; paper is built in and live providers require separate installation, policy approval, explicit confirmation, sync, and audit gates
```

The app boundary is modular-monolith ownership, not a distributed-service
boundary. Admin, Ninja, MCP, CLI, generated hooks, and product web routes call
shared application services for durable behavior.

## Source Tree

```text
pyproject.toml
manage.py
tradingcodex_service/
  application/
    brokers.py
    common.py
    components.py
    runtime.py
    orders.py
    portfolio.py
    policy.py
    research.py
    audit.py
    harness.py
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
maintainability. For the `0.2.0` release contract, implementation should split
by durable service use case or harness component rather than by interface
surface. Web, API, CLI, MCP, and generated hooks should continue calling the
same application services instead of growing separate policy, execution,
research, projection, or audit paths.

Do not reintroduce Node runtime surfaces such as `package.json`, `packages/*`,
old `templates/*`, or Node MCP scripts unless the product direction changes
explicitly in docs.

Durable service implementation lives under
`tradingcodex_service/application/`. CLI command implementations live under
`tradingcodex_cli/commands/`. The `0.2.0` codebase should use these canonical
modules directly rather than preserving pre-release compatibility facades.

## Runtime Planes

| Plane | Responsibility | Durable state |
| --- | --- | --- |
| Codex control plane | Role prompts, hooks, skills, workflow guidance, generated project config | Generated workspace files and Codex session state |
| Django service plane | Policy, brokers, orders, approvals, portfolio, audit, harness, MCP registry, External MCP Gate, Admin, REST, web dashboard, and file-native research indexing | Central Django DB for non-research runtime records |
| Workspace system plane | Agent TOML, skill files, research markdown, schemas, local wrapper, MCP config, artifact directories | Codex-native workspace files and provenance |

The control plane can request actions. The service plane decides and records
durable outcomes. The workspace system plane makes those outcomes readable and
repeatable for Codex and humans.

Control-plane maintainability depends on clear ownership:

- `.codex/prompts/base_instructions/*` owns durable coordinator identity,
  routing fail-closed rules, and cross-cutting safety/context-efficiency rules.
- `.codex/agents/*.toml` owns fixed-role identity, model/tool defaults,
  role-projected skill source lists, and assigned skill projection.
- `.agents/skills/*` owns head-manager and strategy procedures;
  `.tradingcodex/subagents/skills/*` owns role procedures and output shape.
  Skill files do not own durable role eligibility or MCP authority.
- `.codex/hooks/*` owns prompt classification, hook audit, and guidance context;
  hooks do not enforce execution-sensitive outcomes.
- `.tradingcodex/policies/*` owns principal, role, information-barrier, and
  restricted-list policy projections.
- `tradingcodex_service/application/*` owns durable service behavior used by
  CLI, API, MCP, web, Admin, and generated hooks.
- `tradingcodex_service/application/agents.py` is the service registry for
  role labels, display groups, handoff contracts, forbidden action summaries,
  built-in skills, permission profiles, and MCP allowlists.

Adding a future subagent should therefore add or update the role registry,
role TOML, role-skill projection, information-barrier policy, MCP allowlist,
docs, and tests together instead of burying role-specific behavior inside
generic skills.

## Central DB Ownership

The default runtime DB is the central local SQLite ledger at:

```text
~/.tradingcodex/state/tradingcodex.sqlite3
```

Overrides:

- `TRADINGCODEX_HOME`
- `TRADINGCODEX_DB_NAME`
- `TRADINGCODEX_DATABASE_URL` for SQLite or PostgreSQL-style Django database
  configuration. PostgreSQL deployments should install the package with the
  `postgres` extra so Django can load the driver.

`TRADINGCODEX_WORKSPACE_ROOT` selects the Codex workbench for file-native
agent, skill, and research state. It must not partition canonical
execution-sensitive investment state. `.tradingcodex/workspace.json` stores the
immutable workspace id; `path_hash` remains path provenance and may change if a
workspace moves.

Two generated workspaces have separate research handoff markdown and source
snapshot JSON because those files belong to the workspace. They share
non-research MCP ledger rows, broker connections, portfolio sync/reconciliation
state, order tickets, approvals, executions, policy, and audit records through
the same central DB unless the operator intentionally changes the DB path.
Paper portfolio state is scoped by active profile (`portfolio_id`,
`account_id`, `strategy_id`), not by workspace path.
Order-ticket listing and ticket-addressed service actions use the same active
profile scope so a user reviewing the current account/strategy does not see,
check, approve, or submit drafts from another profile as current work.

## Django App Boundaries

| App | Responsibility |
| --- | --- |
| `harness` | Workspace identity, workspace provenance, active profile metadata, and file-native agent/skill projection helpers. |
| `workflows` | Workflow lanes, workflow runs, artifact handoffs, readiness labels, process state. |
| `policy` | Principals, capabilities, restricted list, limits, policy decisions. |
| `orders` | Canonical order tickets, order checks, approval receipts, broker order timeline, fills, and execution attempts/results. |
| `portfolio` | Cash, positions, exposure snapshots, normalized ledger events, broker sync runs, reconciliation runs, paper portfolio state. |
| `research` | Workspace markdown research artifacts, artifact versions, evidence packs, report metadata, and file-native source/as-of snapshots. No Django DB models or Admin DB surface. |
| `audit` | Append-only audit events, request hashes, result hashes, policy/action provenance. |
| `mcp` | Protocol adapter metadata, tool registry, and non-research tool call ledger. |
| `integrations` | Broker connections, broker accounts, instrument maps, paper and validation-only execution paths, read-only data adapters, future broker adapter definitions. |

## Service Layer Rules

Interfaces must call shared service functions rather than duplicating durable
logic. This applies to:

- product web routes
- Django Admin default model registry
- Django Ninja endpoints
- MCP tool handlers
- CLI commands
- generated workspace wrappers
- generated hooks that need durable state

Executable actions flow through:

```text
requester -> permission -> policy -> payload validation -> approval/duplicate-request check -> connection -> audit
```

Policy and approval are revalidated immediately before non-live connection use.

## Service Use Cases

Order and execution use cases:

- `create_order_ticket`
- `run_order_checks`
- `request_order_approval`
- `submit_approved_order`
- `cancel_approved_order`
- `refresh_broker_order_status`
- `get_order_status`
- `simulate_policy`
- `record_execution_result`

`OrderTicket` is the canonical product and Codex workflow root. CLI, API, and
MCP order workflows address central DB tickets directly; no file-payload order
compatibility path is maintained outside Django migrations.

Broker and portfolio use cases:

- `list_broker_connections`
- `get_broker_connection_status`
- `sync_broker_account`
- `record_broker_mapping_review`
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
- `record_audit_event`

Research artifact writes preserve workspace markdown as the source of truth and
carry handoff metadata for source/as-of posture, claim type discipline,
confidence, missing evidence, next-recipient routing, blocked actions, and
source snapshots. `quality-check --strict` validates the markdown handoff
contract, Evidence Run Card shape, and Validation Card shape without moving
research memory into the central DB.

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
| `WorkflowRun` | Workflow lane, status, role participation, and lifecycle. |
| `ArtifactRef` | Handoff references, role owner, hero/support marker, and acceptance state between workflow runs and artifacts. |
| `OrderTicket` | Canonical user-facing draft/check/approval/submission state machine and payload hash owner. |
| `OrderCheckRun` | Schema, policy, cash/position, market, broker-validation, and risk check results, plus machine-readable `approval_table_meta` carrying approval-table validity window, invalidation events, per-row as-of fields, snapshot hashes, and cash-reserve stress values. |
| `ApprovalReceipt` | Approval evidence, approver, exact order payload hash, broker/account scope, and policy context. |
| `OrderEvent` | Order ticket state and broker timeline events. |
| `BrokerOrder` | Broker-side order id and status mapping. |
| `Fill` | Fill quantity/price/fee record linked to an order ticket. |
| `ExecutionResult` | Adapter submission outcome and idempotency record. |
| `BrokerConnection` | Broker transport, adapter type, credential reference, capabilities, status, and drift state. |
| `BrokerAccount` | Discovered account metadata and trading-enabled lock per broker connection. |
| `InstrumentMap` | Canonical-to-broker symbol mapping and order sizing metadata. |
| `PortfolioSnapshot` | Point-in-time portfolio state. |
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

Broker adapters sit behind a registry-like service interface. The built-in
paper adapter supports account discovery, cash/position reads, validation, and
paper submission. External MCP broker support starts from discovery metadata
and reviewed read-only or summary-only mappings; execution-like external tools
must map to a TradingCodex service connection path and remain disabled until
separate review enables the full live execution checklist.
