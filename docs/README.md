# TradingCodex Documentation

`docs/` is the canonical product reference. It explains durable behavior and
the reasons behind it; it is not a second user guide or a source-file index.

- New users start with the [User Guide](https://monarchjuno.github.io/tradingcodex/).
- Operators start with [installation.md](../installation.md).
- Maintainers start with [AGENTS.md](../AGENTS.md) and the codebase
  [Architecture](architecture.md), then read the owning page below before
  changing behavior.

## Documentation Ownership

| Layer | Purpose |
| --- | --- |
| `README.md` | Product summary and primary install route. |
| `installation.md` | Setup, update, and operator recovery. |
| `docs/` | Canonical product behavior, architecture, safety, and rationale. |
| `guidebook/` | Short task-first instructions for end users. |
| `AGENTS.md` | Repository-wide development rules. |

Keep a concept in one owning document and link to it elsewhere. Update another
layer only when its audience's route changes; do not copy a behavior contract
into every layer. If two layers disagree, fix the owning `docs/` page first.

## Start By Goal

| Goal | Canonical page | Useful follow-up |
| --- | --- | --- |
| Understand the product and its design posture | [Product Direction](product-direction.md) | [Harness](harness.md) |
| Understand runtime and source ownership | [Architecture](architecture.md) | [Interfaces And Surfaces](interfaces-and-surfaces.md) |
| Change Head Manager, roles, skills, or research flow | [Roles, Skills, And Workflows](roles-skills-and-workflows.md) | [Codex-Native Orchestration](codex-native-orchestration.md) |
| Work with research evidence, datasets, and artifacts | [Research Memory And Artifacts](research-memory-and-artifacts.md) | [Data Sources And OpenBB](data-sources-and-openbb.md) |
| Work with reusable company, product, technology, science, industry, or value-chain knowledge | [Knowledge Wikis](knowledge-wikis.md) | [Investment Brain Plugins](investment-brain-plugins.md) |
| Understand saved decisions and improvement | [Decision Memory](decision-memory.md) | [Improvement Loop](improvement-loop.md) |
| Change policy, approvals, brokers, or execution | [Safety, Policy, And Execution](safety-policy-and-execution.md) | [Guardrails](guardrails.md) |
| Change attach/update or generated workspace files | [Generated Workspaces](generated-workspaces.md) | [Deployment](deployment.md) |
| Change the viewer, API, MCP, Admin, or CLI | [Interfaces And Surfaces](interfaces-and-surfaces.md) | [Architecture](architecture.md) |
| Choose or verify validation | [Validation And Test Plan](validation-and-test-plan.md) | [Release Readiness](release-readiness.md) |

## Reference By Domain

### Product And Research

- [Product Direction](product-direction.md) — product thesis, native-Codex
  posture, goals, non-goals, defaults, and scope.
- [Harness](harness.md) — how Codex, services, and workspace files cooperate.
- [Financial Workflow References](financial-workflow-references.md) — finance
  workflow evidence and non-expert output requirements.
- [User-Facing Skills](user-facing-skills.md) — direct skill entrypoints and
  user-visible responsibilities.
- [Roles, Skills, And Workflows](roles-skills-and-workflows.md) — agent and
  skill behavior, research routing, and handoffs.
- [Codex-Native Orchestration](codex-native-orchestration.md) — the durable run
  boundary and why Django is not an agent scheduler.
- [Research Memory And Artifacts](research-memory-and-artifacts.md) — file-native
  evidence, datasets, calculations, artifacts, and provenance.
- [Knowledge Wikis](knowledge-wikis.md) — agent-maintained Obsidian-compatible
  background knowledge, explicit write admission, community packages, and the
  read-only Viewer.
- [Data Sources And OpenBB](data-sources-and-openbb.md) — source fallback,
  optional direct OpenBB MCP, and third-party terms.
- [Decision Memory](decision-memory.md) and [Improvement Loop](improvement-loop.md)
  — retained decisions, outcomes, lessons, and review.

### Safety And Architecture

- [Safety, Policy, And Execution](safety-policy-and-execution.md) — canonical
  sensitive-action, approval, broker, secret, and audit boundary.
- [Guardrails](guardrails.md) — guidance, enforcement, and information-barrier
  taxonomy.
- [Architecture](architecture.md) — codebase map, consequential request flow,
  runtime planes, central DB, app boundaries, and service ownership.
- [Interfaces And Surfaces](interfaces-and-surfaces.md) — viewer, Admin, API,
  MCP, CLI, and generated wrapper boundaries.
- [Generated Workspaces](generated-workspaces.md) — attach/update, generated
  files, projection, provenance, and platform rules.
- [Investment Brain Plugins](investment-brain-plugins.md) — current managed
  Brain behavior and migration context; native user skills remain the preferred
  direction for future simplification.

### Operations And Governance

- [Validation And Test Plan](validation-and-test-plan.md) — complete validation
  matrix; use the smallest relevant subset during iteration.
- [Release Readiness](release-readiness.md) — reusable release and publication
  checklist; live status remains in GitHub and PyPI.
- [Deployment](deployment.md) — packaging, publication, update, and CI policy.
- [Licensing And Commercialization](licensing-and-commercialization.md) — open
  source, generated workspace, trademark, and legal-review boundaries.

## Writing Rules

- Document durable behavior, not temporary implementation plans or completed
  checklists; Git history preserves those.
- Prefer links to repeated explanations. Keep the codebase map in
  `architecture.md` and validation routing in `AGENTS.md`, not in parallel
  documentation layers.
- Keep user procedures in `guidebook/`; keep internal source paths out of it.
- Keep product copy in English unless a reviewed localization layer is added.
- A behavior change updates its owning page in the same change. A code-only
  refactor that preserves behavior need not churn every documentation layer.
