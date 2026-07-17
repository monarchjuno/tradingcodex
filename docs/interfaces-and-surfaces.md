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
| Root native action hook | Exact immediate submit/cancel plus normalized first-meaningful-line `$tcx-order-allow`, `$tcx-build`, `$tcx-brain`, and `$tcx-strategy` current-turn admission with capability scope and proof injection where required | Accept free-form intent, mismatched skill links, combine scopes, run from subagents, elevate the Codex sandbox, or bypass service gates |
| CLI | Local operator and generated wrapper interface | Fork durable behavior away from services |

## Product Web App

TradingCodex provides a read-only React workspace viewer at `/`, not a
table-first Admin replacement or agent runtime. React 19, TypeScript, and Vite 8 source lives under
`frontend/`; the deterministic build is committed under
`tradingcodex_service/static/tradingcodex_web/` and served by Django and
WhiteNoise. Node 22 is a maintainer build dependency only. Installed packages
and generated workspaces do not run a Node server or npm.

The SPA keeps three stable hash sections:

- **Library** (`#/library`) browses workspace research, reports, sources,
  Dataset and Calculation cards, forecasts, and other accepted artifacts with
  sanitized previews, lineage, payload availability, and source/as-of posture.
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
plus sanitized skill, artifact, Dataset, and Calculation detail. Dataset views
show cards, manifest/schema/profile metadata, lineage, and payload availability;
Calculation views show cards, metrics, diagnostics, warnings, and reuse lineage.
The viewer never returns an unbounded Dataset payload or private
materialization; Dataset detail may include the same maximum-20-row bounded
profile sample as the canonical profile service. It
does not invoke `codex exec`, preview prompts, start or resume runs, expose raw
reasoning or tool payloads, or mutate skill, strategy, Dataset, Calculation,
policy, order, broker, or execution state.

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
  The snapshot includes bounded `datasets` and `calculations` card sections.
- `GET /api/viewer/skills/{skill_id}` and
  `GET /api/viewer/artifacts/{artifact_id}` return sanitized detail.
- `GET /api/viewer/datasets/{dataset_id}` returns manifest, schema/profile,
  lineage, quality warnings, withdrawal posture, and payload availability.
- `GET /api/viewer/calculations/{calculation_run_id}` returns verified typed
  metrics, diagnostics/warnings, fingerprint, and exact reuse lineage.
- Dataset and Calculation cards/details are read-only Library projections. No
  viewer route registers data, materializes a slice, prepares a calculation,
  runs `tcx-calc`, creates a reuse record, or records a Run.
- The root SPA remains available when a query contains an invalid workspace id
  so the API error can render in the viewer; the API never silently falls back.
- The viewer exposes Library, Skills, and System only. Its left rail is the
  registered-workspace selector.
- The viewer has no POST, PATCH, or DELETE route and no loopback mutation
  exception. Administrative mutations remain on authenticated canonical
  surfaces outside the viewer.
- The System capability inventory is sanitized and read-only; it never exposes
  launch configuration, secrets, raw plugin content, or management actions.
- Execution-sensitive actions remain behind TradingCodex role, MCP, policy,
  approval, duplicate-request, connection, and audit checks. Analysis begins
  only in native Codex; the viewer cannot initiate it.

## Django Admin

Django Admin uses Django's default UI. It is a local/staff DB inspection and
bounded emergency edit surface, not a custom TradingCodex operations console.
The order ledger and MCP registry and call-ledger
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

Agent, skill, strategy, research artifacts, Source Snapshots, Datasets, and
CalculationSpec/Run records
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
- `GET /api/viewer/datasets/{dataset_id}`
- `GET /api/viewer/calculations/{calculation_run_id}`
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
- `GET /api/research/catalog`
- `POST /api/research/catalog/search`
- `POST /api/research/catalog/rebuild`
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
first-meaningful-line `$tcx-order-allow` turn whose protected tool call carries current
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
the first meaningful line may instead be exactly `$tcx-order-allow --mode
paper|validation|live`, followed by a non-empty normal interactive or Codex app
Scheduled Task request. `UserPromptSubmit` requires the root session and turn,
accepts a Markdown skill link only when its label and target match the projected
workspace skill, issues one workspace/session/turn/original-prompt/mode-bound
`OrderTurnGrant`, and lets
normal orchestration continue. Root Head Manager alone can select one later
submit or cancel through `use_order_turn_grant`; `PreToolUse` reserves the grant
for the tool-use id and injects internal proof. A direct MCP caller or subagent
cannot supply that proof, and the browser viewer has no grant entrypoint. Consumption still enters the same
service-owned policy, approval, idempotency, live-confirmation, adapter, audit,
reconciliation, and uncertainty gates.

## Root Native Workspace-Change Boundary

Mutating Codex Build work begins only when the first meaningful invocation of a
root native prompt is `$tcx-build` and the same or later lines contain a
non-empty concrete request. The invocation may be a plain token or a Markdown
link whose label and target match the current workspace's projected skill.
`UserPromptSubmit` issues a DB-canonical `BuildTurnGrant` bound to the
workspace, session, turn, cwd, and original complete prompt. `PreToolUse` requires the
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
Build profile opens connector/build paths plus credential-free public HTTP(S)
and HTTPS Git retrieval but denies protected runtime/DB, credential, ledger,
authenticated or local/private network access, uploads, package installs,
remote mutation, and broker calls. Codex Plan mode cannot issue or use a
grant, and a grant is bound to its issue-time permission mode. Native
`apply_patch` is the Build edit surface. Its shell is restricted to public
GET/HEAD, enumerated read-only HTTPS Git, limited workspace `pwd`/`cat`/`ls`,
inert provider reads/hash/diff/Git inspection, exact isolated `py_compile`, and
allowlisted workspace-launcher commands. General interpreters, helper scripts,
test runners, build systems, shell composition, and model-authored POST are
blocked; controlled `trading/` edits, trusted launcher commands, and protected
MCP mutations retain their deterministic Build checks. Broader validation is
an explicit user-terminal or maintainer flow.

Investment Brain and Strategy management use separate exact invocations on the
first meaningful line: `$tcx-brain` and `$tcx-strategy`. A plain token or
matching projected link is accepted and the request may share the line or
follow it. They stay in `trading-research`, issue a
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
`.tradingcodex/runtime/mode.json` is ignored. User-installed Codex capability
management remains native Codex functionality and does not synthesize a Build
turn or TradingCodex authority.

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
- `list_artifact_catalog`
- `search_artifact_catalog`
- `append_research_artifact_version`
- `export_research_artifact_md`
- `record_source_snapshot`
- `search_datasets`
- `get_dataset_manifest`
- `profile_dataset`
- `materialize_dataset_slice`
- `record_dataset_snapshot`
- `prepare_calculation`
- `record_calculation_run`
- `search_calculations`
- `get_calculation_run`
- `compare_calculation_runs`
- `create_research_spec`
- `get_research_spec`
- `list_research_specs`
- `create_replay_manifest`
- `record_experiment_run`
- `rebuild_research_index`
- `rebuild_artifact_catalog`
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
- `list_codex_capabilities`
- `record_audit_event`

`list_codex_capabilities` is Head Manager's read-only, secret-free view of the
native Codex capability inventory. It performs no installation, refresh,
enablement, disablement, deletion, classification, or execution.

Every MCP tool definition includes stable name, description, input schema,
category, risk level, role allowlist, approval requirement, audit requirement,
and standard MCP hints for read-only, destructive, idempotent, and open-world
behavior. `tools/list` returns this metadata as tool annotations.
Dataset and Calculation visibility is role-composed. Head Manager receives
only `search_datasets`, `get_dataset_manifest`, and `search_calculations` for
planning. The six calculation roles—fundamental, technical, macro, valuation,
portfolio, and risk—also receive Dataset profile/record/materialize and
Calculation get/compare/prepare/record as applicable. News, instrument,
judgment review, Build, and the viewer do not gain mutation or execution
authority through these tools.

Dataset search returns L0 cards only: 20 by default, at most 200, with no rows
or expanded schema. Manifest lookup does not read the payload. Profile is
bounded to 20 sample rows. Materialization accepts typed selectors rather than
SQL and returns a scratch reference plus hash, never table rows in the tool
context. Prepared Calculation execution accepts only service-authored sidecar
identity and declared scratch-local input/output basenames. Search returns
cards; get returns one verified Run; compare aggregates only explicitly
requested runs and metrics on the server. Recording a declared tabular or
time-series output also registers its derived Dataset lineage.
For `record_source_snapshot`, normal agent/API requests omit service-owned
`snapshot_id`, `retrieved_at`, and `recorded_at`. TradingCodex stamps receipt
and storage time and returns a bounded ID. `known_at` is caller-supplied only
when the true knowable time is supported and timezone-qualified; any explicit
timestamp still passes the strict point-in-time ordering checks.
Every later snapshot read recomputes the content-addressed id and envelope
hash. Run-bound artifact writes derive the exact `source_snapshot_hashes`
mapping and include it in the authenticated artifact receipt, so rewriting and
self-rehashing a snapshot under its old id fails closed.
`record_audit_event` accepts exactly
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

### Codex Capability Inventory

Standalone MCP servers and skills, and installed plugin MCP servers, skills,
apps, and hooks, remain native Codex capabilities. Root and fixed-role agents
receive them through Codex configuration inheritance; TradingCodex overrides
only its own role-scoped `mcp_servers.tradingcodex` entry and projected
`tcx-*` skills. TradingCodex does not proxy or persist external lifecycle,
permission, risk, or call state.

The System viewer and `$tcx-server` can request `list_codex_capabilities` and
display only kind, id, label, scope, origin, enabled/availability, and parent
plugin. Missing CLI data, timeout, or damaged manifests produce a partial
inventory with bounded warnings. The surface is read-only and contains no
management commands or recommendations.

Repository and user skills come from Codex's `.agents/skills` discovery roots;
TradingCodex also inventories direct compatibility skills below
`CODEX_HOME/skills` as `user-legacy` without reading their bodies. For plugin
component files, the inventory reads only top-level app, MCP-server, and hook
identifiers, so an app bundle is shown by its component name rather than a
generic `app` label. Nested configuration, commands, URLs, environment values,
credentials, prompt bodies, skill bodies, and hook code are never returned.

This inventory is not a current-task `tools/list`. An `available` record means
the configuration was discovered and enabled when inventoried; it does not
prove the app or MCP tool was loaded into an already-running task. Conversely,
an external tool exposed to the current task can be callable even when the
sanitized inventory is partial. Head Manager and evidence roles therefore
inspect current callable tools, use the runtime's available deferred-tool
discovery surface, and
attempt the smallest relevant read-only call before declaring a provider or
data field unavailable. If a plugin, app, skill, or MCP server changed after
task start, use a new task or restart Codex before diagnosing installation.

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

`tcx research catalog list|search|rebuild` exposes the parallel v2 artifact
catalog. List and search lazily refresh a file-native projection across
research, reports, decisions, forecasts, and evaluation artifacts. Rebuild
deletes only that derived projection. Existing source files are never rewritten;
records with incomplete legacy metadata remain visibly `legacy_partial`, and
point-in-time searches exclude records without a valid qualifying cutoff.

`tcx postmortem list|process-review|create|show` is available from the CLI;
lesson promotion is only available to the authenticated `judgment-reviewer`
through role-scoped MCP. User capability lifecycle is managed through Codex,
not the TradingCodex CLI. The compatibility commands
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
provider before connector rendering and registration stores only provider
metadata and `credential_ref`. Public source is staged inertly under
`$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`; externally informed
bundles include `source-provenance.json`. `inspect-provider` is inert and prints
a secret-free provenance summary plus the exact bundle hash for review. In a
Build sandbox that cannot read the central ledger it returns a bundle-only
`service_check_required` posture; interactive inspection and approval still
resolve canonical approval state. A
provenance or helper change invalidates approval. Approval and revocation reject piped or automated stdin and are not
available through MCP, API, Admin, the browser viewer, Build, or Automation. Approval
creates a database-bound immutable snapshot; the running service must be
restarted before that exact snapshot can load, then connector rendering,
registration, and validation resume in a fresh Build turn. A
`provider_development_required` scaffold precedes provider implementation only
when the user explicitly asks for scaffold-only output.

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
