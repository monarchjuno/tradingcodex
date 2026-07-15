# Product Direction

This document owns TradingCodex product direction, product language, target
runtime posture, goals, non-goals, and the current scope boundary.

## Product Thesis

TradingCodex exists because Codex-assisted investment work needs more than a
chat transcript. Research, role handoffs, approvals, policy checks, execution
attempts, portfolio state, and audit records need a durable local system that
agents can call without turning a natural-language answer into a broker action.

The product is therefore a local-first investment operating system built on
Codex:

- Codex is the work surface and orchestration client.
- The TradingCodex harness is the orchestration and runtime subsystem.
- Django is the durable service plane.
- The central local DB is the ledger.
- MCP is the agent/tool boundary.
- Generated workspaces are readable, repeatable Codex clients.

The investment OS is the top-level product model. Its core kernel owns the
durable quality, role, evidence, forecasting, policy, and execution contracts.
The harness coordinates those contracts across Codex roles, generated files,
service state, MCP tools, artifacts, and feedback loops. Guardrails and
Improvement remain cross-cutting harness systems: Guardrails reduce, isolate,
or block risk; Improvement raises workflow quality through memory, skills,
postmortems, and validation feedback.

## Product Layers

| Layer | Purpose | Customization rule |
| --- | --- | --- |
| Core kernel | Preserve user scope, role boundaries, evidence and point-in-time discipline, source and claim provenance, uncertainty, method-fit requirements, forecast lifecycle/scoring, artifact gates, policy, approval, audit, and execution safety. | Always applies. A skill, strategy, model, prompt, connector, or user overlay cannot weaken or replace it. |
| Bundled investment capability pack | Ship the default fundamental, technical, news, macro, instrument, valuation, portfolio, risk, judgment-review, forecasting, and anti-overfit procedures needed for useful investment work in a clean workspace. | Ships enabled as the pristine baseline and evolves through reviewed product changes and evaluation. |
| Managed user overlays | Add user-approved role-local methods, sector or universe procedures, evidence preferences, output conventions, workspace investor context, additional instructions, and `strategy-*` judgment rules. | Additive only. Overlays may specialize the work inside an existing role but do not redefine core semantics or authority. |

Host-global and plugin-provided skills are part of the surrounding Codex
runtime, not part of the TradingCodex baseline. Codex can expose their metadata
and may select them implicitly. TradingCodex therefore treats them as inactive
for investment methodology until the user explicitly opts in for the current
workflow or activates the skill through a TradingCodex-managed customization
path. Generated
workspace projections enumerate managed workspace skills, but the product does
not claim that host-global skills are hidden or technically impossible to
invoke until the active Codex runtime passes an explicit collision/isolation
attestation.

## Pristine Investment Quality Contract

A newly attached workspace must be capable of decision-useful investment
research, analysis, and forecast production without requiring a user-authored
skill or saved strategy. For supported scope, the pristine baseline should:

- collect and preserve source-backed, time-bounded evidence and distinguish
  facts, source claims, inference, and assumptions;
- explain business, market, event, instrument, valuation, portfolio, and risk
  drivers causally rather than relying on generic ratio lists or prose fluency;
- select a method profile that fits the question and instrument instead of
  forcing every workflow through a signal backtest or one valuation method;
- expose contrary evidence, missing support, sensitivities, uncertainty,
  update triggers, invalidation conditions, and conservative readiness;
- issue forecasts only with a target, horizon, point-in-time base rate,
  evidence, assumptions, probability or range, key variables, invalidation
  conditions, resolution rule, and independent resolution/scoring path;
- reject or downgrade stale, unsupported, leakage-prone, overfit, or
  non-reproducible work instead of inventing completeness; and
- remain useful when no customization is installed, while allowing managed
  overlays to add specialization without regressing the core quality floor.

"Calibrated" is an evaluated property, not a prompt adjective. A fresh user
workspace can issue scoreable forecasts and must report insufficient personal
sample until enough outcomes resolve. Product-level quality and calibration
claims require populated frozen evaluation profiles, measured runs, proper
scores versus recorded base rates, and the documented independent review gate.
Creating a profile, changing a model selector, or attaching a workspace does
not by itself establish quality superiority.

## Target User Posture

TradingCodex is built for users who want a rigorous local investment workflow,
not a black-box trading bot.

| User posture | Product response |
| --- | --- |
| Individual investor using Codex for research | Provide native Codex workflows plus a read-only multi-workspace viewer for source posture, summaries, and artifacts. |
| Operator validating non-live submissions | Provide deterministic policy, approval, connection, duplicate-request, and audit checks. |
| Developer extending adapters or universe routing | Provide modular Django apps for runtime state, service-layer contracts, MCP registry metadata, and template-driven workspace generation. |
| Research-heavy user with multiple Codex projects | Keep research handoffs workspace-local and Codex-readable while preserving central runtime provenance and profile-scoped paper portfolios. |
| Compliance-minded operator | Make approvals, restricted lists, capability checks, and audit events inspectable through Admin, API, MCP ledger, and exports. |

## Product Language

TradingCodex durable product language is English. This includes:

- durable docs in `docs/`
- generated workspace guidance
- `AGENTS.md` content generated from templates
- role prompts and skill descriptions
- Admin labels and help text
- CLI command help
- user-facing product web copy

Internal classifiers may accept broader user input when useful, but durable
guidance emitted by the product should remain English.

## Target Runtime

| Runtime area | Direction |
| --- | --- |
| Python | Use the current supported Python line defined in `pyproject.toml` and release docs. |
| Django | Use the latest LTS-oriented Django service plane, currently Django 5.2.x. |
| Database | Default to central local SQLite; design models so PostgreSQL remains viable later. |
| Frontend | Use React 19, TypeScript, and Vite 8 under `frontend/`; commit the deterministic build under Django static files and serve it through Django and WhiteNoise. Node 22 is a maintainer build dependency only, never an installed-runtime or generated-workspace requirement. |
| MCP | Provide project-scoped stdio bridge support for Codex environments. |
| Deployment | Local-first. PyPI installs the local package; initial scope does not provide a hosted service. |

## Goals In Detail

| Goal | Detail |
| --- | --- |
| Pristine investment competence | Make the bundled baseline decision-useful for supported research, analysis, and scoreable forecasting before any user skill or strategy is added, and evaluate that baseline separately from customization. |
| Managed customization | Let users add methods, domain knowledge, preferences, and strategies as explicit overlays without replacing core quality, safety, role, evidence, or execution contracts. |
| Codex-native workflow | Preserve Codex project conventions, role files, hooks, skills, and generated prompts so the user works in familiar Codex surfaces. |
| Durable service plane | Put durable behavior behind Django services so Web, Admin, API, MCP, and CLI do not fork policy or execution logic. |
| Runtime ledger | Treat portfolio state, order lifecycle, non-research MCP ledger rows, and audit events as central DB records. Treat agent, skill, research handoff, and source-snapshot state as workspace files. |
| Workspace viewer | Organize `/` into Library, Skills, and System with a registered-workspace selector. Keep it read-only; native Codex owns analysis, follow-up, and skill invocation. |
| Deterministic executable boundary | Admit final submit/cancel only through an exact complete immediate root-native action or an exact-first-line `$tcx-order-allow` current-turn grant with hook-injected proof. Both paths retain service checks for requester identity, permission, policy fit, payload shape, exact approval, duplicate-request state, connection, live confirmation, and audit trail. |
| Strong role model | Keep one `head-manager`, nine fixed analytical and decision-support subagents, and role-owned skills as a durable coordination model, including an independent judgment-review gate. Final execution is service-owned and runs no model. |
| Multi-universe extensibility | Let public equity be deepest first while preserving paths for ETF/index, crypto, macro/rates/FX/commodities, options, credit-signal, and cross-asset workflows. |
| Intuition-led investing with gates | Let users begin from rough market intuition, then let Head Manager form working questions, identify evidence and Investor Context needs, preserve blocked actions, and dynamically choose or revise the smallest useful exact-role team. |
| Compounding workflow memory | Each completed workflow should leave a source-bound decision episode, forecast or outcome posture, missing-evidence notes, and reviewed lesson candidates so the next workflow is easier to run without turning one outcome into a rule. |
| Local operator control | Make Django Admin and Ninja useful for local/staff inspection, validation, and operation without becoming a bypass. |

## Non-Goals In Detail

| Non-goal | Reason |
| --- | --- |
| Built-in named live broker execution | Core ships paper only; broker-specific live support is request-built provider work behind the TradingCodex broker control plane and must pass provider review, policy, approval, duplicate-request, connection, sync, and audit gates. |
| Raw credential storage | Secrets do not belong in generated workspaces, prompt output, API responses, MCP responses, logs, or audit output. |
| REST or raw/direct MCP execution mutation | Public REST and generic CLI surfaces do not submit or cancel orders, and direct MCP callers cannot mint current-turn proof. Root Head Manager sees only `use_order_turn_grant`, which is inert without proof injected for an exact `$tcx-order-allow` turn. Immediate and protected-turn entries converge on the same service-owned policy, approval, idempotency, adapter, and audit kernel. |
| A second scheduler or web runner | Recurring and interactive TradingCodex work uses native Codex tasks. Django exposes a read-only workspace viewer and does not launch, resume, schedule, or supervise Codex processes. |
| SDK-backed orchestration by default | Django should not become the agent runtime in v1. Future SDK modes require explicit feature flags and docs. |
| Workspace-local investment ledgers | Generated workspaces own Codex-readable agent, skill, and research handoff files, but canonical execution-sensitive investment state belongs to the central local DB. |
| Workspace-as-account UX | Attached workspaces are selectable inspection scopes, not selectable investor profiles. Internal portfolio/account/strategy scope still isolates paper state, while suitability context is a separate optional workspace file. |
| Canonical editable LLM Wiki | A Wiki may be generated for navigation, but immutable and append-only decision, source, forecast, outcome, and review records remain canonical. |
| Automatic strategy or prompt learning | Historical or forward evidence may propose a reviewed change; it cannot silently rewrite a strategy, skill, prompt, policy, or execution boundary. |
| Public-equity-only product | Public equity is the first deep sleeve, not the long-term product boundary. |
| Hidden safety policy | Durable rules must not live only in code, prompts, templates, tests, or hooks. |

## Investment Universe Scope

| Universe | Initial treatment |
| --- | --- |
| Public equity | Full research, valuation, thesis, earnings, catalyst, sizing, risk, and non-live order-validation path. |
| ETF/index | Instrument support, constituent diligence, benchmark-relative research, and policy-gated non-live submission path when supported. |
| Public crypto | Read-only market structure and risk support; no unsupported execution claims. |
| Macro/rates/FX/commodities | Macro transmission, liquidity, policy, and cross-asset risk inputs; execution blocked unless explicitly supported later. |
| Options | Payoff/risk support and hedge context; no execution unless a future adapter and policy path explicitly support it. |
| Credit signals | Equity-risk context and warning signals; credit-instrument decisions route to a future credit workflow. |

Unsupported or weakly sourced universes receive conservative readiness labels
such as `research-only`, `screen-grade`, `not-decision-ready`, or `blocked`.

## Current Defaults

- Central local SQLite database at `state/tradingcodex.sqlite3` under the canonical macOS, Windows, or Linux application-data home.
- `TRADINGCODEX_WORKSPACE_ROOT` selects the Codex workspace for file-native agent, skill, and research state.
- Research markdown under `trading/research` and `trading/reports` is workspace-native; source snapshots are workspace JSON files under `trading/research/source-snapshots/`.
- Staff/local-only Admin and OpenAPI docs.
- Live broker adapters disabled and unimplemented.
- Paper and validation-only execution paths only.
- Django Ninja for REST/control APIs.
- React 19, TypeScript, and Vite 8 source under `frontend/`, with committed
  viewer assets served by Django and WhiteNoise and no Node production
  runtime.
- Read-only Library, Skills, and System sections with registered-workspace
  selection. Analysis, follow-up, and skill invocation remain native Codex
  operations.
- Custom Django/ASGI endpoint for MCP, backed by a typed tool registry and DB-visible tool ledger.
- Python workspace generator and Python generated hooks.
- Documentation in `docs/` remains the source of truth for product direction, safety rules, role boundaries, and execution policy.

## Licensing Posture

TradingCodex uses an Apache-2.0 open-core strategy. The repository open core is
permissively licensed, while TradingCodex marks, official hosted services,
verified adapters, enterprise policy/compliance packs, support, and managed
deployments may be governed by separate commercial terms.

Generated workspace scaffold files remain under the repository license. User
research, portfolio data, order records, configuration secrets, and other
user-provided content remain owned by the user unless separately licensed or
contributed. See
[licensing-and-commercialization.md](./licensing-and-commercialization.md).
