# Product Direction

This document owns TradingCodex product direction, product language, target
runtime posture, goals, non-goals, and the current scope boundary.

## Product Thesis

TradingCodex exists because Codex-assisted investment work needs more than a
chat transcript. Research, role handoffs, approvals, policy checks, execution
attempts, portfolio state, and audit records need a durable local system that
agents can call without turning a natural-language answer into a broker action.

The product is therefore a local-first trading harness:

- Codex is the work surface and orchestration client.
- Django is the durable service plane.
- The central local DB is the ledger.
- MCP is the agent/tool boundary.
- Generated workspaces are readable, repeatable Codex clients.

The harness is the top-level product model. Guardrails and Improvement are
child systems under the harness, not peer products. Guardrails reduce, isolate,
or block risk; Improvement raises workflow quality through memory, skills,
postmortems, and validation feedback.

## Target User Posture

TradingCodex is built for users who want a rigorous local investment workflow,
not a black-box trading bot.

| User posture | Product response |
| --- | --- |
| Individual investor using Codex for research | Provide structured workflows, role prompts, source posture, plain-language workflow summaries, and readable artifacts. |
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
| Frontend | Use Django templates, local static HTMX, and small plain JavaScript; no Node, bundler, React, or frontend build step in the baseline. |
| MCP | Provide project-scoped stdio bridge support for Codex environments. |
| Deployment | Local-first. PyPI installs the local package; initial scope does not provide a hosted service. |

## Goals In Detail

| Goal | Detail |
| --- | --- |
| Codex-native workflow | Preserve Codex project conventions, role files, hooks, skills, and generated prompts so the user works in familiar Codex surfaces. |
| Durable service plane | Put durable behavior behind Django services so Web, Admin, API, MCP, and CLI do not fork policy or execution logic. |
| Runtime ledger | Treat portfolio state, order lifecycle, non-research MCP ledger rows, and audit events as central DB records. Treat agent, skill, research handoff, and source-snapshot state as workspace files. |
| Agents-first web | Show head-manager, subagents, required/optional/strategy skills, skill markdown, and workspace research markdown at `/`; keep operational diagnostics out of primary navigation. |
| Deterministic executable boundary | Make executable action outcomes reproducible by checking requester identity, permission, policy fit, payload shape, exact approval, duplicate-request state, connection, and audit trail. |
| Strong role model | Keep one `head-manager`, ten fixed subagents, and role-owned skills as a durable coordination model, including an independent judgment-review gate. |
| Multi-universe extensibility | Let public equity be deepest first while preserving paths for ETF/index, crypto, macro/rates/FX/commodities, options, credit-signal, and cross-asset workflows. |
| Intuition-led investing with gates | Let users begin from rough market intuition, then translate that intuition into workflow lane, role team, evidence needs, investor-profile questions, blocked actions, and next allowed actions. |
| Compounding workflow memory | Each completed workflow should leave reusable context, source posture, missing-evidence notes, tests, or improvement proposals so the next workflow is easier to run and review. |
| Local operator control | Make Django Admin and Ninja useful for local/staff inspection, validation, and operation without becoming a bypass. |

## Non-Goals In Detail

| Non-goal | Reason |
| --- | --- |
| Built-in named live broker execution | Core ships paper only; broker-specific live support is request-built provider work behind the TradingCodex broker control plane and must pass provider review, policy, approval, duplicate-request, connection, sync, and audit gates. |
| Raw credential storage | Secrets do not belong in generated workspaces, prompt output, API responses, MCP responses, logs, or audit output. |
| REST execution bypass | REST endpoints may validate or call service-layer use cases, but cannot bypass MCP/service execution rules. |
| Product web orchestration | The web dashboard reviews state and previews workflow handoffs; it does not spawn subagents or perform investment analysis. |
| SDK-backed orchestration by default | Django should not become the agent runtime in v1. Future SDK modes require explicit feature flags and docs. |
| Workspace-local investment ledgers | Generated workspaces own Codex-readable agent, skill, and research handoff files, but canonical execution-sensitive investment state belongs to the central local DB. |
| Workspace-as-account UX | Workspaces are Codex workbenches. Portfolio/profile scope owns paper account and strategy separation. |
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

- Central local SQLite database at `~/.tradingcodex/state/tradingcodex.sqlite3`.
- `TRADINGCODEX_WORKSPACE_ROOT` selects the Codex workbench for file-native agent, skill, and research state.
- Research markdown under `trading/research` and `trading/reports` is workspace-native; source snapshots are workspace JSON files under `trading/research/source-snapshots/`.
- Staff/local-only Admin and OpenAPI docs.
- Live broker adapters disabled and unimplemented.
- Paper and validation-only execution paths only.
- Django Ninja for REST/control APIs.
- Custom Django/ASGI endpoint for MCP, backed by a typed tool registry and DB-visible tool ledger.
- Python workspace generator and Python generated hooks.
- Documentation in `docs/` remains the source of truth for product direction, safety rules, role boundaries, and execution policy.

## Licensing Posture

TradingCodex uses an Apache-2.0 open-core strategy. The repository open core is
permissively licensed, while TradingCodex marks, official hosted services,
verified adapters, enterprise policy/compliance packs, support, and managed
deployments may be governed by separate commercial terms.

Generated workspace scaffold files remain under the repository license. User
research, portfolio data, order artifacts, configuration secrets, and other
user-provided content remain owned by the user unless separately licensed or
contributed. See
[licensing-and-commercialization.md](./licensing-and-commercialization.md).
