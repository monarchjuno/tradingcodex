# Workflows And Agents

Use this page before changing `head-manager`, fixed subagents, skills, hooks, routing, handoff quality, optional skills, strategies, or generated role instructions. Human-facing rules live in [docs/roles-skills-and-workflows.md](../docs/roles-skills-and-workflows.md), [docs/harness.md](../docs/harness.md), and [docs/artifact-supervisor-loop-prd.md](../docs/artifact-supervisor-loop-prd.md).

## Fixed Team

TradingCodex uses one root `head-manager` plus ten fixed subagents, including an independent `judgment-reviewer` gate.

| Role | Owns | Never allowed |
| --- | --- | --- |
| `head-manager` | intake, staged workflow plan, dispatch, artifact acceptance, synthesis, validation/audit status | final investment conclusion without accepted role artifacts, raw broker APIs |
| `fundamental-analyst` | business, statements, filings, economics, fundamental risks | orders, approval, execution, secrets |
| `technical-analyst` | price action, trend, momentum, volume, volatility, liquidity setup | orders, execution, standalone investment conclusion |
| `news-analyst` | verified news, disclosures, event chronology, narrative change | unverified rumor claims, execution |
| `macro-analyst` | macro, rates, FX, commodities, liquidity, policy transmission | orders, execution |
| `instrument-analyst` | ETF/index, options, crypto public market structure, instrument mechanics | unsupported execution claims |
| `valuation-analyst` | valuation ranges, scenarios, sensitivity, decision-quality gaps | approval, execution |
| `portfolio-manager` | portfolio fit, sizing, concentration, draft order-ticket readiness | self-approval, execution |
| `risk-manager` | downside, restricted list, policy readiness, approval readiness | order drafting, execution |
| `execution-operator` | approved submit/cancel/status through TradingCodex service boundary | raw broker APIs, secrets, policy change |

## Routing Contract

Natural-language investment requests activate workflow routing. `head-manager` should draft, validate, record, and dispatch from a staged plan before substantive investment analysis. If dispatch is unavailable or role routing is unverified, the workflow waits.

Negated scope is binding. `no order`, `no trading`, and `no valuation` remove those actions or roles from the plan. Broad public-equity prompts such as `Analyze NVDA` default to thesis review unless the user narrows scope first. Narrow fact-only and technical-only prompts stay on the selected producer roles without `judgment-reviewer` unless broader judgment is requested.

Execution-only approved-action lanes use ticket, approval, policy, duplicate-request, connection, and audit gates. They do not dispatch `judgment-reviewer` unless the prompt first routes through research or decision support.

Key files:

- `tradingcodex_service/application/workflow_planner.py`
- `workspace_templates/modules/codex-base/files/.codex/hooks/tradingcodex_hook.py`
- `workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/head-manager.md`
- `workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow/SKILL.md`

## Handoff Contract

Role artifacts should include artifact path, original request, binding constraints, source/as-of or retrieved-at posture, claim discipline, confidence, uncertainty, missing evidence, readiness label, next recipient, blocked actions, and handoff state.

Handoff states:

- `accepted`: can move downstream.
- `revise`: stayed in bounds but needs more work before downstream use.
- `blocked`: policy, boundary, stale data, unsupported instrument, or user scope blocks downstream action.
- `waiting`: required upstream role output does not exist yet.

Downstream roles consume accepted upstream artifacts. They do not repair missing upstream analysis outside their own question.

## Skill And Projection Boundaries

Head-manager and strategy skills live under `.agents/skills/*`. Role-owned subagent skills live under `.tradingcodex/subagents/skills/*`. Fixed subagent TOML projects only that role's allowed skill source list.
It does not include root or strategy skill files as disabled subagent entries.

`tradingcodex_service/application/agents.py` owns role metadata, built-in skills, permission profiles, MCP allowlists, forbidden skill tags, and projection behavior. Skill bodies should describe procedures, not grant durable role authority.

Shared subagent quality skills include `forecasting-discipline`,
`thesis-scenario-tree`, `numeric-data-qc`, and `anti-overfit-validation`.
`agent-judgment-review` is role-owned by `judgment-reviewer` so the challenge
gate is independent from producing analysts and downstream reviewers. These are
review procedures, not role authority.

Default user-visible root skills:

- `plan-workflow`
- `tcx-workflow`
- `automate-workflow`
- `tcx-server`
- `tcx-build`
- `strategy-creator`
- `postmortem`

## Edit Checklist

When changing this area, keep these aligned:

- human docs in `docs/harness.md`, `docs/roles-skills-and-workflows.md`, and related pages
- component registry in `tradingcodex_service/application/components.py`
- role/skill registry in `tradingcodex_service/application/agents.py`
- generated role TOML in `workspace_templates/modules/fixed-subagents/files/.codex/agents/`
- head-manager prompt and hooks in `workspace_templates/modules/codex-base/files/`
- repo skills in `workspace_templates/modules/repo-skills/files/`
- generated workspace and routing tests
