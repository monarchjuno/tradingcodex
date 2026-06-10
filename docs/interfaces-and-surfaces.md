# Interfaces And Surfaces

This document owns the behavior of TradingCodex product web, Django Admin,
Django Ninja API, Django-hosted MCP, CLI, and generated workspace wrappers.

## Interface Rule

Every interface is a caller of the service layer. No interface may create a
parallel policy, order, approval, execution, portfolio, research, or audit path.
Public import and route facades may stay stable for compatibility, but their
durable behavior should delegate to `tradingcodex_service/application/` service
modules and `tradingcodex_cli/commands/` command modules.

| Surface | Primary role | Must not do |
| --- | --- | --- |
| Product web | Visual review and starter prompt preparation | Spawn agents, generate investment analysis, approve orders, execute orders, mutate execution-sensitive state |
| Django Admin | Local/staff operations console | Bypass service-layer policy or audit |
| Django Ninja | Typed local/staff REST and control API | Mirror every MCP tool automatically or bypass execution checks |
| MCP | Agent/tool execution boundary | Expose raw REST endpoints or raw broker APIs |
| CLI | Local operator and generated wrapper interface | Fork durable behavior away from services |

## Product Web App

TradingCodex provides a user-facing web app at `/`. It is a dashboard and
review surface, not a table-first Admin replacement.
The web app should present Harness as the top-level model, with Guardrails and
Improvement visible as child systems.

Routes:

- `/` visual dashboard
- `/harness/` full harness topology
- `/research/` DB-backed research memory review
- `/portfolio/` central paper portfolio state
- `/orders/` order, approval, and execution lifecycle review
- `/policy/` restricted list and policy decision review
- `/activity/` MCP call ledger, audit events, and workflow activity
- `/workflow/starter-prompt/` Codex starter prompt generator

The product web app uses Django templates, local static HTMX, and local static
Alpine. There is no Node, bundler, React, or frontend build step in the
baseline.

### Visual Harness Canvas

The visual harness canvas is server-rendered SVG/HTML. It shows:

- center node: `head-manager`
- surrounding nodes: the nine fixed subagents
- edge groups: dispatch, research handoff, portfolio/risk gate, approval gate, execution gate
- role inspector: owned skills, allowed MCP tools, forbidden actions, latest artifacts, latest activity
- MCP execution boundary: principal, policy, schema, approval, adapter, and audit checks

### Product Web Boundary

- The product web app does not spawn Codex subagents.
- The product web app does not generate investment analysis.
- The product web app does not approve or execute orders in v1.
- The product web app can generate starter prompts for the user to run in Codex.
- Execution-sensitive actions remain behind TradingCodex MCP and service-layer policy.

## Django Admin

Django Admin is the harness control panel for local/staff operators. It can
inspect and manage:

- role roster and role skill assignments
- skill proposals and generated workspace config
- policy, restricted symbols, capability allowlists, limits
- MCP tool registry and tool call ledger
- workflow runs, artifact refs, readiness labels
- order intents, approval receipts, execution results
- research artifacts, markdown versions, source snapshots
- portfolio snapshots, positions, cash balances
- adapter definitions and universe plugins
- audit logs

Risky changes use:

```text
proposal -> validation -> approval -> apply -> audit
```

Admin actions must call service functions and create audit events. Useful Admin
actions include enabling/disabling MCP tools, syncing the built-in MCP
registry, approving/applying/rejecting skill proposals, toggling
principals/capabilities/restricted symbols, and disabling live adapters.

## Django Ninja API

Django Ninja provides local/staff typed control APIs:

- `GET /api/health`
- `GET /api/harness/status`
- `GET /api/subagents`
- `GET /api/subagents/{role}/skills`
- `GET /api/workflows/{id}`
- `POST /api/workflows/{id}/validate`
- `POST /api/policy/simulate`
- `POST /api/orders/validate-intent`
- `POST /api/approvals`
- `POST /api/executions/submit-approved`
- `GET /api/audit/events`
- `GET /api/portfolio/snapshot`
- `POST /api/research/artifacts`
- `GET /api/research/artifacts`
- `GET /api/research/artifacts/{artifact_id}`
- `POST /api/research/artifacts/{artifact_id}/export`
- `POST /api/research/search`
- `POST /api/research/source-snapshots`

The canonical approval and execution routes are `/api/approvals` and
`/api/executions/submit-approved`. Compatibility aliases may exist under
`/api/orders/approvals` and `/api/orders/executions/submit-approved`; aliases
must call the same service functions and must not widen permissions.

OpenAPI docs are staff-protected. REST is for operations, validation,
inspection, and local control.

## MCP Boundary

TradingCodex hosts MCP inside Django as a custom endpoint at `/mcp`, separate
from the Ninja API. MCP is intentionally selected service-layer use cases, not
an automatic REST mirror.

Minimum MCP protocol surface:

- `initialize`
- `tools/list`
- `tools/call`
- `resources/list`
- `prompts/list`

Minimum MCP tools:

- `get_tradingcodex_status`
- `validate_order_intent`
- `create_approval_receipt`
- `submit_approved_order`
- `cancel_approved_order`
- `get_positions`
- `get_portfolio_snapshot`
- `simulate_policy`
- `list_workflow_artifacts`
- `create_research_artifact`
- `get_research_artifact`
- `list_research_artifacts`
- `search_research_artifacts`
- `append_research_artifact_version`
- `export_research_artifact_md`
- `record_source_snapshot`
- `record_audit_event`

Every MCP tool definition includes stable name, description, input schema,
category, risk level, role allowlist, approval requirement, and audit
requirement. `tools/list` returns this metadata as tool annotations.
`tools/call` records `McpToolCall` rows with principal, status, request/result
hashes, errors, and duration.

For Codex environments that require stdio MCP, `tcx mcp stdio` runs a bridge
that calls the same service layer. The stdio bridge must never write non-MCP
logs to stdout.

The project MCP server is named `tradingcodex` and carries the current
workspace provenance. Optional global safe MCP is named `tradingcodex-home`,
limits the server-side tool surface to read-only/status/search tools, and must
not expose approval, execution, cancellation, policy mutation, secret, or broker
tools.

## Role-Specific MCP Exposure

The root `head-manager` allowlist exposes research, audit, portfolio/status,
and policy simulation tools, but excludes approval creation and execution
submission. Role-scoped agent TOML files expose narrower risky tools only to
their owner roles:

- `risk-manager` can create approval receipts.
- `execution-operator` can call experimental submit/cancel execution tools.

MCP registry role allowlists are a second boundary after `.codex/agents/*.toml`.
MCP tool execution also checks active `Principal` rows and matching
`Capability` rows.

## CLI

The CLI entrypoint is `tcx`.

Top-level commands:

- `tcx attach [workspace] [--overwrite]`
- `tcx init <workspace> [--overwrite]`
- `tcx doctor [--layer <name>]`
- `tcx workspace status|list`
- `tcx profile status|list|create|select`
- `tcx subagents status|list|plan|skills|prompt|state`
- `tcx skills list [--all]|inspect|propose-add|propose-update|apply-proposal`
- `tcx research create|append|get|list|search|export`
- `tcx policy simulate`
- `tcx db status|path|migrate`
- `tcx mcp call <tool> [tool args]`
- `tcx mcp ledger [--tool <name>] [--principal <id>] [--status ok]`
- `tcx mcp install-global --safe`
- `tcx mcp stdio`
- `tcx service runserver`

Generated workspace wrapper commands:

- `./tcx doctor`
- `./tcx workspace status|list`
- `./tcx profile status|list|create|select`
- `./tcx subagents status`
- `./tcx subagents prompt "<request>"`
- `./tcx validate order <path>`
- `./tcx approve <path>`
- `./tcx db status|path|migrate`
- `./tcx mcp call <tool>`
- `./tcx mcp ledger [--tool <name>]`
- `./tcx research create|append|get|list|search|export`

Default main-agent skill listing is user-facing, not exhaustive. It shows only
direct user entrypoints: `orchestrate-workflow`,
`head-manager-interview`, and `postmortem`. Full inspection is available
through `./tcx skills list --all` and role-specific
`./tcx subagents skills <role>`.
