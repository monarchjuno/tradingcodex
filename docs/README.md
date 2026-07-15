# TradingCodex Documentation

This directory is the human-readable source of truth linked from the public `README.md`. It explains the product model, operating rules, safety boundaries, generated workspace behavior, implementation architecture, validation expectations, release policy, and licensing posture.

Use these docs when you want the deeper "why" behind TradingCodex behavior. Coding agents should use `openwiki/` for fast source navigation, then return here before changing durable product behavior.

New to TradingCodex? Start with the [User Guide](https://monarchjuno.github.io/tradingcodex/) for setup, a first analysis, the workspace viewer, and everyday safety boundaries.

## How To Read These Docs

| Reader goal | Start with | Then read |
| --- | --- | --- |
| Understand TradingCodex quickly | [core-concepts-and-rules.md](./core-concepts-and-rules.md) | [product-direction.md](./product-direction.md), [harness.md](./harness.md) |
| Install or update a workspace | [../installation.md](../installation.md) | [generated-workspaces.md](./generated-workspaces.md), [deployment.md](./deployment.md) |
| Understand which user skill to start with | [user-facing-skills.md](./user-facing-skills.md) | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md), [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) |
| Understand the investment workflow | [harness.md](./harness.md) | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md), [research-memory-and-artifacts.md](./research-memory-and-artifacts.md) |
| Understand decision memory, replay, and lessons | [decision-memory.md](./decision-memory.md) | [research-memory-and-artifacts.md](./research-memory-and-artifacts.md), [improvement-loop.md](./improvement-loop.md) |
| Understand safety and execution boundaries | [safety-policy-and-execution.md](./safety-policy-and-execution.md) | [guardrails.md](./guardrails.md), [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) |
| Understand implementation architecture | [system-architecture.md](./system-architecture.md) | [interfaces-and-surfaces.md](./interfaces-and-surfaces.md), [components.md](./components.md) |
| Change prompts, skills, hooks, routing, or generated workspaces | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) | [generated-workspaces.md](./generated-workspaces.md), [validation-and-test-plan.md](./validation-and-test-plan.md) |
| Review release readiness | [release-readiness.md](./release-readiness.md) | [validation-and-test-plan.md](./validation-and-test-plan.md), then the changed topic documents |
| Validate a change before release or handoff | [validation-and-test-plan.md](./validation-and-test-plan.md) | The topic document for the changed area |

## Documentation Layers

| Layer | Audience | Role |
| --- | --- | --- |
| `README.md` | New users and evaluators | Product summary, installation path, feature overview, and links into these docs. |
| `installation.md` | Operators | Setup, update, installer, MCP/service startup, and smoke checks. |
| `docs/` | Humans and maintainers | Durable product source of truth and deeper design rationale. |
| `guidebook/` | End users | Static task-first user guide published to GitHub Pages; it summarizes common journeys and links back to canonical detail. |
| `openwiki/` | Coding agents | Fast repository map and edit/validation routing. |
| `AGENTS.md` | Coding agents | Hard rules, setup guard, coding rules, and validation requirements. |

If these layers disagree, treat `docs/` as the durable product intent and fix the mismatch deliberately. Changes to a user-visible setup, task, skill, viewer, recovery, provider, or order journey also require a `guidebook/` update in the same change. Do not let hidden behavior drift live only in code, tests, prompts, skills, hooks, or generated templates.

## Core Documents

| Document | Owns |
| --- | --- |
| [core-concepts-and-rules.md](./core-concepts-and-rules.md) | Fast operating reference for planes, guardrails, role boundaries, execution lifecycle, research memory, and documentation rules. |
| [product-direction.md](./product-direction.md) | Product thesis, target user posture, goals, non-goals, product language, default runtime, universe scope, and open-core posture. |
| [harness.md](./harness.md) | Investment OS orchestration/runtime subsystem: roles, skills, service-layer state, policy, MCP, research memory, approvals, execution adapters, audit, and improvement loops. |
| [components.md](./components.md) | Component-first maintenance registry, taxonomy tags, owned surfaces, dependencies, capabilities, and validation. |

## Active Release

| Document | Purpose |
| --- | --- |
| [release-readiness.md](./release-readiness.md) | v1.0.0 source readiness, final-commit and exact-artifact validation, and pending tag, publication, and post-publish gates. |

## Workflow And Agent Documents

| Document | Owns |
| --- | --- |
| [user-facing-skills.md](./user-facing-skills.md) | User-facing primary and supporting skills, routing posture, and role-owned skill boundaries. |
| [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) | Fixed role roster, no-overlap role contract, head-manager dispatch gate, skills, strategy skills, subagent isolation, workflow routing, and module graph. |
| [research-memory-and-artifacts.md](./research-memory-and-artifacts.md) | File-native research memory, source snapshots, artifact paths, readiness labels, report quality floor, forecast ledger posture, and handoff metadata. |
| [decision-memory.md](./decision-memory.md) | Ledger-first decision memory, historical replay and live forward evidence, postmortem and lesson lifecycle, strategy snapshots, investor context, skill-first UX, and evaluation. |
| [financial-workflow-references.md](./financial-workflow-references.md) | Research-backed finance workflow principles and non-expert UX requirements for request scoping and handoffs. |
| [codex-native-orchestration.md](./codex-native-orchestration.md) | Dynamic Head Manager orchestration, lightweight analysis runs, exact-role dispatch, artifact lineage, and the Django boundary. |

## Safety And Improvement Documents

| Document | Owns |
| --- | --- |
| [guardrails.md](./guardrails.md) | Guardrail taxonomy: guidance, enforcement, and information barriers. |
| [safety-policy-and-execution.md](./safety-policy-and-execution.md) | Permission checks, approval rules, execution lifecycle, adapter boundary, external MCP gate, blocked actions, and secret handling. |
| [improvement-loop.md](./improvement-loop.md) | Workflow quality, research memory, improve records, skill evolution, postmortem review, validation feedback, and quality-learning loops. |

## Implementation And Operations Documents

| Document | Owns |
| --- | --- |
| [system-architecture.md](./system-architecture.md) | Django modular monolith, central DB ownership, app boundaries, runtime planes, service-layer use cases, and core models. |
| [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) | Product web, Django Admin, Django Ninja API, MCP boundary, CLI, generated wrapper behavior, and external MCP surface. |
| [generated-workspaces.md](./generated-workspaces.md) | `tcx attach`, `tcx update`, generated files, project-scoped MCP config, hooks, workspace provenance, profile scope, and template rules. |
| [investment-brain-plugins.md](./investment-brain-plugins.md) | Product plan for high-freedom community TradingCodex Investment Brain plugins, Codex-skill projection, layer/override semantics, Decision Memory boundaries, and Git-managed user workspaces. |
| [validation-and-test-plan.md](./validation-and-test-plan.md) | Required validation commands, unit/API/generator/smoke coverage, MCP smokes, broker provider smokes, routing scenarios, and release-sensitive checks. |
| [deployment.md](./deployment.md) | PyPI release process, CI/CD, Trusted Publishing, installer/update policy, versioning, and what is not deployed. |
| [licensing-and-commercialization.md](./licensing-and-commercialization.md) | Apache-2.0 open-core boundary, generated workspace ownership, contributions, trademark posture, and legal review needs. |

## Change-To-Docs Map

| Change type | Update these docs |
| --- | --- |
| Product scope, non-goals, default runtime, product language, or release posture | `product-direction.md`, `core-concepts-and-rules.md` |
| Investment OS or harness-subsystem model, component registry, guardrail/improvement taxonomy, or cross-cutting concept language | `product-direction.md`, `harness.md`, `components.md`, `guardrails.md`, `improvement-loop.md`, `core-concepts-and-rules.md` |
| User-facing request scoping, investor-context suitability, plain-English output, or professional finance framing | `financial-workflow-references.md`, `interfaces-and-surfaces.md`, `roles-skills-and-workflows.md` |
| Role roster, model policy, head-manager dispatch, skills, Investment Brain plugins, strategy behavior, routing, information barriers, or handoff quality | `release-readiness.md`, `investment-brain-plugins.md`, `codex-native-orchestration.md`, `roles-skills-and-workflows.md`, `harness.md`, `generated-workspaces.md`, `core-concepts-and-rules.md` |
| Research or decision memory, source snapshots, ResearchSpec/replay/ExperimentRun, forecast/calibration ledgers, postmortem lessons, search indexes, readiness labels, artifact paths, report quality, or markdown preview | `decision-memory.md`, `research-memory-and-artifacts.md`, `improvement-loop.md`, `codex-native-orchestration.md` |
| Policy, permissions, approvals, idempotency, execution, adapters, broker safety, external MCP gate, or secret handling | `safety-policy-and-execution.md`, `guardrails.md`, `core-concepts-and-rules.md` |
| Django apps, models, service-layer contracts, central DB ownership, or runtime topology | `system-architecture.md` |
| Product web, Admin, REST, MCP, CLI, or generated wrapper behavior | `interfaces-and-surfaces.md` |
| Workspace templates, bootstrap, hooks, project MCP config, generated files, or update behavior | `generated-workspaces.md` |
| User-facing setup, everyday task, skill, viewer, recovery, provider, or order onboarding journey | The owning canonical topic document plus `guidebook/` |
| Test expectations, smoke flows, validation commands, or regression scenarios | `validation-and-test-plan.md` |
| Packaging, installer, release automation, versioning, or distribution boundary | `deployment.md`, `installation.md` |
| License, contribution, trademark, generated workspace ownership, or commercialization boundary | `licensing-and-commercialization.md` |

## Source-Of-Truth Principles

| Principle | Meaning |
| --- | --- |
| Docs first for product behavior | Durable rules for safety, roles, workflows, execution, generated workspaces, and release posture belong in these docs. |
| Update docs with behavior | Code, templates, prompts, hooks, tests, and generated artifacts that change durable behavior should update the owning doc in the same change. |
| Keep `README.md` concise | The public README should explain the product and route readers here instead of duplicating every rule. |
| Keep OpenWiki agent-focused | `openwiki/` should help agents work efficiently and link back to these docs for durable explanations. |
| Keep product language English | TradingCodex product copy, generated workspace guidance, Admin UI, CLI help, role prompts, durable docs, and examples are written in English unless a reviewed localization layer is being built. |
| Product web is read-only | The `/` React viewer exposes Library, Skills, and System for a registered selected workspace. Native Codex owns analysis and the viewer exposes no mutation path. |
| Open-core boundary is explicit | Apache-2.0 covers the repository open core; trademarks and official commercial offerings remain separately governed. |
