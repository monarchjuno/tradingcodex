# TradingCodex Docs

This directory is the durable source of truth for TradingCodex product direction,
operating rules, safety boundaries, generated workspace behavior, and release
policy. TradingCodex is a Python/Django-native, local-first trading harness.
Codex projects are clients and provenance sources; the central Django service
and local DB are the investment ledger.

The docs are intentionally split by decision area. Start with the two gateway
documents, then follow the topic-specific files for implementation-level rules.

## Reading Order

| Start here | Purpose |
| --- | --- |
| [tradingcodex-prd.md](./tradingcodex-prd.md) | Top-level product contract: what TradingCodex is, what it is not, and which detailed docs own each decision area. |
| [core-concepts-and-rules.md](./core-concepts-and-rules.md) | Fast operating reference for planes, guardrails, role boundaries, execution lifecycle, and artifact posture. |

## Detailed Source Documents

| Document | Owns | Update when |
| --- | --- | --- |
| [harness.md](./harness.md) | Top-level harness model and how Guardrails and Improvement fit under it | The top-level product model, harness responsibilities, or cross-cutting taxonomy changes |
| [guardrails.md](./guardrails.md) | Guardrails taxonomy: guidance, enforcement, and information barriers | Guardrail classes, enforcement boundaries, information barriers, or safety taxonomy changes |
| [improvement-loop.md](./improvement-loop.md) | Improvement taxonomy: workflow quality, research memory, skill proposals, postmortems, validation feedback | Quality gates, learning loops, postmortems, skill proposals, or validation feedback behavior changes |
| [product-direction.md](./product-direction.md) | Product definition, target users, goals, non-goals, scope posture, current defaults | Product direction, release scope, default runtime, product language, or live-execution posture changes |
| [system-architecture.md](./system-architecture.md) | Django modular monolith, central DB, app boundaries, service layer, core models, runtime planes | Django apps, DB ownership, service-layer use cases, model ownership, or runtime topology changes |
| [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) | Product web, Django Admin, Django Ninja API, MCP endpoint, CLI, generated `./tcx` wrapper | User/admin/API/MCP/CLI surface changes, route changes, or callable tool changes |
| [safety-policy-and-execution.md](./safety-policy-and-execution.md) | Guardrail taxonomy, policy checks, approvals, idempotency, execution lifecycle, blocked actions | Policy, permissions, approvals, adapters, execution, restricted list, secret handling, or risk gates change |
| [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) | Fixed role roster, head-manager dispatch gate, skills, workflow routing, subagent isolation | Role responsibilities, skill assignments, workflow routing, or subagent permission boundaries change |
| [research-memory-and-artifacts.md](./research-memory-and-artifacts.md) | DB-first research memory, source snapshots, freshness, artifact exports, readiness labels | Research models, artifact contracts, source/as-of rules, markdown export behavior, or report quality rules change |
| [generated-workspaces.md](./generated-workspaces.md) | `tcx attach`, `tcx init`, generated files, project-scoped MCP config, smoke checks, workspace provenance | Workspace templates, bootstrap behavior, generated files, hooks, MCP config, or `./tcx` wrapper behavior changes |
| [validation-and-test-plan.md](./validation-and-test-plan.md) | Required validation commands, unit/API/generator/smoke coverage, release-sensitive checks | Test expectations, smoke flows, command requirements, or regression coverage changes |
| [deployment.md](./deployment.md) | PyPI/TestPyPI release process, CI/CD workflow, Trusted Publishing, release smoke checks | Packaging metadata, release automation, versioning policy, or distribution boundary changes |
| [licensing-and-commercialization.md](./licensing-and-commercialization.md) | Open-core licensing, contribution, trademark, generated workspace ownership, commercialization boundary | Repository license, contribution model, trademark policy, generated workspace ownership, or monetization boundary changes |

## Source-Of-Truth Principles

| Principle | Meaning |
| --- | --- |
| Docs first | Product direction, safety rules, role responsibilities, and execution policy changes start by reading the relevant docs. |
| Update in the same change | Durable rule changes in code, templates, prompts, hooks, tests, or generated artifacts must update the relevant docs in the same change. |
| Gateway docs remain stable | `tradingcodex-prd.md` and `core-concepts-and-rules.md` stay as stable entrypoints even when details move into topic files. |
| Implementation verifies docs | Implementation does not replace the docs. If implementation reveals a durable rule, document it. |
| Product language is English | TradingCodex product copy, generated workspace guidance, Admin UI, CLI help, and durable docs are written in English. |
| Product web is review-first | The `/` web app visualizes harness state and prepares starter prompts, but does not spawn agents, approve orders, or execute orders. |
| Open-core boundary is explicit | Apache-2.0 covers the repository open core; trademarks and official commercial offerings remain separately governed. |

## Change Checklist

| Change type | Documents to check |
| --- | --- |
| Top-level harness model, guardrail/improvement taxonomy, or cross-cutting concept language | `harness.md`, `guardrails.md`, `improvement-loop.md`, `core-concepts-and-rules.md` |
| Product scope, non-goals, or default runtime | `tradingcodex-prd.md`, `product-direction.md`, `harness.md` |
| Django apps, models, service-layer contracts, or DB ownership | `system-architecture.md` |
| Product web, Admin, REST, MCP, CLI, or wrapper behavior | `interfaces-and-surfaces.md` |
| Guardrails, policy, permission, approval, execution, adapter, or secret boundary | `guardrails.md`, `safety-policy-and-execution.md`, `core-concepts-and-rules.md` |
| Subagent roster, role responsibilities, skills, workflow routing, or information barriers | `roles-skills-and-workflows.md`, `core-concepts-and-rules.md` |
| Research memory, artifacts, exports, source snapshots, report readiness, or postmortem learning | `improvement-loop.md`, `research-memory-and-artifacts.md` |
| Generated workspace structure, templates, hooks, bootstrap, or MCP config | `generated-workspaces.md` |
| Test coverage or validation commands | `validation-and-test-plan.md` |
| PyPI/TestPyPI release, CI/CD, or package distribution boundary | `deployment.md`, `product-direction.md` |
| License, contribution, trademark, or monetization boundary | `licensing-and-commercialization.md` |
