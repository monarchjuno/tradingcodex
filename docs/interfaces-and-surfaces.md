# Interfaces And Surfaces

This document owns the behavior of TradingCodex product web, Django Admin,
Django Ninja API, stdio MCP, CLI, and generated workspace wrappers.

## Interface Rule

Every interface is a caller of the service layer. No interface may create a
parallel policy, order, approval, execution, portfolio, research, or audit path.
The v1 public routes and imports use the canonical
`tradingcodex_service/application/` services and
`tradingcodex_cli/commands/` modules directly.

| Surface | Primary role | Must not do |
| --- | --- | --- |
| Product web | Read-only workspace selection, artifact/source review, skill projection, and system posture | Launch Codex, mutate workspace/service state, accept arbitrary paths, expose raw reasoning/tool payloads, or bypass policy, approval, and execution gates |
| Django Admin | Local/staff operations console | Bypass service-layer policy or audit |
| Django Ninja | Typed authenticated local/staff REST and operator-managed remote control API | Mirror every MCP tool automatically or bypass execution checks |
| MCP | Agent research, order-preparation, approval, status, one proof-protected current-turn order effect, proof-protected Build services, and scoped Brain/Strategy lifecycle | Expose raw submit/cancel/refresh mutations, accept protected calls without current hook proof, mirror raw REST endpoints, proxy raw broker APIs, or expose the model to runtime credentials |
| Root native action hook | Exact immediate submit/cancel plus exact-first-line `$tcx-order-allow`, `$tcx-build`, `$tcx-brain`, and `$tcx-strategy` current-turn admission with capability scope and proof injection where required | Accept free-form intent, combine scopes, run from subagents, elevate the Codex sandbox, or bypass service gates |
| CLI | Local operator and generated wrapper interface | Fork durable behavior away from services |

## Product Web App

TradingCodex provides a read-only React workspace viewer at `/`, not a
table-first Admin replacement or agent runtime. React 19, TypeScript, and Vite 8 source lives under
`frontend/`; the deterministic build is committed under
`tradingcodex_service/static/tradingcodex_web/` and served by Django and
WhiteNoise. Node 22 is a maintainer build dependency only. Installed packages
and generated workspaces do not run a Node server or npm.

The SPA keeps three stable hash sections:

- **Library** (`#/library`) browses workspace research, reports, sources, forecasts, and other
  accepted artifacts with sanitized previews and source/as-of posture.
- **Skills** (`#/skills`) inspects built-in, optional, and strategy projections
  plus sanitized guidance. It cannot invoke or modify them.
- **System** (`#/system`) shows workspace, internal paper-account scope,
  broker/data-source, permission, and order posture.

Decision Memory does not add a fourth top-level section. Native Codex handles
retrieval, replay, review, and lesson validation; Library exposes resulting
artifacts. Investor-context setup is a native skill/operator operation.

SPA navigation uses hash sections so Django needs only a GET shell at `/`.
`/admin/` remains Django Admin, `/api/` remains Django Ninja, and static paths
remain Django/WhiteNoise assets. Non-root product paths return `404`; browser
navigation stays under the root hash routes.

Library and Skills use list/detail navigation. Wide windows keep the list and
reader side by side; Codex in-app and other half-width desktop windows switch
to a full-width list-to-reader transition so neither pane is squeezed. At that
compact desktop width the registered-workspace rail becomes a horizontal
selector with branch and readiness context, while phone layouts keep the
two-row navigation and full-width controls. The viewer shows no composer,
active run, follow-up, or mutation control.

Markdown preview rendering uses the shared maintained parser/sanitizer service.
The client must not inject unsanitized workspace HTML.

Workspace selection is explicit and limited to registered state:

- `GET <web route>?workspace=<workspace_id>` stores the selected
  `WorkspaceContext` in the current browser session.
- The left rail lists up to 20 recently seen validated `WorkspaceContext` rows;
  half-width desktop and narrower layouts replace it with a select control so
  the Library, Skills, and System content receives the full window width.
- Web and API rendering use the selected workspace path only after its current
  v1 manifest and registered path validate. An explicitly unknown, unavailable,
  or stale selection returns an error and never falls back to another workspace.
  A request with no selection uses `TRADINGCODEX_WORKSPACE_ROOT`.
- Recent activity and role-inspector activity are filtered by the selected
  workspace identity so MCP calls, audit events, and workflow runs from another
  Codex workspace do not appear as current-workspace evidence.
- Opening a workspace requires an existing `.tradingcodex/workspace.json`
  manifest. The viewer cannot create or attach a workspace.
- This selector binds viewer and Django Ninja requests in the browser
  session. It does not change CLI, MCP stdio, or process-level environment
  behavior.

### Native Runtime And Workspace Viewer

Native Codex is the only agent runtime. Head Manager interprets requests,
creates lightweight analysis-run provenance, and dynamically dispatches exact
fixed roles under generated instructions, skills, hooks, TOML, MCP allowlists,
and service gates.

The Django product web is a separate read-only viewer. It selects only a
registered, currently valid attached workspace and returns a canonical snapshot
plus sanitized skill and artifact detail. It does not invoke `codex exec`,
preview prompts, start or resume runs, expose raw reasoning or tool payloads, or
mutate skill, strategy, policy, order, broker, or execution state.

`$tcx-dashboard` is the native Codex entrypoint for opening this viewer. It
opens the selected viewer destination in the Codex in-app browser by default and
uses external Browser Use only when the user explicitly requests an external
browser. Generated project configuration enables those two browser surfaces but
keeps general Computer Use and full CDP access disabled. If the requested
surface is unavailable, the skill returns the exact clickable viewer URL rather
than launching through the shell or silently switching browser surfaces.

### Product Web Boundary

- `GET /api/viewer/` returns the selected workspace snapshot as
  `{generated_at, sections}`; each section is `{ok, data}` or `{ok, error}`.
- `GET /api/viewer/skills/{skill_id}` and
  `GET /api/viewer/artifacts/{artifact_id}` return sanitized detail.
- The root SPA remains available when a query contains an invalid workspace id
  so the API error can render in the viewer; the API never silently falls back.
- The viewer exposes Library, Skills, and System only. Its left rail is the
  registered-workspace selector.
- The viewer has no POST, PATCH, or DELETE route and no loopback mutation
  exception. Administrative mutations remain on authenticated canonical
  surfaces outside the viewer.
- External MCP discovery and permission review must not expose raw external
  tools directly to Codex or turn user consent into order/execution approval.
- Execution-sensitive actions remain behind TradingCodex role, MCP, policy,
  approval, duplicate-request, connection, and audit checks. Analysis begins
  only in native Codex; the viewer cannot initiate it.

## Django Admin

Django Admin uses Django's default UI. It is a local/staff DB inspection and
bounded emergency edit surface, not a custom TradingCodex operations console.
The order ledger and MCP registry, connection, permission, and call-ledger
models are fail-closed and read-only in Admin: order tickets, approval receipts,
order-turn grants, execution results, broker orders, fills, check runs, order
events, and MCP state must be changed through their canonical services. Admin
exposes:

- policy, restricted symbols, capability allowlists, limits
- MCP tool registry and tool call ledger
- workflow runs, artifact refs, readiness labels
- order tickets, approval receipts, order-turn grants, execution results
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

Django Ninja provides authenticated local/staff and explicitly hardened remote
typed control APIs. Anonymous access is limited to read-only health/product
state. Cookie-authenticated staff mutations require CSRF. Header API keys do not
require CSRF, but their bound identity must be an active canonical principal.
Role-authored mutations call the same registered MCP service tools used by
stdio, including role allowlists, capability checks, input validation, and
transport-principal binding; a staff session does not itself confer an agent
role, even when a staff username collides with an active agent principal id.
Role-authored endpoints require an API-key-bound principal. Administrative
strategy and optional-skill operations remain available to CSRF-protected
staff, while API-key callers need an active canonical `head-manager` for those
administrative paths. The read-only viewer creates no anonymous mutation
exception:

- `GET /api/health`
- `GET /api/health/live`
- `GET /api/health/ready`
- `GET /api/viewer/` returns one canonical selected-workspace snapshot for
  Library, Skills, and System as `{generated_at, sections}`, where every
  section is either `{ok: true, data}` or `{ok: false, error}`. Strategies and
  optional skills are snapshot sections rather than a second frontend load path.
- `GET /api/viewer/skills/{skill_id}`
- `GET /api/viewer/artifacts/{artifact_id}`
- `GET /api/harness/status`
- `GET /api/harness/components`
- `GET /api/harness/components/{component_id}`
- `GET /api/harness/optional-skills`
- `GET|POST /api/harness/strategies`
- `GET|PATCH|DELETE /api/harness/strategies/{name}`
- `POST /api/harness/strategies/{name}/activate|archive`
- `GET /api/harness/subagents/prompt` returns a Codex-native starter instruction
  without classifying meaning or selecting roles
- `GET /api/subagents`
- `GET /api/subagents/{role}/skills`
- `GET|POST /api/subagents/{role}/optional-skills`
- `GET|PATCH|DELETE /api/subagents/{role}/optional-skills/{name}`
- `POST /api/subagents/{role}/optional-skills/{name}/activate|archive`
- `POST /api/workflows` creates a lightweight analysis run
- `GET /api/workflows/{id}` returns that run and its run-local artifacts
- `POST /api/policy/simulate`
- `GET|POST /api/orders/tickets`; list responses are scoped to the active
  profile (`portfolio_id`, `account_id`, `strategy_id`)
- `GET /api/orders/tickets/{ticket_id}`
- `POST /api/orders/tickets/{ticket_id}/checks`
- `POST /api/orders/tickets/{ticket_id}/approval-request` local control only; Codex risk-manager workflows should prefer MCP `request_order_approval`
- `POST /api/approvals`
- `GET /api/audit/events` returns recent audit events for the API process
  workspace identity only
- `GET /api/portfolio/snapshot`
- `GET /api/portfolio/reconciliations`
- `GET /api/brokers`
- `GET /api/brokers/{broker_id}`
- `POST /api/brokers/{broker_id}/sync`

Broker connection responses expose the required exact `provider_id` and
`transport`. They do not expose or accept a parallel adapter-type identity.
- `POST /api/research/artifacts`
- `GET /api/research/artifacts`
- `GET /api/research/artifacts/{artifact_id}`
- `POST /api/research/artifacts/{artifact_id}/export`
- `POST /api/research/search`
- `POST /api/research/source-snapshots`
- `POST|GET /api/research/specs`
- `GET /api/research/specs/{spec_id}`
- `POST /api/research/replay-manifests`
- `POST /api/research/experiments`
- `POST /api/research/causal-equity-analyses`
- `POST /api/research/judgment-priors`
- `POST /api/research/judgment-reviews`
- `POST /api/research/index/rebuild`
- `POST|GET /api/research/forecasts`
- `GET /api/research/forecasts/calibration`
- `GET /api/research/forecasts/{forecast_id}`
- `POST /api/research/forecasts/{forecast_id}/revisions`
- `POST /api/research/forecasts/{forecast_id}/resolution`
- `POST /api/research/forecasts/{forecast_id}/score`
- `POST /api/evaluations/corpora`
- `POST /api/evaluations/runs`
- `POST /api/evaluations/blind-reviews`
- `POST /api/evaluations/comparisons`

ResearchSpec, replay, experiment, causal-analysis, judgment, forecast, and
calibration routes are evidence-only. Causal equity analysis is bound to
`valuation-analyst`; blind priors/reviews and forecast resolution are bound to
`judgment-reviewer`. These routes cannot draft, approve, or execute orders.
The HTTP handlers dispatch these role-owned operations through their canonical
MCP tool definitions rather than maintaining a second REST-only role table.
Caller-supplied `created_by`, `role`, producer metadata, or order principal
cannot replace the identity bound by the staff/API-key transport.

`POST /api/research/specs` accepts a bundled `method_profile`. Common evidence
fields apply to every profile; `quant_signal_v1` adds preregistered signal,
trial, cost, capacity, and validation fields, while
`listed_equity_fcff_dcf_v1` adds instrument, driver, base-rate, scenario,
reconciliation, and independent-review plans. Evaluation corpus creation uses
`core_investment_v1` by default and may accept a bounded corpus-defined profile
with explicit required tags and metric dimensions. Evaluation runs bind an
`extension_profile_hash` so pristine and customized arms cannot be conflated.
Those hashes and deterministic check outcomes are currently caller-attested;
comparisons record unverified provenance and force `hold` until a trusted
evaluation runner supplies a verifiable runtime binding.

The canonical approval route is `/api/approvals`. REST exposes no final order
submit, submitted-order cancel, or broker-order status-refresh mutation. Final
effects enter only through an exact immediate root-native action or an exact
first-line `$tcx-order-allow` turn whose protected tool call carries current
hook-injected proof; both converge on the service-owned execution gateway.

OpenAPI docs are staff-protected. REST is for operations, validation,
inspection, and local control. Codex-native workflows should prefer
role-scoped MCP tools so tool annotations, role allowlists, call ledgers, and
workspace provenance stay in the same approved action boundary. Immediate
submit and cancel never enter through MCP. The current-turn path uses only the
Head Manager-scoped `use_order_turn_grant` tool, which is inert without proof
reserved and injected by `PreToolUse` for that exact turn.

## Root Native Final-Action Boundary

Only a root native Codex workspace user turn may request a final submit or
cancel. For identifiers already known when the turn begins, the complete
trimmed prompt must match one of the exact `$tcx-order-submit` or
`$tcx-order-cancel` `--name value` forms in
[Safety, Policy, And Execution](safety-policy-and-execution.md). Those two skill
bundles are explicit-only documentation and carry no tools.

The generated `UserPromptSubmit` hook recognizes the literal reserved token
before allocating an analysis run, deterministically parses the full prompt,
creates a workspace-bound `native-user` mandate, writes redacted audit metadata,
and calls `application/execution_gateway.py` in-process. It invokes no shell,
subprocess, public MCP tool, REST route, or model. Malformed reserved actions,
subagent turns, and the retired
`$execute-paper-order` form fail closed. The returned context is an allowlisted
result projection; canonical recovery comes from DB order status, not automatic
retry.

When the workflow must create or select canonical identifiers during the turn,
the physical first line may instead be exactly `$tcx-order-allow --mode
paper|validation|live`, followed by the normal interactive or Codex app
Scheduled Task request. `UserPromptSubmit` requires the root session and turn,
issues one workspace/session/turn/prompt/mode-bound `OrderTurnGrant`, and lets
normal orchestration continue. Root Head Manager alone can select one later
submit or cancel through `use_order_turn_grant`; `PreToolUse` reserves the grant
for the tool-use id and injects internal proof. A direct MCP caller or subagent
cannot supply that proof, and the browser viewer has no grant entrypoint. Consumption still enters the same
service-owned policy, approval, idempotency, live-confirmation, adapter, audit,
reconciliation, and uncertainty gates.

## Root Native Workspace-Change Boundary

Mutating Codex Build work begins only when the exact physical first line of a
root native prompt is `$tcx-build` and later lines contain a non-empty concrete
request. `UserPromptSubmit` issues a DB-canonical `BuildTurnGrant` bound to the
workspace, session, turn, cwd, and complete prompt. `PreToolUse` requires the
grant for controlled `trading/` edits and injects a one-time internal proof for
protected build MCP calls. Ordinary `apply_patch` edits outside `trading/` do
not consume Build authority; generic Write/Edit tools remain blocked. The grant
supports multiple build steps only in the current
turn; each mutating follow-up requires the exact marker again. Subagents cannot
mint, inherit, or use it, and the browser viewer has no Build path.

This is an intent gate, not a permission-profile switch. Actual Codex
permissions remain authoritative. The default `trading-research` profile
allows general computation, credential-free public retrieval, disposable temp
writes, and user-owned file changes outside `trading/`. Controlled `trading/`
or optional-role-skill lifecycle work starts in a fresh `trading-build` root turn. The
Build profile opens connector/build paths but denies protected runtime/DB,
credential, ledger, and network access. Codex Plan mode cannot issue or use a
grant, and a grant is bound to its issue-time permission mode. General
workspace-local shell, Python, and focused validation are available within the
profile; controlled `trading/` edits, trusted launcher commands, and protected
MCP mutations retain their deterministic Build checks.

Investment Brain and Strategy management use separate exact root markers:
`$tcx-brain` and `$tcx-strategy`. They stay in `trading-research`, issue a
DB-canonical grant with only the matching capability scope, and admit only the
canonical Brain source path plus `manage_investment_brain`, or Strategy body
staging plus `manage_strategy`. The hook owns and injects the MCP proof;
Research does not expose the generated CLI or attached runtime. Neither marker
can be combined with `$tcx-build`, an order marker, or the other managed skill;
Plan mode and subagents remain blocked.

This also applies to Codex app Scheduled Tasks. A recurring Build task works
only when its deliberately saved prompt starts with `$tcx-build`; every run
gets a fresh grant decision. Controlled `trading/` or optional-role-skill
lifecycle runs require a `trading-build` Automation runtime. Brain and Strategy
management starts directly with its matching exact marker in a
`trading-research` Automation runtime. A `trading-research` run may read
and write ordinary user-owned paths outside `trading/`, use temporary
computation, public evidence retrieval, rendering/inspection, and specifically
proof-protected canonical DB calls; Plan mode blocks Build entirely. Prefer an
isolated worktree or workspace and retain a reviewable diff. `$tcx-build` must
never be combined with `$tcx-order-allow`.

Persistent `tcx mode` is retired: `tcx mode status` is an inert compatibility
diagnostic, `tcx mode set ...` cannot grant authority, and any old
`.tradingcodex/runtime/mode.json` is ignored. External MCP consent instead uses
the explicit operator command `tcx mcp permission`. External MCP lifecycle and
consent mutations require an interactive user terminal and are rejected from
Head Manager's Build-turn MCP and shell paths. User-terminal CLI mutation is separate
operator authority and does not synthesize a Build turn.

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
- `begin_analysis_run`
- `list_broker_connections`
- `get_broker_connection_status`
- `sync_broker_account`
- `list_reconciliation_runs`
- `create_order_ticket`
- `run_order_checks`
- `request_order_approval`
- `get_order_ticket`
- `list_order_tickets`
- `use_order_turn_grant` (Head Manager only; requires current hook proof)
- `discard_draft_order`
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
- `create_research_spec`
- `get_research_spec`
- `list_research_specs`
- `create_replay_manifest`
- `record_experiment_run`
- `rebuild_research_index`
- `create_causal_equity_analysis`
- `record_blind_judgment_prior`
- `complete_judgment_review`
- `issue_forecast`
- `revise_forecast`
- `resolve_forecast`
- `score_forecast`
- `get_forecast`
- `list_forecasts`
- `get_forecast_calibration_report`
- `create_evaluation_corpus`
- `record_evaluation_run`
- `record_blind_human_review`
- `compare_evaluation_runs`
- `list_external_mcp_connections`
- `register_external_mcp_connection`
- `check_external_mcp_connection`
- `discover_external_mcp_connection`
- `review_external_mcp_tool`
- `record_audit_event`

The four External MCP lifecycle tools above are present for the explicit
operator service/CLI path but are omitted from the Head Manager allowlist and
rejected on agent stdio transport. `list_external_mcp_connections` remains the
read-only inspection surface.

Every MCP tool definition includes stable name, description, input schema,
category, risk level, role allowlist, approval requirement, audit requirement,
and standard MCP hints for read-only, destructive, idempotent, and open-world
behavior. `tools/list` returns this metadata as tool annotations.
For `record_source_snapshot`, normal agent/API requests omit service-owned
`snapshot_id`, `retrieved_at`, and `recorded_at`. TradingCodex stamps receipt
and storage time and returns a bounded ID. `known_at` is caller-supplied only
when the true knowable time is supported and timezone-qualified; any explicit
timestamp still passes the strict point-in-time ordering checks.
Every later snapshot read recomputes the content-addressed id and envelope
hash. Run-bound artifact writes derive the exact `source_snapshot_hashes`
mapping and include it in the authenticated artifact receipt, so rewriting and
self-rehashing a snapshot under its old id fails closed.
External MCP lifecycle calls identify a connection by `name`; tool review uses
either `tool_id` or `name` plus `external_name`. Numeric router identifiers and
router/tool-name aliases are not v1 inputs. `record_audit_event` accepts exactly
`{"event":{"type":...,"resource":...,"decision":...,"payload":{...}}}`;
`resource` and `decision` may be omitted and then normalize to an empty resource
and `recorded`, while top-level event fields and `action` aliases are rejected.
For a recorded workflow, research artifact write tools accept caller-owned
report/source fields plus the run/task reference. Canonical workflow semantics,
binding, identity, schema, and hashes are service-derived and reject overrides.
The MCP schema exposes structured follow-up and improvement objects. Before an
accepted run-bound write is receipted or published, the service validates its
exact intended Markdown bytes with the strict artifact-quality contract; a
failure leaves no file or receipt. Synthesis binding also rejects authenticated
inputs whose current-run handoff state is not `accepted`.
`tools/call` records `McpToolCall` rows with principal, status, request/result
hashes, errors, and duration, except research tools and
evaluation tools plus `list_workflow_artifacts`, which are excluded so
research/evaluation payloads remain only in workspace files.

`tcx mcp stdio` calls the same service layer as CLI, API, and web surfaces. The
stdio bridge must never write non-MCP logs to stdout.

Every stdio instance requires a transport principal. Generated root and role
config bind `TRADINGCODEX_MCP_PRINCIPAL`, and a caller-supplied payload
principal must match it. This prevents one projected role server from invoking
a tool as another role.

The project MCP server is named `tradingcodex` and carries the current
workspace provenance. Optional global safe MCP is named `tradingcodex-home`,
limits the server-side tool surface to read-only/status/search tools, and must
not expose approval, execution, cancellation, policy mutation, secret, broker
sync, broker mapping mutation, or order-ticket mutation tools.

### External MCP Gate

External MCP lifecycle mutation is available only through the explicit
interactive operator CLI. Product web and Django Admin may inspect managed
connections and lifecycle results, but they cannot register, import, check,
discover, or review them because those surfaces cannot receive the one-use
operator capability. TradingCodex stores connection metadata, imported
`tools/list`, `resources/list`, and `prompts/list` records, schema hashes, risk
categories, canonical capability mappings, role scopes, and proxy decisions in
the central DB.

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
policy simulation, and the proof-protected `use_order_turn_grant` tool, but
excludes approval creation and raw submit/cancel/refresh mutations. The
protected tool rejects every call without the current hook-injected proof. No
fixed-role allowlist or public/global MCP surface exposes a usable final submit,
cancel, or broker-status-refresh mutation. Role-scoped agent TOML files expose
other narrower risky tools only to their owner roles:

- `risk-manager` can create approval receipts.
- forecast authors may issue/revise only as themselves; `judgment-reviewer`
  independently resolves forecasts and cannot resolve its own forecast
- `valuation-analyst` owns deterministic causal equity analysis, while
  `judgment-reviewer` owns the blind prior and second-pass challenge review
- `head-manager` can freeze evaluation corpora, record control/candidate runs,
  and compare them; `judgment-reviewer` records model-identity-hidden human
  reviews. Evaluation authority remains research-only.

MCP registry role allowlists are a second boundary after `.codex/agents/*.toml`.
MCP tool execution also checks active requester identities (`Principal`) and
matching action permissions (`Capability`).

## CLI

The CLI entrypoint is `tcx`. `python -m tradingcodex_cli --help` is the current
high-level command inventory; use command-specific help for option details. The
workspace-facing surface is grouped as follows:

- workspace setup and health: `tcx attach`, `update`, `doctor`, `service`,
  `home`, and `workspace`
- analysis and durable context: `tcx workflow begin|show`, `tcx decision list|show|export`,
  `tcx decision snapshot list|record|show`, `tcx profile`, and
  `tcx investor-context`
- roles and reusable capability: `tcx subagents
  list|status|inspect|diff|project|state|context-audit|plan|skills|prompt`,
  `skills`, `strategies`, and `investment-brains`
- research and review: `tcx research`, `forecast`, `postmortem`, and
  `evaluation corpus|run|assign-review|review-packet|blind-review|compare`
- service and operator surfaces: `tcx db`, `policy`, `build`, `connectors`,
  and `mcp`

Generated launchers project `TRADINGCODEX_SERVICE_ADDR`. `tcx service status`,
`ensure`, `stop`, and `runserver` use that address when no positional address
is supplied. Release workspaces default to `127.0.0.1:48267`; development
bootstrap can select a separate checkout-scoped loopback port.

`tcx postmortem list|process-review|create|show` is available from the CLI;
lesson promotion is only available to the authenticated `judgment-reviewer`
through role-scoped MCP. `tcx mcp external import-codex --source
workspace|global|any --name <server>` and the other External MCP lifecycle
commands require an interactive operator terminal. The compatibility commands
`validate`, `risk-check`, `approve`, `quality-check`, and `audit` remain
available for their narrow documented paths but are not general workflow
entrypoints.

`tcx subagents plan <agent...>|--all` is an explicit fixed-roster and thread-
capacity preview. It validates the caller-named roles and shows deterministic
dispatch batches under the configured thread limit. It does not classify a
request, choose roles, create an analysis run, or persist a workflow plan.

Generated workspaces expose the same workspace-scoped command surface through
`./tcx` on POSIX and `tcx.cmd` on native Windows. In addition to the grouped
commands above, the generated launchers expose `./tcx update status [--json]`
and the retired, inert `./tcx mode status` compatibility diagnostic. Connector
setup remains provider-first through `./tcx connectors`; provider approval and
revocation require an interactive operator terminal.

`tcx subagents prompt` accepts an investment request and emits a Codex-native
starter prompt. `tcx subagents plan` accepts only explicit fixed-role ids or
`--all`; it is not a semantic planner. Optional-skill CRUD uses only `--role`.
The proposal commands retain their distinct `--to` target option because they
are a different proposal contract.

Connector setup is provider-first. Core ships the `paper` provider only; a
named broker request routes to `$tcx-build` to install or develop a reviewed
provider, then registration stores only provider metadata and `credential_ref`.
`inspect-provider` is inert and prints the exact source/bundle hashes for
review. Approval and revocation reject piped or automated stdin and are not
available through MCP, API, Admin, the browser viewer, Build, or Automation. Approval
creates a database-bound immutable snapshot; the running service must be
restarted before that exact snapshot can load.

Default main-agent skill listing is user-facing, not exhaustive. It shows only
direct user entrypoints: `tcx-plan`, `tcx-workflow`, `tcx-memory`,
`tcx-automate`, `tcx-dashboard`, `tcx-server`, `tcx-build`, `tcx-investor-context`,
`tcx-strategy`, `tcx-brain`, and active `strategy-*` skills.
Postmortem review is part of `tcx-memory`. Full inspection is available through
`./tcx skills list --all` and role-specific `./tcx subagents skills <role>`.

Optional-skill and strategy CRUD CLI commands call the same shared application
service used by the authenticated API and Head Manager guidance.
`tcx-brain` similarly routes installed Brain list, inspect, validate, install,
update, activate, deactivate, rollback, and remove through the canonical
Investment Brain application service while keeping user-owned source edits
workspace-file-native.
Additional instruction edits are native/CLI-managed and file-native; they are stored
under `.tradingcodex/agent-instructions/` and reflected in generated projection
indexes.
