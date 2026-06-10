# Roles, Skills, And Workflows

This document owns the fixed role roster, head-manager dispatch gate, skills,
workflow routing, subagent isolation, role-owned artifacts, and module graph.
Roles and workflows participate in both Harness child systems: Guardrails
through role boundaries and information barriers, and Improvement through
workflow quality, skill proposals, and postmortems.

## Fixed Role Roster

TradingCodex always uses `head-manager` as the main agent with nine fixed
subagents.

| Role | Responsibility | Never allowed |
| --- | --- | --- |
| `head-manager` | Workflow dispatch, subagent coordination, synthesis, validation/audit status tracking | Finalize investment conclusions without subagent output, call broker APIs directly |
| `fundamental-analyst` | Business, financial statement, official disclosure, and competitive analysis | order intent, approval, execution, secret read |
| `technical-analyst` | Price action, trend, momentum, volume, volatility | order intent, execution, standalone investment conclusion |
| `news-analyst` | News, disclosure events, macro events, narrative change | assert unverified rumors, execution |
| `macro-analyst` | Macro, rates, FX, commodities, liquidity, policy, cross-asset transmission | order intent, execution, unsupported implementation claims |
| `instrument-analyst` | ETF/index, options/derivatives, crypto public market structure, credit-signal boundary, instrument mechanics | order intent, execution, unsupported instrument execution claims |
| `valuation-analyst` | DCF, reverse DCF, multiples, scenario, expected return | approval, execution, broker API call |
| `portfolio-manager` | Portfolio fit, sizing, draft order intent | self-approval, execution, arbitrary policy changes |
| `risk-manager` | Risk review, policy review, approval readiness, approval receipt | order drafting, execution, arbitrary policy changes |
| `execution-operator` | Submit approved order intents through TradingCodex MCP | raw broker API, secret read, policy change |

## Head-Manager Dispatch Gate

In investment workflows, `head-manager` is a dispatcher, coordinator, and
synthesizer, not the analyst. Security analysis, investment judgment,
valuation, technical analysis, news analysis, portfolio/risk review, order
drafting, approval, and execution requests must pass through the subagent
dispatch gate.

Codex currently spawns subagents only when the user explicitly asks for
subagent, parallel agent, or delegated agent work. TradingCodex also treats
explicit `$orchestrate-workflow` usage as workflow consent.

| Trigger | Handling |
| --- | --- |
| General investment request, such as "Analyze Apple stock" | `UserPromptSubmit` injects `confirmation_required`; `head-manager` asks for `$orchestrate-workflow` confirmation or provides a starter prompt instead of doing analysis directly. |
| Explicit `$orchestrate-workflow` request | The representative workflow skill becomes the primary orchestrator and dispatches selected subagents. |
| Explicit subagent/parallel/delegated request | `UserPromptSubmit` injects `dispatch_allowed`; the skill checks existing subagent state before creating/reusing sessions. |
| Same run/role subagent is active | Wait or follow up instead of creating duplicates. |
| Same role artifact has passed quality gates | Reuse the artifact instead of duplicating work. |
| Codex `spawn_agent` schema cannot select exact fixed role | Treat role routing as `routing-unverified`; provide `waiting_for_subagent_dispatch` and task briefs only. |

Fail closed: if subagent dispatch is unavailable, the workflow waits.
`head-manager` must not fill the gap with direct analysis.

## Head-Manager Operating Style

For repository, CLI, Django, MCP, template, docs, test, and harness maintenance
work, `head-manager` follows the default Codex coding-agent style: concise
preambles before grouped tool work, plans only for meaningful multi-step tasks,
`rg`-first search, `apply_patch` for manual edits, focused validation before
broader checks, respect for dirty worktrees, and concise final handoffs.

This operating style is a working discipline, not an investment permission.
It does not weaken the dispatch gate, role-owned skill boundary, MCP execution
boundary, approval requirements, or information barriers.

## Allowed And Forbidden Head-Manager Responses

| Situation | Allowed response | Forbidden response |
| --- | --- | --- |
| Broad analysis such as "Analyze Apple stock" | research-only lane, selected team, artifact paths, subagent workflow confirmation, or starter prompt | Direct business/price/news/recommendation analysis |
| Explicit workflow request such as "$orchestrate-workflow analyze Apple" | Spawn selected team or reuse active/completed roles, wait for outputs, then synthesize | Analyze without dispatch |
| Decision support such as "Should I buy?" | Dispatch analyst/valuation/portfolio/risk team and explain required artifacts/gates | Offer buy/sell opinion without subagent output |
| Dispatch unavailable, role routing unverified, or dispatch failed | Provide `waiting_for_subagent_dispatch` state and task briefs only | Switch to "I will analyze it myself" |
| Subagent artifacts exist | Summarize role outputs, conflicts, confidence/missing evidence, and next allowed action | Override subagent evidence with unsupported certainty |

## Skills And Context

Repo-local skills live under `.agents/skills/*` so they are discoverable at the
workspace level. TradingCodex treats role-owned skills as an ownership contract
and config boundary.

Instruction/skill separation:

| Surface | Owns | Must not own |
| --- | --- | --- |
| `head-manager` base instructions | durable identity, safety invariants, dispatch fail-closed rule, role boundaries, MCP execution boundary, skill routing | workflow templates, scenario tables, long checklists, subagent message bodies |
| Head-manager skills | repeatable workflow procedures, universe maps, scenario gates, subagent briefing/reuse mechanics, synthesis, profile interview, postmortem workflow | weakening base guardrails, bypassing role-owned skills, approving or executing directly |
| Fixed subagent TOML | standing role identity, role purpose, artifact wall, model/tool config, MCP allowlist, and always-on prohibitions | per-request user intent, workflow lane decisions, source selection, or temporary task-specific context |
| Role-owned skills | specialist role methods and artifact expectations | work for other roles, self-approval, execution outside MCP |
| Main-to-subagent briefs | request-specific assignment envelope: verbatim user request, explicit constraints, workflow consent posture, lane, artifact path, material context, data-cutoff needs, request-specific out-of-scope items, and return contract | standing role manuals, model/tool config, MCP allowlists, long method checklists, long source-class lists, or repeated guardrail prose |

The root/head-manager session can inspect and assign role-owned skills, but
must not use analyst, portfolio, risk, approval, or execution skills to fill in
role work directly.

User-visible skill lists are not the same as enabled or installed skills. The
main-agent user surface should show only direct user entrypoints by default:

- `orchestrate-workflow`
- `head-manager-interview`
- `postmortem`

Internal head-manager harness skills such as `investment-workflow-map`,
`scenario-quality-gates`, `manage-subagents`, and `synthesize-decision` remain
enabled for `head-manager`; they are hidden from the default user-facing list,
not disabled.

Head-manager skill responsibilities:

| Skill | Responsibility |
| --- | --- |
| `orchestrate-workflow` | workflow sequencing, lane escalation, stage gates, and movement across research, thesis, portfolio, risk, order, approval, execution, and postmortem |
| `investment-workflow-map` | universe/workflow classification, source/as-of posture, support gaps, hero/support artifacts, and readiness labels |
| `scenario-quality-gates` | scenario selection, minimum useful role team, artifact expectations, blocked actions, and quality gates |
| `external-data-source-gate` | read-only external evidence-source constraints and connector honesty |
| `manage-subagents` | fixed-role dispatch mechanics, runtime state/reuse checks, compact briefs, artifact review, and conflict handling |
| `synthesize-decision` | final user-facing decision state after subagent artifacts or outputs exist |
| `head-manager-interview` | durable investor/operator profile, suitability context, constraints, and tone calibration |
| `postmortem` | audit-backed process review and improvement proposals after failures, thesis changes, rejected orders, or executions |

## Skill Proposal Flow

The built-in role skill map is a bootstrap baseline. Role skill changes move
through skill proposals so they can be inspected, approved, applied, and
audited.

Expected flow:

```text
proposal -> validation -> approval -> apply -> audit
```

CLI and Admin should both call shared service-layer helpers for proposal
operations.

## Subagent Isolation

- Subagent context is intentionally minimized.
- `head-manager` keeps full product and harness context.
- Fixed subagent TOML files supply the standing role-local card: affiliation, coordinator, assigned role, role purpose, own artifact paths, handoff target, and forbidden actions.
- Per-task subagent briefs are assignment envelopes, not role manuals. They should add only the current task, original request, explicit constraints, workflow consent posture, lane, expected artifact path, material context, request-specific stage boundaries, and concise return contract.
- Workflow consent stays separate from explicit user constraints. Consent to orchestrate or use subagents allows dispatch, but it is not itself an analytical constraint.
- Execution roles may additionally receive the workspace MCP boundary because they need it to submit approved actions.
- MCP/tool isolation is configured per role in `.codex/agents/*.toml`.
- Generated fixed-role subagent TOML files pin `model = "gpt-5.5"` and `model_reasoning_effort = "high"`.
- Spawn by fixed role label so the role file supplies runtime defaults.
- If the active Codex schema cannot select the exact fixed role, role routing is `routing-unverified`.

The root `head-manager` MCP allowlist intentionally excludes
`submit_approved_order`, `cancel_approved_order`, and approval creation.
`risk-manager` owns approval receipt creation; `execution-operator` owns
experimental submit/cancel execution tools.

## Hooks Are Guidance

- `UserPromptSubmit` handles prompt classification, secret warnings, direct-answer prevention context, and duplicate marker management.
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
