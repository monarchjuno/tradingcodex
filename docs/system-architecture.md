# System Architecture

This document owns TradingCodex architecture, Django app boundaries, central DB
ownership, service-layer use cases, runtime planes, and core model ownership.

## Architecture Summary

```text
Multiple Codex projects / subagents / local CLI
  -> product web review dashboard, Django-hosted MCP endpoint, or stdio bridge
  -> Django service layer
  -> central Django DB-backed policy, research, orders, portfolio, audit, harness, integrations
  -> paper/stub adapter boundary; future live adapters only after separate installation and policy approval
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
    common.py
    runtime.py
    policy.py
    orders.py
    portfolio.py
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
  universes/
  workflows/
workspace_templates/
tests/
docs/
```

Do not reintroduce Node runtime surfaces such as `package.json`, `packages/*`,
old `templates/*`, or Node MCP scripts unless the product direction changes
explicitly in docs.

`tradingcodex_service.domain` remains a compatibility facade for existing
imports and tests. Durable service implementation lives under
`tradingcodex_service/application/`. Likewise, `tradingcodex_cli.workspace`
remains the generated-wrapper entry facade while command implementations live
under `tradingcodex_cli/commands/`.

## Runtime Planes

| Plane | Responsibility | Durable state |
| --- | --- | --- |
| Codex control plane | Role prompts, hooks, skills, workflow guidance, generated project config | Generated workspace files and Codex session state |
| Django service plane | Policy, orders, approvals, portfolio, research, audit, harness, MCP registry, Admin, REST, web dashboard | Central Django DB |
| Workspace system plane | Readable exports, schemas, local wrapper, MCP config, artifact directories | Export/cache files and provenance |

The control plane can request actions. The service plane decides and records
durable outcomes. The workspace system plane makes those outcomes readable and
repeatable for Codex and humans.

## Central DB Ownership

The default runtime DB is the central local SQLite ledger at:

```text
~/.tradingcodex/state/tradingcodex.sqlite3
```

Overrides:

- `TRADINGCODEX_HOME`
- `TRADINGCODEX_DB_NAME`

`TRADINGCODEX_WORKSPACE_ROOT` is provenance only. It must not partition
canonical investment state. `.tradingcodex/workspace.json` stores the immutable
workspace id; `path_hash` remains path provenance and may change if a workspace
moves.

Two generated workspaces share research memory, MCP ledger, approvals,
executions, and audit records through the same central DB unless the operator
intentionally changes the DB path. Paper portfolio state is scoped by active
profile (`portfolio_id`, `account_id`, `strategy_id`), not by workspace path.

## Django App Boundaries

| App | Responsibility |
| --- | --- |
| `harness` | Subagent roster, role skill map, skill proposals, generated workspace config, workspace identity, workspace provenance, active profile metadata. |
| `workflows` | Workflow lanes, workflow runs, artifact handoffs, readiness labels, process state. |
| `policy` | Principals, capabilities, restricted list, limits, policy decisions. |
| `orders` | Order intents, approval receipts, execution results, lifecycle validation. |
| `portfolio` | Cash, positions, exposure snapshots, paper portfolio state. |
| `research` | DB-backed markdown research artifacts, artifact versions, evidence packs, report metadata, source/as-of records. |
| `audit` | Append-only audit events, request hashes, result hashes, policy/action provenance. |
| `mcp` | Protocol adapter metadata, tool registry, tool call ledger. |
| `integrations` | Paper/stub adapters, read-only data adapters, future broker adapter definitions. |
| `universes` | Public equity, ETF/index, crypto, macro/rates/FX/commodities, options, credit-signal workflow plugins. |

## Service Layer Rules

Interfaces must call shared service functions rather than duplicating durable
logic. This applies to:

- product web routes
- Django Admin actions
- Django Ninja endpoints
- MCP tool handlers
- CLI commands
- generated workspace wrappers
- generated hooks that need durable state

Executable actions flow through:

```text
principal -> capability -> policy -> schema -> approval/idempotency -> adapter -> audit
```

Policy and approval are revalidated immediately before adapter submission.

## Service Use Cases

Executable use cases:

- `create_order_intent`
- `validate_order_intent`
- `create_approval_receipt`
- `submit_approved_order`
- `cancel_approved_order`
- `simulate_policy`
- `record_execution_result`

Read/write research and audit use cases:

- `create_research_artifact`
- `get_research_artifact`
- `list_research_artifacts`
- `search_research_artifacts`
- `append_research_artifact_version`
- `export_research_artifact_md`
- `record_source_snapshot`
- `record_audit_event`

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
| `Principal` | Actor identity used for policy and MCP calls. |
| `Capability` | Explicit allow/deny inputs for actions. |
| `PolicyDecision` | Deterministic policy outcomes and reasons. |
| `RestrictedSymbol` | Restricted security/instrument entries. |
| `ResearchArtifact` | Canonical research object and current metadata. |
| `ResearchArtifactVersion` | Versioned markdown/content hash/source posture. |
| `SourceSnapshot` | Retrieved source metadata, as-of posture, and provenance. |
| `WorkspaceContext` | Calling workspace provenance. |
| `WorkflowRun` | Workflow lane, status, role participation, and lifecycle. |
| `ArtifactRef` | Handoff references between workflow runs and artifacts. |
| `SkillProposal` | Proposed skill assignment or prompt/template change. |
| `RoleSkillAssignment` | Approved role-to-skill ownership map. |
| `OrderIntent` | Draft or approved order intent data. |
| `ApprovalReceipt` | Approval evidence, approver, and policy context. |
| `ExecutionResult` | Adapter submission outcome and idempotency record. |
| `PortfolioSnapshot` | Point-in-time portfolio state. |
| `Position` | Instrument position state. |
| `CashBalance` | Cash state by currency/account context. |
| `McpToolDefinition` | Admin-visible synced MCP registry entry. |
| `McpToolCall` | DB-visible MCP call ledger. |
| `AuditEvent` | Append-only audit record. |
| `UniversePlugin` | Installed universe capability and routing metadata. |
| `AdapterDefinition` | Adapter availability, risk posture, and enabled state. |

## Adapter Boundary

Paper and stub adapters are shipped first as experimental local harness
surfaces. Live broker adapters remain disabled and unimplemented in the initial
core. Any future live adapter must be separately installed and must still pass
the same service-layer policy, approval, idempotency, adapter, and audit
boundary.
