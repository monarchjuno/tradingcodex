# Roles, Skills, And Workflows

This document owns the fixed role roster, no-overlap role contract,
head-manager dispatch gate, agent handoff contract, skills, workflow routing,
subagent isolation, role-owned artifacts, and module graph.
Roles and workflows participate in both Harness child systems: Guardrails
through role boundaries and information barriers, and Improvement through
workflow quality, skill proposals, and postmortems.

## Fixed Role Roster

TradingCodex always uses `head-manager` as the main agent with nine fixed
subagents.

| Role | Responsibility | Never allowed |
| --- | --- | --- |
| `head-manager` | Workflow dispatch, subagent coordination, synthesis, validation/audit status tracking | Finalize investment conclusions without subagent output, call broker APIs directly |
| `fundamental-analyst` | Business, financial statement, official disclosure, and competitive analysis | order drafting, approval, execution, secret read |
| `technical-analyst` | Price action, trend, momentum, volume, volatility | order drafting, execution, standalone investment conclusion |
| `news-analyst` | News, disclosure events, macro events, narrative change | assert unverified rumors, execution |
| `macro-analyst` | Macro, rates, FX, commodities, liquidity, policy, cross-asset transmission | order drafting, execution, unsupported implementation claims |
| `instrument-analyst` | ETF/index, options/derivatives, crypto public market structure, credit-signal boundary, instrument mechanics | order drafting, execution, unsupported instrument execution claims |
| `valuation-analyst` | DCF, reverse DCF, multiples, scenario, expected return | approval, execution, broker API call |
| `portfolio-manager` | Portfolio fit, sizing, draft order ticket | self-approval, execution, arbitrary policy changes |
| `risk-manager` | Risk review, policy review, approval readiness, approval receipt | order drafting, execution, arbitrary policy changes |
| `execution-operator` | Submit approved order tickets through TradingCodex MCP | raw broker API, secret read, policy change |

## No-Overlap Role Contract

Roles own questions, not broad topics. A role may reference another role's
artifact, but it must not silently redo that role's work, fill missing evidence
for that role, or treat coordinator context as a substitute for an accepted
artifact.

| Role | Owns | Consumes | Must hand off |
| --- | --- | --- | --- |
| `head-manager` | intake, lane selection, role dispatch, artifact acceptance, conflict reconciliation, user synthesis | user request, accepted role artifacts, service status | selected lane/team, compact briefs, accepted artifacts, conflicts, next allowed action |
| `fundamental-analyst` | business model, financial statements, filings, economics, fundamental risks | assigned evidence and source references | evidence-backed fundamental report with source/as-of posture and missing evidence |
| `technical-analyst` | price action, trend, momentum, volume, volatility, liquidity setup | assigned market-data references | technical report with setup observations, data posture, confidence, and invalidation gaps |
| `news-analyst` | verified news, disclosures, event chronology, narrative change, source quality | assigned filings/news/source references | dated event report with factual timeline, source caveats, and unresolved claims |
| `macro-analyst` | macro, rates, FX, commodities, liquidity, policy, cross-asset transmission | assigned macro/source references and relevant role artifacts | macro transmission report with source/as-of posture and regime uncertainty |
| `instrument-analyst` | ETF/index methodology, options/derivatives, crypto public market structure, credit-signal boundary, instrument mechanics | assigned instrument/source references | instrument support report with mechanics, liquidity/support gaps, and no execution implication |
| `valuation-analyst` | valuation range, scenario assumptions, market-implied expectations, sensitivity | accepted research artifacts and user-stated method constraints | valuation report with assumptions, sensitivity, confidence, and readiness for portfolio/risk review |
| `portfolio-manager` | portfolio fit, sizing context, concentration, liquidity, opportunity cost, draft order-ticket readiness | accepted research/valuation artifacts and portfolio state | portfolio report and, only when allowed, draft order-ticket readiness or draft ticket |
| `risk-manager` | downside, restricted-list and policy readiness, limits, approval readiness, approval receipt | accepted portfolio/order artifacts, policy state, restricted-list state, audit evidence | risk/policy report, approval readiness state, approval receipt when allowed, or blocked reasons |
| `execution-operator` | approved submit/cancel/status through the TradingCodex service boundary; live only when every live gate passes | approved order ticket, matching approval receipt, policy allow state | execution result, MCP response, audit reference, or rejected/blocked reasons |

Downstream roles handle weak upstream work by returning a revision request or
`blocked` readiness state. They do not repair missing upstream analysis inside
their own artifact unless the missing work is explicitly within their owned
question.

## Handoff Quality Contract

Every role-to-role handoff is a quality artifact, not just a message. A useful
handoff is accepted only when it contains:

- artifact path or durable DB artifact reference
- original request and binding user constraints, when they affect scope
- role-owned findings with material claims marked `[factual]`, `[inference]`,
  or `[assumption]` where useful
- source/as-of/retrieved-at posture, stale-data warnings, and missing coverage
  for market-sensitive evidence
- confidence, uncertainty drivers, and missing evidence
- readiness label or support gap, using conservative labels
- frontmatter or structured metadata for `context_summary`, `reader_summary`,
  `next_action`, `handoff_state`, `confidence`, `missing_evidence`,
  `next_recipient`, `blocked_actions`, and `source_snapshot_ids` when the
  artifact is stored as workspace markdown
- role-boundary conflicts, if the task asks the role to cross its boundary
- next eligible recipient and actions that remain blocked

Handoff state is one of:

| State | Meaning |
| --- | --- |
| `accepted` | The artifact answers the owned role question and can be consumed downstream. |
| `revise` | The role stayed in bounds, but missing evidence or scope mismatch must be fixed before downstream use. |
| `blocked` | Policy, role boundary, unsupported instrument, stale data, or user constraint blocks downstream action. |
| `waiting` | Required role output does not exist yet, so `head-manager` may provide task briefs but no substantive synthesis. |

`head-manager` is responsible for accepting, revising, blocking, or waiting on
handoffs before moving a workflow forward. It must preserve unresolved
conflicts instead of averaging them into false consensus.

## Head-Manager Dispatch Gate

In investment workflows, `head-manager` is a dispatcher, coordinator, and
synthesizer, not the analyst. Security analysis, investment judgment,
valuation, technical analysis, news analysis, portfolio/risk review, order
drafting, approval, and execution requests must pass through the subagent
dispatch gate.

Natural-language investment requests are sufficient workflow activation for
fixed-role dispatch. Explicit subagent, parallel, delegated-agent, and
`$tcx-workflow` requests remain supported as manual-control entrypoints, but
they are not required before `head-manager` routes the work.

| Trigger | Handling |
| --- | --- |
| General investment request, such as "Analyze Apple stock" | `UserPromptSubmit` injects compact auto-dispatch context with lane, selected team, blocked actions, and the persisted prompt-gate path; `head-manager` dispatches or reuses selected subagents before analysis. |
| Explicit `$tcx-workflow` request | The workflow skill becomes the primary manual-control orchestrator and dispatches selected subagents. |
| Broker/provider build request, such as "connect this broker" | Route to the `connector_build` lane and `$tcx-build`; connect/scaffold/register/validate provider metadata without investment dispatch or live submission. |
| Runtime/server request, such as "open dashboard" or "check TradingCodex status" | Route to `$tcx-server`; report service/MCP/update posture and use CLI recovery commands without changing execution authority. |
| Explicit subagent/parallel/delegated request | `UserPromptSubmit` records the explicit activation source; the skill checks existing subagent state before creating/reusing sessions. |
| Strategy authoring request, such as "Create a quality income strategy" | Do not auto-dispatch investment subagents. Route through `strategy-creator`, CLI, API, or service-layer flows so the strategy skill is created and projected as a root/head-manager strategy entry. Django web previews strategies read-only. |
| Non-investment repository, docs, or harness administration request | No investment dispatch is required; `head-manager` follows normal Codex coding-agent behavior while preserving execution and secret guardrails. |
| Same run/role subagent is active | Wait or follow up instead of creating duplicates. |
| Same role artifact has passed quality gates | Reuse the artifact instead of duplicating work. |
| Codex `spawn_agent` schema cannot select exact fixed role | Treat role routing as `routing-unverified`; provide `waiting_for_subagent_dispatch` and task briefs only. |

The selected role team from compact hook context or the persisted starter
prompt is binding for the current lane. `head-manager` must not add roles
outside that team merely because they might be useful. For `research_only`, do
not add valuation, portfolio, risk, approval, or execution roles unless the
user later asks for valuation, decision support, portfolio fit, sizing, order
drafting, approval, or execution.

Negated scope terms are binding. Phrases such as "no valuation", "no order", or
"no trading" remove those actions or roles from routing instead of triggering
them as positive intent.

Broad public-equity review prompts such as "Analyze NVDA" default to the
smallest decision-useful lane: `thesis_review` with fundamental, technical,
news, and valuation roles. Explicit narrowing happens first, so "chart only"
stays technical-only, "company facts only" stays research-only, and "no
valuation" removes valuation while preserving the remaining broad thesis
review. This default does not add portfolio advice, order drafting, approval,
execution, broker access, or secret access.

Requests that combine valuation or decision support with portfolio fit, such
as "fair value and whether it fits my portfolio", route through valuation
before portfolio/risk review. Portfolio wording must not skip valuation when
the user explicitly asks for fair value, target price, recommendation, or
similar decision support.

Fail closed: if subagent dispatch is unavailable, the workflow waits.
`head-manager` must not fill the gap with direct analysis.

## Head-Manager Operating Style

For repository, CLI, Django, MCP, template, docs, test, and harness maintenance
work, `head-manager` follows the default Codex coding-agent style: concise
preambles before grouped tool work, plans only for meaningful multi-step tasks,
`rg`-first search, `apply_patch` for manual edits, focused validation before
broader checks, respect for dirty worktrees, and concise final handoffs.

This operating style is a working discipline, not an investment permission.
It does not weaken the dispatch gate, role-owned skill boundary, approved action
boundary, approval requirements, or information barriers.

## Allowed And Forbidden Head-Manager Responses

| Situation | Allowed response | Forbidden response |
| --- | --- | --- |
| Broad analysis such as "Analyze Apple stock" | auto-dispatch or reuse selected subagents, then wait for outputs before synthesis | Direct business/price/news/recommendation analysis |
| Explicit workflow request such as "$tcx-workflow analyze Apple" | Spawn selected team or reuse active/completed roles, wait for outputs, then synthesize | Analyze without dispatch |
| Broker/provider build request | Check full-access plus TCX build mode, connect/scaffold/register/validate provider metadata through `$tcx-build`, and keep live submission inside service gates | Dispatch investment subagents, ask for raw secrets, or expose raw broker SDK tools |
| Decision support such as "Should I buy?" | Dispatch analyst/valuation/portfolio/risk team and explain required artifacts/gates | Offer buy/sell opinion without subagent output |
| Dispatch unavailable, role routing unverified, or dispatch failed | Provide `waiting_for_subagent_dispatch` state and task briefs only | Switch to "I will analyze it myself" |
| Subagent artifacts exist | Summarize role outputs, conflicts, confidence/missing evidence, and next allowed action | Override subagent evidence with unsupported certainty |
| Financial judgment is ready for synthesis | Run a challenge review against accepted artifacts, contrary evidence, profile gaps, policy conflicts, and selected strategy rules | Smooth conflicts into a stronger conclusion without naming the objection |

## Skills And Context

Head-manager and strategy skills live under `.agents/skills/*` so they are
discoverable to the root workspace coordinator. Role-owned subagent skills live
under `.tradingcodex/subagents/skills/*`; each fixed-role TOML file projects
only that role's allowed skill source list into developer instructions so a
custom subagent can apply its own procedures without importing root, strategy,
or out-of-role skill files.

Instruction/skill separation:

| Surface | Owns | Must not own |
| --- | --- | --- |
| `head-manager` base instructions | durable identity, safety invariants, dispatch fail-closed rule, role boundaries, approved action boundary, skill routing | workflow templates, scenario tables, long checklists, subagent message bodies |
| Head-manager skills | compact repeatable procedures for workflow routing, server/runtime recovery, build-mode work, strategy creation, and postmortems | role identity, durable routing authority, MCP allowlists, weakening base guardrails, bypassing role-owned skills, approving or executing directly |
| Fixed subagent TOML | standing role identity, role purpose, artifact wall, model/tool config, MCP allowlist, single-item display nickname candidates, and always-on prohibitions | per-request user intent, workflow lane decisions, source selection, or temporary task-specific context |
| Role-owned skills | capability procedure, artifact expectations, quality checks, and local output rules | role eligibility, work for other roles, self-approval, execution outside MCP |
| Main-to-subagent briefs | request-specific assignment envelope: verbatim user request, explicit constraints, workflow consent posture, research artifact language, lane, artifact path, `context_summary`, data-cutoff needs, request-specific out-of-scope items, and return contract | standing role manuals, model/tool config, MCP allowlists, long method checklists, long source-class lists, full artifacts, or repeated guardrail prose |

Repo skill bodies are dependency-light capability references. They should not
declare role ownership, encode role-specific eligibility, or maintain direct
inter-skill call chains. Role-to-skill assignment belongs to `ROLE_SKILL_MAP`,
subagent TOML `skills.config`, CLI/Admin assignment state, and durable
instructions. Role labels, display groups, handoff contracts, forbidden action
summaries, permission profiles, and MCP allowlists are registry-owned service
metadata projected into generated indexes. A skill may mention a concrete
principal only when that principal is part of a policy or artifact contract,
such as `created_by` or `approved_by`.
validation.

Every repo `SKILL.md` should keep document metadata such as `name` and
`description` in frontmatter so Codex and the product web can separate metadata
from the markdown body. Every repo skill should also include
`agents/openai.yaml` metadata with a concise
display name, short description, default prompt that names its `$skill`, and an
explicit implicit-invocation policy. Metadata is UI-facing; it must not be the
only place where durable role or safety behavior lives.

The root/head-manager session can inspect and assign role-owned skills, but
must not use analyst, portfolio, risk, approval, or execution skills to fill in
role work directly.

User-visible skill lists are not the same as enabled or installed skills. The
main-agent user surface should show only direct user entrypoints by default:

- `tcx-workflow`
- `tcx-server`
- `tcx-build`
- `strategy-creator`
- `postmortem`

The root context does not load long workflow maps, scenario-quality gates,
or connector runbooks as always-visible prompt bulk. Those concerns move into
service-generated compact context, projected role indexes, and the short
`tcx-*` skills above. Compatibility skills may remain hidden for one release
cycle for older generated workspaces, but they should not be part of the
default user-facing or implicit skill surface.

## Additional Agent Instructions

Django web can store project-local additional instructions for any agent under
`.tradingcodex/agent-instructions/<role>.md`. Projection appends that text
after the generated default role instructions: `head-manager` receives the
block at the end of `.codex/prompts/base_instructions/head-manager.md`, and
fixed subagents receive it inside `.codex/agents/<role>.toml`
`developer_instructions`.

The web UI should explain the role-boundary, MCP permission, approval,
execution, and secret-access implications as guidance text only. It saves the
user's additional instruction text as-is and does not reject content based on
those warnings. Default generated instructions and service-layer execution
checks remain the authoritative safety boundary.

Head-manager skill responsibilities:

| Skill | Responsibility |
| --- | --- |
| `tcx-workflow` | compact workflow routing, selected-team dispatch/reuse, Artifact Supervisor Loop planning, handoff quality states, bounded follow-up/escalation proposals, and synthesis after accepted artifacts |
| `tcx-server` | startup health, local dashboard URL guidance, explicit user-requested dashboard opening, Codex restart guidance, TradingCodex MCP setup, update-status explanation, read-only broker/status inspection, and service troubleshooting without granting execution authority |
| `tcx-build` | full-access plus TCX-build-mode gated self-update, template/harness edits, broker/API provider connect/scaffold/register/validate flows, credential-ref handling, and live-submit blocking outside service gates |
| `external-data-source-gate` | read-only external evidence-source constraints and External MCP Gate honesty |
| `strategy-creator` | create, update, validate, and activate user-approved `strategy-*` skills as strategy library entries without granting policy, approval, execution, MCP, or role-boundary authority |
| `postmortem` | audit-backed process review and improvement proposals after failures, thesis changes, rejected orders, or executions |

## Broker Control Plane

TradingCodex is the local broker control plane, not a bundled collection of
broker SDK shortcuts. Codex may prepare a broker connector from natural
language, but the server owns connector state, provider capability profiles,
mapping review, account sync, order tickets, approvals, idempotency, broker
order status, reconciliation, and audit.

Broker-specific REST, SDK, MCP, CSV, or manual interfaces belong behind
provider adapters. The server calls only the small canonical broker contract for
health, account/cash/position reads, constraints, preview/validation, and
reviewed live submit/cancel/status methods.

`execution-operator` uses TradingCodex MCP canonical tools such as
`submit_approved_order`, `cancel_approved_order`, `refresh_broker_order_status`,
`get_order_ticket`, and `run_order_checks`. It never calls broker APIs, broker
SDKs, shell scripts, or broker-specific MCP tools directly. If a broker is MCP
backed, the reviewed broker MCP mapping stays behind the TradingCodex service
execution path.

## Strategy Skills

Output language precedence is:

```text
current user instruction -> selected strategy language -> product default
```

`strategy-creator` creates strategies as standalone Codex-compatible skills
whose names start with `strategy-`. Strategy skills live under
`.agents/skills/strategy-*/` with `SKILL.md` and `agents/openai.yaml`, and
active strategies are projected into the root
`.codex/config.toml` strategy marker so Codex can invoke them through `$` or
slash skill surfaces.

Strategy skill frontmatter must include `name`, `description`, `type:
strategy`, `status`, `language`, `owner: user`, and `last_reviewed`. The body must cover thesis, eligible universe, preferred
setups, entry criteria, exit criteria, evidence requirements, decision-ready
standard, sizing guidance, risk controls, block conditions, and change log.
Strategy bodies are standalone strategy procedures; they must not mention
TradingCodex role names, MCP, approval gates, approved action gates, or handoff
mechanics.

`strategy-*` skills guide judgment only. They never approve, execute, override
policy, change MCP allowlists, bypass information barriers, read secrets, or
grant broker authority. The root `head-manager` selects at most the relevant
strategy for a workflow and passes only compact selected strategy context to
subagents.

Strategy and policy behavior is fixed for the active workflow. Agents may
record a postmortem, optional-skill proposal, policy proposal, or strategy
change proposal after the workflow, but they must not silently self-update
strategy rules, policy, role authority, approval gates, approved action gates, MCP
allowlists, or broker permissions while producing financial judgment. When no
active user-approved strategy exists, the workflow uses explicit user
constraints and generated TradingCodex rules as temporary context only; it must
not pretend an ad hoc preference is a saved strategy.

Subagents receive only request-specific instructions and compact
`strategy_context` when relevant. Valuation, portfolio, and risk roles may
receive request-specific horizon, risk, sizing, and constraint context.
Execution receives no strategy judgment context.

## Skill Customization Flow

The built-in role skill map is the locked core baseline. Core skills cannot be
deleted, overwritten, or reassigned by user customization. User customization
starts with optional, role-local skills for fixed subagents.

Optional skill CRUD is managed by the shared application service used by the
`head-manager`, Django web, Django Ninja, and CLI. Generic `SKILL.md`
authoring should still follow `$skill-creator` discipline, then use the shared
service path for validation, status, and TOML projection.
Optional skill `name` and `description` are read from `SKILL.md` frontmatter;
the sidecar `agents/tradingcodex.json` stores lifecycle metadata such as role,
scope, status, and timestamps only.

Expected optional skill flow:

```text
user or web request -> shared service validation -> workspace file edit -> TOML projection -> Django/API/CLI status check
```

Codex-visible state is file-native: `.codex/agents/*.toml`,
`.agents/skills/*`, `.tradingcodex/subagents/skills/*`,
`.codex/config.toml`, role-projected skill source blocks, and
`.tradingcodex/generated/projection-manifest.json`. Django DB does not store
skill proposals, role-skill assignments, optional skill CRUD state, or skill
application audit state.

Optional skills may add work style, checklist, evidence-quality, or output-shape
procedures inside an existing subagent role boundary. They must not alter role
identity, model settings, MCP allowlists, permission profiles, information
barriers, approval authority, execution authority, secret access, live broker
posture, or core skill behavior.

## Subagent Isolation

- Subagent context is intentionally minimized.
- `head-manager` keeps full product and harness context.
- Fixed subagent TOML files supply the standing role-local card: affiliation, coordinator, assigned role, role purpose, own artifact paths, handoff target, and forbidden actions.
- Per-task subagent briefs are assignment envelopes, not role manuals. They should add only the current task, original request, explicit constraints, workflow consent posture, research artifact language, lane, expected artifact path, compact `context_summary`, request-specific stage boundaries, and concise return contract.
- Full artifacts, source dumps, strategy libraries, and repeated role manuals
  stay out of the brief unless a short excerpt is load-bearing.
- `.tradingcodex/mainagent/subagent-session-state.json` is a compact working
  summary with total counters and a recent-event window. Full subagent event
  history belongs in `trading/audit/subagent-session-events.jsonl`.
- `.tradingcodex/mainagent/workflow-loop-state.json` is the compact latest
  assisted-loop summary and pointer. The canonical loop state for a routed
  prompt lives under
  `.tradingcodex/mainagent/workflows/<workflow_run_id>/loop-state.json`, with
  the matching prompt gate beside it. It contains selected team, allowed
  follow-up team, escalation-only roles, pending tasks, planner decisions,
  blocked actions, and stop reason. It is inspectable state, not an automatic
  recursive dispatch mechanism.
- `.tradingcodex/mainagent/session-workflow-runs.json` maps Codex session or
  thread keys to workflow run ids. Subagent start/stop records use run id,
  role, and subagent session id so a reused role session can continue the right
  workflow without touching another active Codex app thread.
- `tcx subagents loop --artifact <path>` and the matching web/API preview read
  artifact handoff state and `follow_up_requests`, then return closed planner
  actions. Recording the result updates workspace loop state only; Codex still
  uses the explicit fixed-role spawn path for any real follow-up.
- Downstream roles start from artifact path plus `context_summary`; they open
  full markdown only for disputed, stale, missing, or load-bearing evidence.
- The head-manager should tell subagents to write reader-facing research artifacts in the user's language from the original request unless the user explicitly requests another artifact language. File paths, frontmatter keys, symbols, tickers, source names, and quoted source text stay in their natural/original form.
- When selecting an exact fixed role with Codex `spawn_agent`, do not combine `agent_type` with full-history forking. Use a compact assignment envelope on the first attempt and no model/reasoning overrides.
- Workflow consent stays separate from explicit user constraints. Consent to orchestrate or use subagents allows dispatch, but it is not itself an analytical constraint.
- Execution roles may additionally receive the approved action boundary because they need it to submit approved actions.
- MCP/tool isolation is configured per role in `.codex/agents/*.toml`.
- Generated fixed-role subagent TOML files pin `model = "gpt-5.5"` and `model_reasoning_effort = "high"`, and set `nickname_candidates` to the exact role `name` as a single-item list.
- Spawn by fixed role label so the role file supplies runtime defaults.
- If the active Codex schema cannot select the exact fixed role, role routing is `routing-unverified`.

The root `head-manager` MCP allowlist intentionally excludes
`submit_approved_order`, `cancel_approved_order`, and approval creation, but
includes External MCP Gate lifecycle tools for registration, check, discovery,
and read-only review.
`risk-manager` owns approval receipt creation; `execution-operator` owns
experimental submit/cancel execution tools.
`portfolio-manager` is the only role that creates draft order tickets.
`instrument-analyst` may read broker instrument constraints for instrument
support analysis, but this read-only access does not grant order drafting,
approval, or execution authority.

Generated Codex permission profiles allow public web, filing, disclosure, news,
and market-data network access for evidence gathering. Direct broker APIs,
broker-specific Codex MCP servers, raw secrets, approval bypasses, and execution
remain blocked by role instructions, projected role skill-source boundaries,
TradingCodex MCP allowlists, and service-layer policy.

## Hooks Are Guidance

- `UserPromptSubmit` handles prompt classification, secret warnings, direct-answer prevention context, and duplicate marker management.
- `SessionStart` writes compact TradingCodex mode, permission, update, server/MCP, and routing diagnostics for `head-manager`; incompatible service details such as version mismatch, DB mismatch, and occupied ports must survive into compact context so `head-manager` can mention the startup notice before claiming dashboard readiness. Startup recovery and dashboard URL guidance stay in `$tcx-server`, while self-update and connector implementation stay in `$tcx-build`.
- Official `UserPromptSubmit` matchers are ignored, so classification happens inside the hook script.
- Hooks use command type only and do not rely on ordering or concurrency between hooks.
- Project-local hooks load only in trusted projects and may be disabled when `features.hooks=false`.
- Hooks are not enforcement. TradingCodex MCP, permissions, and policy validation block actual order/approval/execution actions.

## Investment Workflow Map

TradingCodex does not collapse investment work into generic "stock analysis."
`head-manager` first classifies universe and workflow type, then uses quality
gates to determine lane, role team, artifacts, and blocked actions.

| Workflow type | Typical outputs | Core quality point |
| --- | --- | --- |
| Issuer baseline / tearsheet | factual issuer profile, evidence pack | no recommendation without thesis/valuation/risk handoff |
| Idea triage / watchlist | candidate funnel, research priority, next workflow | research priority is not an investment recommendation |
| Earnings preview / deep dive | expectation bar, thesis change, source posture | freeze time and distinguish reported facts, consensus, assumptions, and PM judgment |
| Catalyst calendar / thesis tracker | dated calendar, monitoring rules, append-only update log | confirmed dates, inferred windows, and action thresholds stay distinct |
| Valuation / model / scenario | valuation report, workbook, sensitivity map | current-price implication and source-backed assumptions are explicit |
| Position sizing / hedge | risk decision report, binding constraint, retained exposure | missing price/liquidity/borrow/options inputs block implementation-ready language |
| Model audit / normalization / QC | audit issue log, normalization pack, circulation memo | support findings affect readiness but do not create a conclusion by themselves |

Decision Quality Spine applies across these workflow types inside the selected
lane. Role artifacts should expose evidence grade, source freshness, source
quality, conflicts, decision readiness, confidence, missing evidence, scenario
cases where relevant, forecast permission fields when prediction or decision
support is in scope, anti-overfit checks for backtests/signals, and conservative
`accepted`, `revise`, `blocked`, or `waiting` handoff states.

Shared role-skill bundles for this spine are `forecasting-discipline`,
`thesis-scenario-tree`, `numeric-data-qc`, and `anti-overfit-validation`.
They are quality procedures only; role eligibility, MCP allowlists, approval,
execution, broker, and secret boundaries remain registry, TOML, policy, and
service-layer concerns.

## Module Graph

The initial default module graph is the baseline harness installed by the
product, not a user-selected operating phase.

| Module | Role |
| --- | --- |
| `codex-base` | base Codex config, head-manager constitution, hooks, rules, workspace scripts |
| `fixed-subagents` | fixed role subagent roster |
| `repo-skills` | repeatable investment workflow skills |
| `guidance-guardrails` | instruction, workflow, hook, and checklist-based guidance |
| `enforcement-guardrails` | schemas, policy, deterministic validation input |
| `information-barriers` | file boundary, policy wall, secret wall, trading folders |
| `audit` | audit directory and append-only event convention |
| `tradingcodex-mcp` | MCP enforcement boundary and approved-action gateway |
| `stub-execution` | fake execution for policy/MCP wiring tests |
| `paper-trading` | simulated portfolio execution without live brokers |
| `postmortem` | audit-backed investment process review |
