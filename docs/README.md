# TradingCodex Docs

This directory is the durable source of truth for TradingCodex product direction,
operating rules, safety boundaries, generated workspace behavior, and release
policy. TradingCodex is a Python/Django-native, local-first trading harness.
Codex projects are clients and provenance sources; the central Django service
and local DB are the investment ledger.

The docs are intentionally split by decision area. Start with the gateway
reference, then follow the topic-specific files for implementation-level rules.

## Reading Order

| Start here | Purpose |
| --- | --- |
| [core-concepts-and-rules.md](./core-concepts-and-rules.md) | Fast operating reference for planes, guardrails, role boundaries, execution lifecycle, and artifact posture. |

## Detailed Source Documents

| Document | Owns | Update when |
| --- | --- | --- |
| [harness.md](./harness.md) | Top-level harness model and how Guardrails and Improvement fit under it | The top-level product model, harness responsibilities, or cross-cutting taxonomy changes |
| [components.md](./components.md) | Component-first harness registry, component IDs, taxonomy tags, owned surfaces, validation | Harness component ownership, component registry behavior, or component/runtime exposure changes |
| [guardrails.md](./guardrails.md) | Guardrails taxonomy: guidance, enforcement, and information barriers | Guardrail classes, enforcement boundaries, information barriers, or safety taxonomy changes |
| [improvement-loop.md](./improvement-loop.md) | Improvement taxonomy: workflow quality, research memory, file-native skill proposals, postmortems, validation feedback | Quality gates, learning loops, postmortems, skill proposal/projection, or validation feedback behavior changes |
| [product-direction.md](./product-direction.md) | Product definition, target users, goals, non-goals, scope posture, current defaults | Product direction, release scope, default runtime, product language, or live-execution posture changes |
| [financial-workflow-references.md](./financial-workflow-references.md) | Research-backed finance workflow and non-expert UX principles for workflow handoffs and role outputs | User-facing workflow intake, suitability/profile context, plain-English output, or professional finance method framing changes |
| [artifact-supervisor-loop-prd.md](./artifact-supervisor-loop-prd.md) | PRD for bounded artifact-driven supervisor loops, follow-up routing, lane escalation, loop state, and Decision Quality Spine preservation | Investment-analysis loop behavior, follow-up routing, decision-support quality, forecasting, shared workflow skills, or artifact loop contracts change |
| [system-architecture.md](./system-architecture.md) | Django modular monolith, central DB, app boundaries, service layer, core models, runtime planes | Django apps, DB ownership, service-layer use cases, model ownership, or runtime topology changes |
| [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) | Product web, Django Admin, Django Ninja API, MCP endpoint, CLI, generated `./tcx` wrapper | User/admin/API/MCP/CLI surface changes, route changes, or callable tool changes |
| [safety-policy-and-execution.md](./safety-policy-and-execution.md) | Guardrail taxonomy, policy checks, approvals, idempotency, execution lifecycle, blocked actions | Policy, permissions, approvals, adapters, execution, restricted list, secret handling, or risk gates change |
| [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) | Fixed role roster, head-manager dispatch gate, skills, strategy skills, workflow routing, subagent isolation | Role responsibilities, skill assignments, strategy behavior, workflow routing, or subagent permission boundaries change |
| [research-memory-and-artifacts.md](./research-memory-and-artifacts.md) | File-native research memory, source snapshots, freshness, artifact paths, readiness labels | Research file contracts, source/as-of rules, markdown write/preview behavior, or report quality rules change |
| [generated-workspaces.md](./generated-workspaces.md) | `tcx attach`, `tcx init`, generated files, project-scoped MCP config, smoke checks, workspace provenance | Workspace templates, bootstrap behavior, generated files, hooks, MCP config, or `./tcx` wrapper behavior changes |
| [validation-and-test-plan.md](./validation-and-test-plan.md) | Required validation commands, unit/API/generator/smoke coverage, release-sensitive checks | Test expectations, smoke flows, command requirements, or regression coverage changes |
| [deployment.md](./deployment.md) | PyPI/TestPyPI release process, CI/CD workflow, Trusted Publishing, release smoke checks | Packaging metadata, release automation, versioning policy, or distribution boundary changes |
| [licensing-and-commercialization.md](./licensing-and-commercialization.md) | Open-core licensing, contribution, trademark, generated workspace ownership, commercialization boundary | Repository license, contribution model, trademark policy, generated workspace ownership, or monetization boundary changes |

## Source-Of-Truth Principles

| Principle | Meaning |
| --- | --- |
| Docs first | Product direction, safety rules, role responsibilities, and execution policy changes start by reading the relevant docs. |
| Update in the same change | Durable rule changes in code, templates, prompts, hooks, tests, or generated artifacts must update the relevant docs in the same change. |
| Gateway docs remain stable | `core-concepts-and-rules.md` stays as the stable entrypoint even when details move into topic files. |
| Implementation verifies docs | Implementation does not replace the docs. If implementation reveals a durable rule, document it. |
| Product language is English | TradingCodex product copy, generated workspace guidance, Admin UI, CLI help, and durable docs are written in English. |
| Product web is review-first | The `/` web app opens on the workflow planner and can preview handoffs, but does not spawn agents, approve orders, or execute orders. |
| Open-core boundary is explicit | Apache-2.0 covers the repository open core; trademarks and official commercial offerings remain separately governed. |

## Change Checklist

| Change type | Documents to check |
| --- | --- |
| Top-level harness model, component registry, guardrail/improvement taxonomy, or cross-cutting concept language | `harness.md`, `components.md`, `guardrails.md`, `improvement-loop.md`, `core-concepts-and-rules.md` |
| Product scope, non-goals, or default runtime | `product-direction.md`, `harness.md` |
| Financial workflow intake, non-expert UX, suitability/profile context, or plain-English finance output | `financial-workflow-references.md`, `interfaces-and-surfaces.md`, `roles-skills-and-workflows.md` |
| Django apps, models, service-layer contracts, or DB ownership | `system-architecture.md` |
| Product web, Admin, REST, MCP, CLI, or wrapper behavior | `interfaces-and-surfaces.md` |
| Guardrails, policy, permission, approval, execution, adapter, or secret boundary | `guardrails.md`, `safety-policy-and-execution.md`, `core-concepts-and-rules.md` |
| Subagent roster, role responsibilities, skills, strategy behavior, workflow routing, decision-quality routing, or information barriers | `roles-skills-and-workflows.md`, `artifact-supervisor-loop-prd.md`, `core-concepts-and-rules.md` |
| Research memory, artifacts, exports, source snapshots, forecast ledgers, report readiness, or postmortem learning | `improvement-loop.md`, `research-memory-and-artifacts.md`, `artifact-supervisor-loop-prd.md` |
| Generated workspace structure, templates, hooks, bootstrap, or MCP config | `generated-workspaces.md` |
| Test coverage or validation commands | `validation-and-test-plan.md` |
| PyPI/TestPyPI release, CI/CD, or package distribution boundary | `deployment.md`, `product-direction.md` |
| License, contribution, trademark, or monetization boundary | `licensing-and-commercialization.md` |
