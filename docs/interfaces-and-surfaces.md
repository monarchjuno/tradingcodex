# Interfaces And Surfaces

This document owns the behavior of TradingCodex product web, Django Admin,
Django Ninja API, stdio MCP, CLI, and generated workspace wrappers.

## Interface Rule

Every interface is a caller of the service layer. No interface may create a
parallel policy, order, approval, execution, portfolio, research, or audit path.
For the `0.2.0` release contract, public routes and imports should use the
canonical `tradingcodex_service/application/` service modules and
`tradingcodex_cli/commands/` command modules directly rather than preserving
pre-release aliases.

| Surface | Primary role | Must not do |
| --- | --- | --- |
| Product web | Visual review, broker read-only setup, order-ticket drafts/checks, and workflow handoff preparation | Spawn agents, generate investment analysis, approve orders, execute orders, mutate execution-sensitive state |
| Django Admin | Local/staff operations console | Bypass service-layer policy or audit |
| Django Ninja | Typed local/staff REST and control API | Mirror every MCP tool automatically or bypass execution checks |
| MCP | Agent approved-action boundary | Expose raw REST endpoints or raw broker APIs |
| CLI | Local operator and generated wrapper interface | Fork durable behavior away from services |

## Product Web App

TradingCodex provides a user-facing web app at `/`. It opens on a workflow
planner for non-expert first use, not a table-first Admin replacement. The
primary product web workflow is translating an investment request into a
Codex-native handoff, with the agents/skills browser available for inspection,
optional skill work, and operator review.
For the root `head-manager`, active `strategy-*` skills are also visible as
strategy entries because they are workspace skills, but product web remains a
read-only preview page.

Routes:

- `/` redirects to `/workflow/starter-prompt/`
- `/harness/` redirects to `/harness/agents/`
- `/harness/agents/` head-manager and subagent skill browser with readable skill preview
- `/workflow/starter-prompt/` workflow planner and Codex handoff generator
- `/brokers/` Broker Center for paper broker setup, native connector profile
  review, external MCP discovery import when needed, read-only sync, and
  reconciliation status
- `/research/` workspace-native research note browser with document details
  and sanitized rendered body
- `/portfolio/` broker-normalized portfolio state, sync action, reconciliation
  status, and workflow-planner links for portfolio review, risk check, or
  rebalance review without creating orders
- `/orders/` order-ticket draft/check review page with submit controls
  disabled until service-layer gates pass
- `/integrations/mcp/` Data Sources surface for External MCP Gate registry,
  source connection checks, manual discovery import, available action review,
  role scopes, and audited dry-run decisions

Direct diagnostic routes may remain for local operators, but they are not part
of the primary product navigation:

- `/policy/` restricted list and policy decision review
- `/activity/` MCP call ledger, audit events, and workflow activity

The product web app uses Django templates, local static HTMX, and small plain
JavaScript. There is no Node, bundler, React, or frontend build step in the
baseline. Its visual language follows a compact dark dashboard style inspired
by shadcn `new-york` components, implemented with vanilla CSS over Django
templates rather than React or Tailwind.

The product web app is content-first. Agent, research, and strategy pages
should show a selectable list first, then a readable preview with document
details. Verbose paths, projection hashes, manifest internals, proposal file
details, and validation internals should live in collapsed diagnostics sections
unless the route is explicitly a diagnostic view.

Markdown preview rendering uses a maintained parser/sanitizer library pair.
Do not hand-roll markdown parsing in templates.

Workspace selection is web-session local:

- `GET <web route>?workspace=<workspace_id>` stores the selected
  `WorkspaceContext` in the current browser session.
- The sidebar selector lists up to 20 recently seen `WorkspaceContext` rows.
- Web rendering uses the selected workspace path when it is valid; invalid or
  missing ids fall back to `TRADINGCODEX_WORKSPACE_ROOT`.
- Recent activity and role-inspector activity are filtered by the selected
  workspace identity so MCP calls, audit events, and workflow runs from another
  Codex workspace do not appear as current-workspace evidence.
- Opening a workspace requires an existing `.tradingcodex/workspace.json`
  manifest. Creating a new workspace is a separate POST action and uses the
  normal non-forced bootstrap path, so non-empty directory protection is not
  bypassed from the web surface.
- This selector does not change CLI, MCP, API, or process-level environment
  behavior.

### Visual Harness Canvas

The visual harness canvas is an optional diagnostic surface rather than the
primary web entrypoint. When present, it is server-rendered SVG/HTML and shows:

- center node: `head-manager`
- surrounding nodes: the nine fixed subagents
- edge groups: dispatch, research handoff, portfolio/risk gate, approval gate, approved action gate
- edge contracts: what the source role must hand off, what the target role may consume, and the quality state expected before moving downstream
- role inspector: owned skills, no-overlap handoff contract, allowed actions,
  forbidden actions, latest artifacts, latest activity
- Approved action boundary: principal, policy, schema, approval, connection, and audit checks

### Product Web Boundary

- The product web app does not spawn Codex subagents.
- The product web app does not generate investment analysis.
- The product web app does not approve or execute orders.
- The product web app can create broker read-only records, import MCP
  discovery metadata, run read-only paper broker sync, and create portfolio
  reconciliation summaries through the service layer.
- The portfolio page can link the current user into a workflow-planner prompt
  for portfolio review, risk check, or rebalance review. Those links prepare a
  Codex handoff only; they do not spawn agents or mutate portfolio/order state.
- The product web app can create order-ticket drafts and run order checks. It
  does not create approval receipts or submit orders.
- The product web app can create, update, activate, archive, delete, and project
  optional subagent skills through the shared application service.
- The product web app lists and previews `strategy-*` skills as read-only
  strategy rules with strategy details. Strategy add, update, activation,
  archival, and deletion workflows happen through Codex `$strategy-creator`,
  CLI, API, or MCP/service-layer flows rather than Django web forms.
- The product web app can edit project-local additional instructions for each
  agent and project them after generated defaults. Its warnings are guidance
  only; it does not reject additional instruction text based on role-boundary
  wording.
- The product web app cannot mutate core/project-scope mainagent skills, fixed
  subagent core skills, permission profiles, MCP allowlists, policy, or
  execution authority through those additional instructions.
- The product web app can preview workflow handoffs for the user to run in Codex,
  including a plain-language workflow summary, idea translation, selected role
  labels and role reasons, blocked actions, next allowed actions, stage order,
  investor-profile gaps, and direct user questions needed before recommendation,
  sizing, approval, or execution. More technical method lenses, iteration controls,
  judgment controls, and strategy baseline details live in a collapsed review
  section, and the copy-ready Codex prompt plus run commands live in a
  collapsed Codex handoff section. The page can show short example prompts that
  link back into the same local preview flow.
- The product web app can preview Artifact Supervisor Loop state, selected
  artifact follow-up requests, pending loop tasks, escalation proposals, and
  blocked actions. This preview is read-only; it does not spawn subagents,
  approve orders, execute orders, or create lane escalation consent.
- The workflow preview can save answered investor-profile context to the
  active profile so later workflow intake reuses it and only asks unanswered
  suitability/profile questions.
- The product web app can register external MCP connections, import discovery
  metadata, review available actions/resources, set role scopes, and audit
  dry-run decisions. It must not expose raw external tools directly to Codex.
- Execution-sensitive actions remain behind TradingCodex MCP and service-layer
  policy, approval, duplicate-request, connection, and audit checks.

## Django Admin

Django Admin uses Django's default admin UI and default model registration. It
is a local/staff DB inspection and emergency edit surface, not a custom
TradingCodex operations console. It exposes:

- policy, restricted symbols, capability allowlists, limits
- MCP tool registry and tool call ledger
- workflow runs, artifact refs, readiness labels
- order tickets, approval receipts, execution results
- portfolio snapshots, positions, cash balances
- adapter definitions
- audit logs
- workspace provenance

TradingCodex does not add custom Admin dashboards, custom Admin templates,
custom Admin CSS, custom Admin actions, or service-layer shortcut buttons.
Risky changes use product web, CLI, API, or MCP service-layer flows such as:

```text
proposal -> validation -> approval -> apply -> audit
```

Agent, skill, strategy, research artifacts, and source snapshots
are intentionally file-native rather than Admin DB surfaces. Optional skill
CRUD, strategy skill creation, and research handoff edits happen over workspace
files; product web shows workspace research files.

## Django Ninja API

Django Ninja provides local/staff typed control APIs:

- `GET /api/health`
- `GET /api/harness/status`
- `GET /api/harness/components`
- `GET /api/harness/components/{component_id}`
- `GET /api/harness/optional-skills`
- `GET|POST /api/harness/strategies`
- `GET|PATCH|DELETE /api/harness/strategies/{name}`
- `POST /api/harness/strategies/{name}/activate|archive`
- `GET /api/harness/subagents/prompt` returns the starter prompt plus
  `intake_summary` for idea translation, plain-language workflow explanation,
  blocked-action reasons, next allowed actions, stage exit criteria, and direct
  profile questions
- `POST /api/harness/subagents/loop` returns closed Artifact Supervisor Loop
  planner actions for artifact paths and can optionally record file-native
  pending tasks/escalation proposals without spawning agents
- `GET /api/subagents`
- `GET /api/subagents/{role}/skills`
- `GET|POST /api/subagents/{role}/optional-skills`
- `GET|PATCH|DELETE /api/subagents/{role}/optional-skills/{name}`
- `POST /api/subagents/{role}/optional-skills/{name}/activate|archive`
- `GET /api/workflows/{id}`
- `POST /api/workflows/{id}/validate`
- `POST /api/policy/simulate`
- `GET|POST /api/orders/tickets`; list responses are scoped to the active
  profile (`portfolio_id`, `account_id`, `strategy_id`)
- `GET /api/orders/tickets/{ticket_id}`
- `POST /api/orders/tickets/{ticket_id}/checks`
- `POST /api/orders/tickets/{ticket_id}/approval-request` local control only; Codex risk-manager workflows should prefer MCP `request_order_approval`
- `POST /api/approvals`
- `POST /api/executions/submit-approved`
- `GET /api/audit/events` returns recent audit events for the API process
  workspace identity only
- `GET /api/portfolio/snapshot`
- `GET /api/portfolio/reconciliations`
- `GET /api/brokers`
- `GET /api/brokers/{broker_id}`
- `POST /api/brokers/{broker_id}/sync`
- `POST /api/research/artifacts`
- `GET /api/research/artifacts`
- `GET /api/research/artifacts/{artifact_id}`
- `POST /api/research/artifacts/{artifact_id}/export`
- `POST /api/research/search`
- `POST /api/research/source-snapshots`

The canonical approval and execution routes are `/api/approvals` and
`/api/executions/submit-approved`. Approval and execution routes do not have
`/api/orders/*` aliases in the `0.2.0` contract.

OpenAPI docs are staff-protected. REST is for operations, validation,
inspection, and local control. Codex-native workflows should prefer
role-scoped MCP tools so tool annotations, role allowlists, call ledgers, and
workspace provenance stay in the same approved action boundary.

## MCP Boundary

TradingCodex exposes the official Codex MCP path through project-scoped stdio:
`tcx mcp stdio`. MCP is intentionally selected service-layer use cases, not an
automatic REST mirror.

Minimum MCP protocol surface:

- `initialize`
- `tools/list`
- `tools/call`
- `resources/list`
- `prompts/list`

Minimum MCP tools:

- `get_tradingcodex_status`
- `list_broker_connections`
- `get_broker_connection_status`
- `sync_broker_account`
- `list_reconciliation_runs`
- `create_order_ticket`
- `run_order_checks`
- `request_order_approval`
- `get_order_ticket`
- `list_order_tickets`
- `submit_approved_order`
- `cancel_approved_order`
- `refresh_broker_order_status`
- `get_order_status`
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
- `list_external_mcp_connections`
- `register_external_mcp_connection`
- `check_external_mcp_connection`
- `discover_external_mcp_connection`
- `review_external_mcp_tool`
- `record_audit_event`

Every MCP tool definition includes stable name, description, input schema,
category, risk level, role allowlist, approval requirement, audit requirement,
and standard MCP hints for read-only, destructive, idempotent, and open-world
behavior. `tools/list` returns this metadata as tool annotations.
Research artifact write tools accept the handoff metadata validated by
`tcx quality-check --strict`.
`tools/call` records `McpToolCall` rows with principal, status, request/result
hashes, errors, and duration, except research tools and
`list_workflow_artifacts`, which are excluded so research payloads remain only
in workspace files.

`tcx mcp stdio` calls the same service layer as CLI, API, and web surfaces. The
stdio bridge must never write non-MCP logs to stdout.

The project MCP server is named `tradingcodex` and carries the current
workspace provenance. Optional global safe MCP is named `tradingcodex-home`,
limits the server-side tool surface to read-only/status/search tools, and must
not expose approval, execution, cancellation, policy mutation, secret, broker
sync, broker mapping mutation, or order-ticket mutation tools.

### External MCP Gate

External MCP servers can be registered through product web as managed
connections. Product web can run the same check/discover lifecycle used by CLI
and MCP tools. TradingCodex stores connection metadata, imported `tools/list`,
`resources/list`, and `prompts/list` records, schema hashes, risk categories,
canonical capability mappings, role scopes, and proxy decisions in the central
DB.

External MCP tools are not automatically exposed to Codex. Discovery imports
default to review-required policy. Unknown, secret, policy/admin, and direct
execution tools are disabled until classified; execution-like external tools
must map to a TradingCodex service connection path instead of direct raw proxy.
Broker/account private-read tools such as balances, positions, orders, fills,
and buying power must be managed by External MCP Gate with role scope and audit.
Public market data, news, and filings MCP may remain lightweight, but when used
for order, risk, approval, or portfolio decisions they must be captured through
source snapshots or research artifacts.

## Role-Specific MCP Exposure

The root `head-manager` allowlist exposes research, audit, portfolio/status,
and policy simulation tools, but excludes approval creation and execution
submission. Role-scoped agent TOML files expose narrower risky tools only to
their owner roles:

- `risk-manager` can create approval receipts.
- `execution-operator` can call experimental submit/cancel execution tools.

MCP registry role allowlists are a second boundary after `.codex/agents/*.toml`.
MCP tool execution also checks active requester identities (`Principal`) and
matching action permissions (`Capability`).

## CLI

The CLI entrypoint is `tcx`.

Top-level commands:

- `tcx attach [workspace] [--overwrite]`
- `tcx init <workspace> [--overwrite]`
- `tcx update [workspace] [--no-doctor]`
- `tcx doctor [--layer <name>]`
- `tcx workspace status|list`
- `tcx profile status|list|create|select|update`
- `tcx subagents status|list|inspect|diff|project|plan|loop|skills|prompt|state`
- `tcx skills list [--all]|inspect|propose-add|propose-update|apply-proposal`
- `tcx research create|append|get|list|search|export`
- `tcx policy simulate`
- `tcx db status|path|migrate`
- `tcx mcp call <tool> [tool args]`
- `tcx mcp ledger [--tool <name>] [--principal <id>] [--status ok]`
- `tcx mcp install-global --safe`
- `tcx mcp stdio`
- `tcx service runserver`
- `tcx service ensure`
- `tcx service status [--json]`

Generated workspace wrapper commands:

- `./tcx doctor`
- `./tcx update [--no-doctor] [--skip-refresh]`
- `./tcx update status [--json]`
- `./tcx mode status`
- `./tcx mode set build --reason "<reason>"`
- `./tcx mode set operate`
- `./tcx connectors status`
- `./tcx connectors providers`
- `./tcx connectors connect <broker> [--provider <provider-id>] [--credential-ref <ref>] [--mode read-only|validation|live-request]`
- `./tcx connectors scaffold <broker-id> [--provider <provider-id>] [--credential-ref <ref>] [--environment <env>]`
- `./tcx connectors register --provider <provider-id> --broker-id <id> --credential-ref <ref> [--environment <env>]`
- `./tcx connectors validate <broker-id>`
- `./tcx workspace status|list`
- `./tcx profile status|list|create|select|update`
- `./tcx subagents status`
- `./tcx subagents prompt [--json|--explain] "<request>"`
- `./tcx subagents loop --request "<request>" --artifact <path> [--record]`
- `./tcx skills optional list|inspect|create|update|activate|archive|delete`

Connector setup is provider-first. Core ships the `paper` provider only; a
named broker request routes to `$tcx-build` to install or develop a reviewed
provider, then registration stores only provider metadata and `credential_ref`.
- `./tcx strategies list|inspect|create|update|activate|archive|delete`
- `./tcx validate order <path>`
- `./tcx approve <path>`
- `./tcx db status|path|migrate`
- `./tcx mcp call <tool>`
- `./tcx mcp ledger [--tool <name>]`
- `./tcx research create|append|get|list|search|export`

Default main-agent skill listing is user-facing, not exhaustive. It shows only
direct user entrypoints: `tcx-workflow`, `tcx-server`, `tcx-build`,
`strategy-creator`, `postmortem`, and active `strategy-*` skills. Full
inspection is available through
`./tcx skills list --all` and role-specific `./tcx subagents skills <role>`.

Optional-skill and strategy CRUD CLI commands call the same shared application
service used by Django web/API and mainagent guidance.
Additional instruction edits are web-first and file-native; they are stored
under `.tradingcodex/agent-instructions/` and reflected in generated projection
indexes.
