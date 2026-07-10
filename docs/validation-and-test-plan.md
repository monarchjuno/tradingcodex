# Validation And Test Plan

This document owns required validation commands, unit/API/generator/smoke test
coverage, and release-sensitive verification expectations.

## Default Validation

Run after source or test changes:

```bash
pytest
```

Run after Django settings, model, admin, API, MCP, or service changes:

```bash
python manage.py check
```

Run after broad Python migration, package, or import-structure changes:

```bash
python -m compileall tradingcodex_cli tradingcodex_service apps tests
```

## Unit Test Expectations

Unit tests should cover:

- policy decisions, restricted list, limits, capability checks
- order ticket checks, JSON order input validation, approval validation, execution preconditions
- order ticket creation, state transitions, check pass/warn/fail recording,
  approval readiness, exact approval-scope hash validation, broker order
  events, and fill deduplication
- stale approval-table gates: expired `valid_until`, cash delta, order-status
  delta, receipt-embedded stale metadata, and metadata-free legacy warning
- approved-order idempotency and duplicate execution blocking
- broker connection defaults, broker account discovery, read-only sync runs,
  portfolio ledger event creation, snapshot materialization, and
  reconciliation summaries
- principal/capability checks before MCP handler dispatch and policy decisions
- universe routing and readiness labels
- adapter registry and disabled live adapter behavior
- audit append behavior and request/result hash generation
- file-native research artifact creation, versioning, search, source snapshot recording, and markdown export
- central DB path resolution through `TRADINGCODEX_HOME` and `TRADINGCODEX_DB_NAME`
- workspace identity/provenance recording without workspace-local DB partitioning
- duplicate research ids fail closed within a workspace unless an explicit append/version path is used
- duplicate order ids fail closed through the central runtime ledger unless an explicit idempotent path is used
- harness component registry uniqueness, dependency validity, taxonomy tag coverage, and tag filtering

## API And Admin Test Expectations

API/Admin tests should cover:

- Ninja endpoints return typed schemas and reject unauthorized calls
- harness component endpoints expose the static component registry and return 404 for unknown component ids
- Django Admin uses default admin templates, default model registration, and no custom TradingCodex admin actions/CSS/dashboard
- service-layer MCP registry, policy, and adapter helpers create audit events when called directly by supported service/API/CLI paths
- agent/skill file projection tests cover proposal files, generated manifest, and blocked risky assignments without writing skill DB or AuditEvent state
- `tcx mcp stdio` handles JSON-RPC `initialize`, `tools/list`, and `tools/call`
- MCP research tools store and retrieve workspace markdown/source-snapshot JSON through the service layer without writing research DB rows, audit rows, or tool-call ledger rows
- non-research MCP tool calls create DB ledger entries with request/result hashes
- generated `./tcx mcp ledger` can inspect the central DB tool-call ledger
- stdio bridge returns valid MCP messages and writes no non-MCP stdout
- Broker Center and order-ticket API endpoints expose read/status/draft/check
  behavior without bypassing approval or approved action gates

## Generated Workspace Smoke Tests

Run after template/bootstrap behavior changes:

```bash
rm -rf /tmp/tradingcodex-smoke
python -m tradingcodex_cli attach /tmp/tradingcodex-smoke
cd /tmp/tradingcodex-smoke
./tcx doctor
./tcx workspace status
./tcx profile status
```

Smoke coverage should verify:

- `tcx attach` and `tcx init` create the workspace contract
- `tcx update` refreshes an existing generated workspace while preserving
  `workspace_id` and active profile
- generated workspace contains `.tradingcodex/workspace.json`
- generated workspace contains `.tradingcodex/generated/component-index.json`
- generated workspace contains no `package.json` or Node MCP/runtime files
- generated workspace contains ten fixed subagents and twenty-six core repo skills
- two generated workspaces have different workspace ids
- two generated workspaces keep separate research markdown/source-snapshot files while sharing non-research MCP ledger rows through the central DB
- profile selection controls paper portfolio separation
- all fixed-role MCP allowlists match `AGENT_SPECS` and runtime tool annotations
- generated hooks are callable, auto-route plain investment prompts, ignore non-investment prompts, and classify secret-warning cases
- component index matches the Python component registry

## Research Memory Smoke Tests

Run after research-memory changes:

```bash
./tcx research create
./tcx research append
./tcx research search
./tcx research export
./tcx research run-card
./tcx research validation-card
./tcx workflow improve
```

The smoke flow should confirm:

- workspace markdown artifact creation
- source/as-of metadata preservation
- version and content hash updates
- duplicate create with changed content is rejected within the same workspace
- markdown export path generation
- artifact `improvements` preview or record investment judgment improve records
- `.tradingcodex/mainagent/improve-index.json` updates incrementally alongside
  `.tradingcodex/mainagent/improve.jsonl`
- workspace provenance recording
- no raw secrets in exported output

## MCP Smoke Tests

Run after MCP registry, handler, bridge, or role allowlist changes:

```bash
./tcx mcp stdio
./tcx mcp install-global --safe --print
```

Verify at least:

- `tools/list`
- tool annotations include category, risk, role allowlist, approval requirement, and audit requirement
- research/status tools are visible to `head-manager`
- approval creation is not visible to `head-manager`
- approval creation is visible only to the approved risk role path
- experimental execution tools are visible only to `execution-operator`
- `tradingcodex-home` safe scope exposes only read-only/status/search tools
- `tradingcodex-home` safe scope may expose broker/order read-status tools
  such as `list_broker_connections`, `get_broker_connection_status`,
  `list_order_tickets`, `get_order_ticket`, and `list_reconciliation_runs`,
  but not sync, approval, submit, cancel, mapping mutation, or order-ticket
  mutation tools
- stdio emits no non-MCP logs to stdout
- external MCP discovery classifies market-data, account-read, and
  execution-like tools while keeping raw execution proxy blocked
- `./tcx mcp external list/register/check/discover/review-tool` covers External
  MCP Gate lifecycle operations
- schema drift disables reviewed tools until re-reviewed
- `validate_order_approval_crosswalk` and `get_pre_approval_occupancy` are
  read-only tools, visible only to order-aware roles and safe-home read scope,
  with no approval, submit, cancel, sync, or order-ticket mutation authority.
- Crosswalk tests must cover missing broker order ids, duplicate replacement
  lineage, stale originals, and unresolved `ACKED` / broker `unknown` rows that
  set `terminal_inference_allowed: false`.
- Crosswalk lookups are fail-closed: a broker-order lookup failure returns
  `status: error` with `error: broker_order_lookup_failed`, a failure reason,
  and empty rows; a filtered lookup that matches nothing returns
  `status: no_match` with empty rows and never falls back to unrelated
  approvals; a `VOIDED` ticket with no replacement or manual-execution
  successor raises the `voided_without_successor` anomaly.
- Unattributed broker fills discovered by `sync_broker_account` are exposed
  explicitly (`unattributed_fills`, `list_unattributed_fills`) and persisted as
  ledger events; `annotate_manual_execution` links a fill to an existing
  `VOIDED` ticket only with explicit evidence and matching
  symbol/side/currency, records provenance `manual`, computes realized amounts
  from broker fill data, and never fabricates canonical fills or broker
  orders.
- Occupancy tests must cover approved-not-submitted rows, unresolved unknown
  rows, conservative reserved notional, and overlap disposition of `blocked` or
  `conservative_exclusion` before approval creation.
- `get_resting_lifecycle_panel` is a read-only tool, visible only to
  order-aware roles and safe-home read scope, with no approval, submit, cancel,
  sync, or order-ticket mutation authority.
- Resting lifecycle tests must cover: a working order past 20:00 KST without
  terminal evidence renders `terminal_blocked` (never `expired`) with a
  `no_terminal_evidence` gap and no DB mutation; two consecutive missed
  cadence checkpoints render `ttl_stale`; `resting` requires at least one
  observed `status_refreshed` event; unknown broker status renders
  `anomaly_unverified`; checkpoint output stays bounded (aggregate counts plus
  a short tail); and identical `as_of` inputs return identical rows.

## Broker Provider Smoke

Run after connector, broker adapter, order-ticket, approval, execution, or
policy changes. Use a disposable workspace and a disposable runtime DB; keep
credentials in process environment only. Core ships paper only, so a real broker
smoke starts by installing or developing a provider for the requested broker.
For repository validation, use the fake provider integration tests unless a
reviewed provider has been added:

```bash
rm -rf /tmp/tradingcodex-provider-smoke /tmp/tradingcodex-provider-home
TRADINGCODEX_HOME=/tmp/tradingcodex-provider-home python -m tradingcodex_cli attach /tmp/tradingcodex-provider-smoke
cd /tmp/tradingcodex-provider-smoke
export TRADINGCODEX_HOME=/tmp/tradingcodex-provider-home
./tcx doctor
./tcx connectors providers
./tcx connectors connect requested-broker --provider requested-broker --credential-ref env:REQUESTED_BROKER --environment live --mode read-only
./tcx connectors scaffold requested-broker --provider requested-broker --credential-ref env:REQUESTED_BROKER --environment live
./tcx connectors validate requested-broker
python -m pytest tests/test_broker_center_prd.py -q
```

`register_broker_connector` should not by itself make a live-capable connector
execution-ready. A validation or live connector starts locked/read-only with
`credential_validation_status: not_checked`; `get_broker_connection_status` or
a successful account sync must prove signed health before validation scopes are
enabled, and live scopes require the separate live gate. If signed health fails,
the connection must remain/read back as locked with no enabled trade scopes, and
order checks or submit preflight must stop before consuming execution
idempotency. Authentication or permission failures must expose a secret-free
diagnostic in `health.details` and `metadata.credential_validation_details`.

Also verify the generated agent contract for broker-validation workflows:

```bash
./tcx doctor --layer codex-native
./tcx doctor --layer improvement
./tcx subagents status
./tcx subagents inspect execution-operator
./tcx subagents inspect risk-manager
./tcx skills list --all
printf '{"prompt":"Configure a reviewed test/sandbox broker connector, validate an approved order path, do not read secrets, and do not call broker APIs directly."}\n' \
  | python .codex/hooks/tradingcodex_hook.py user-prompt-submit
printf '{"prompt":"Configure a reviewed test or sandbox broker connector only. No order, no approval, no execution, do not read secrets."}\n' \
  | python .codex/hooks/tradingcodex_hook.py user-prompt-submit
./tcx subagents prompt "Configure a reviewed test or sandbox broker connector only. No order, no approval, no execution, do not read secrets."
```

Treat the smoke as failed if generated agent instructions hard-code one broker
as the only supported path, if `head-manager` can submit orders, if
`execution-operator` lacks `submit_approved_order`, if raw broker APIs appear in
Codex MCP config, if connector-only work dispatches fixed-role execution
subagents, or if the hook routes a secret-only prompt into execution.

## Harness And Routing Tests

Run targeted scenario tests after harness or workflow routing changes. Inspect
logs/results rather than relying only on static checks.

Scenarios should include:

- broad investment request asks for workflow confirmation or starter prompt
- explicit `$tcx-workflow` routes to the selected role team
- connector build prompts that name a provider route to `connector_build`
  and do not dispatch investment subagents
- negated execution wording such as "no order" stays out of execution routing
- guardrail-verification wording does not trigger execution
- secret-only credential, token, broker-key, password, or `.env` prompts create
  secret-wall warning context without subagent dispatch
- earnings/catalyst/valuation requests route to thesis-review style research
- vague public-equity prompts route to deep thesis review unless narrowed by
  explicit constraints such as "chart only", "company facts only", "no news",
  or "no valuation"
- fact-only and technical-only prompts keep the role team narrow and skip
  independent judgment review unless broader judgment is requested
- backtest, signal, and model-performance prompts require anti-overfit
  validation without implying strategy authoring or execution
- strategy authoring prompts route to `strategy-creator`/strategy CRUD instead
  of investment subagent auto-dispatch
- valuation plus portfolio-fit prompts include valuation before portfolio/risk
  review
- starter-prompt web previews show plain-language workflow labels, selected
  roles, blocked actions, and investor-profile gaps for decision/portfolio
  lanes without creating approvals, executions, MCP calls, or audit events
- starter-prompt CLI/API/web intake reuses answered active-profile investor
  context and only asks unanswered suitability/profile questions
- starter-prompt next allowed actions distinguish unanswered, partially
  answered, and complete active-profile investor context
- starter-prompt web profile-answer forms persist answers to the active profile
  and the refreshed preview removes those questions
- Codex `UserPromptSubmit` generated hooks keep compact intake hints under
  budget; `$tcx-workflow` reuses answered active-profile investor context when
  drafting the validated staged plan
- unavailable or unverified subagent routing fails closed
- completed role artifacts are reused when quality gates pass
- downstream roles return `revise`, `blocked`, or `waiting` instead of filling missing upstream role work
- hook `additionalContext` stays compact and points to persisted workflow
  intake instead of injecting a full starter prompt into every routed turn
- starter prompts and generated guidance expose the no-overlap handoff contract
- starter prompts and generated guidance tell subagents to write reader-facing
  research artifacts in the user's language unless explicitly overridden
- starter prompts and generated guidance tell `head-manager` to keep final chat
  brief while saving full accepted-artifact synthesis as a Markdown report under
  `trading/reports/head-manager/`
- `tcx quality-check <artifact> --strict` fails research markdown that lacks
  source/as-of posture, `context_summary`, material claim tags, handoff state,
  confidence, missing-evidence fields, next-recipient routing, blocked actions,
  or source snapshot metadata
- `tcx quality-check <artifact> --strict` validates
  `trading/forecasts/*.jsonl` forecast records and fails malformed probability
  ranges, missing resolution fields, or invalid open/closed status
- `tcx quality-check <artifact>.run-card.json --strict` validates Evidence Run
  Card config hash, input/source refs, artifact hashes, metrics or validation
  summary, warnings, limitations, timestamp, and evidence-only authority
- `tcx quality-check <artifact>.validation-card.json --strict` validates
  Validation Card evidence-quality labels, anti-overfit evidence metadata,
  artifact hashes, metrics or validation summary, warnings, limitations,
  timestamp, and evidence-only authority
- `tcx quality-check trading/research/source-snapshots/<id>.json` surfaces
  data-boundary warnings for OHLC invariants, non-positive prices, duplicate or
  sparse bars, timezone/as-of ambiguity, adjustment ambiguity, stale sources,
  invalid JSON constants, and missing fallback policy
- generated starter prompts and subagent-management skills include a context
  budget so agents pass artifact paths, context summaries, source snapshot IDs,
  and short deltas instead of full artifacts or repeated role manuals
- multi-round subagent smokes run `tcx subagents context-audit --strict` after
  several workflow intakes, subagent start/stop events, and large research
  artifacts across research-only, thesis, portfolio/risk, order-draft,
  approval/execution, crypto, ETF/index, and options/instrument lanes; the
  audit must show compact hook intake, compact intake history, compact session
  state with total counters plus retained recent events, no pasted markdown artifacts in
  intake/history/state, no research artifacts missing `context_summary`, and
  warnings for artifacts missing reader-first `reader_summary` or `next_action`
- repo skill boundary tests fail when role identifiers leak into generic skills
  outside necessary command principal examples or policy/artifact contracts
- MCP `tools/list` exposes both TradingCodex custom annotations and standard
  MCP hints such as `readOnlyHint`, `destructiveHint`, `idempotentHint`, and
  `openWorldHint`
- Django web additional-agent-instruction edits are saved as-is, projected
  after generated defaults, and removable without leaving stale marker blocks
- `tcx doctor --layer task-harness` is rejected; `improvement` is the canonical
  layer name in the `0.2.0` contract

Harness taxonomy checks should confirm:

- product web opens on workflow planning and still presents an agents/skills
  browser with markdown previews
- Guardrails are split into Guidance, Enforcement, and Information barriers
- Improvement is separate from Guardrails
- `tcx doctor --layer improvement` runs the quality/workflow checks

## Release-Sensitive Validation

Before release or packaging changes, run:

```bash
python -m pytest
python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall tradingcodex_cli tradingcodex_service apps tests
python -m build
python -m twine check dist/*
```

Also install the built wheel in a clean environment and run:

```bash
tcx init .
./tcx doctor
```

Detailed release workflow lives in [deployment.md](./deployment.md).
