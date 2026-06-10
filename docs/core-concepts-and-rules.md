# TradingCodex Core Concepts And Rules

This is the fast reference for TradingCodex operating concepts. It points to
the detailed source documents rather than repeating every rule. Use
[tradingcodex-prd.md](./tradingcodex-prd.md) for the product contract.

## One-Line Product Definition

TradingCodex is a Python/Django-native trading harness that lets an investor
use Codex for research, decision support, approvals, and stub/paper execution
while ensuring every executable action crosses a deterministic Django service
layer and TradingCodex MCP enforcement boundary.

## Core Principles

| Principle | Meaning | Implementation rule |
| --- | --- | --- |
| Agents request actions | Agents analyze, review, draft intents, and request validation. | Natural-language answers must not become broker actions. |
| Django service layer is canonical | Product web, Admin, REST, MCP, and CLI call the same application services. | Do not duplicate policy, order, approval, execution, or research logic per interface. |
| Central DB is the investment ledger | Mutable state and research markdown live in the user-level central Django DB. | Codex projects are clients/provenance; markdown/json files are export/cache/artifact layers. |
| MCP owns executable boundary | Executable action boundaries belong to TradingCodex MCP/service-layer paths. | Raw API keys must not appear in workspace files, shell output, prompts, API responses, MCP responses, or audit output. |
| Enforcement is deterministic | Blocking decisions are reproducible code/policy decisions. | Final order paths revalidate policy, schema, approval, idempotency, adapter, and audit. |
| Capability is an allowlist | Permission is explicit and narrow. | Allowing one action does not imply policy write, secret read, or cash movement. |
| Information barriers control knowledge flow | Roles receive only the information they need. | Maintain research, execution, policy, and secret walls. |
| Improvement loops raise work quality | The harness should improve investment work, not only block bad actions. | Manage skills, schemas, workflows, checklists, validation feedback, and postmortems together. |
| Claim discipline limits false certainty | Investment outputs separate facts, inferences, and assumptions. | Use `[factual]`, `[inference]`, and `[assumption]` in narrative handoffs where useful. |
| Workflow mapping improves routing | Classify universe and workflow type before dispatch. | Public equity is the first deep sleeve, not the only universe. |

## Harness Model

Harness is the top-level TradingCodex concept. It contains the durable service
plane, Codex control plane, workspace system plane, agent roster, role skills,
research memory, policy, MCP boundary, audit ledger, and validation feedback.

Under the harness, two cross-cutting systems explain most product behavior:

```text
TradingCodex Harness
  -> Guardrails
       -> Guidance guardrails
       -> Enforcement guardrails
       -> Information barriers
  -> Improvement
       -> Workflow quality
       -> Research memory and freshness
       -> Skill proposals
       -> Postmortems
       -> Validation/test feedback
```

Use [harness.md](./harness.md) for the top-level model,
[guardrails.md](./guardrails.md) for safety taxonomy, and
[improvement-loop.md](./improvement-loop.md) for quality and learning loops.

## Runtime Planes

| Plane | Responsibility | Source document |
| --- | --- | --- |
| Codex control plane | Agent behavior, workflow, tool surface, prompts, skills, hooks, and Codex-level guidance | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md), [generated-workspaces.md](./generated-workspaces.md) |
| Django service plane | Durable policy/order/portfolio/research/audit/harness/integration logic, product web, Admin, Ninja, MCP HTTP endpoint | [system-architecture.md](./system-architecture.md), [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) |
| Workspace system plane | Generated schemas, policy exports, MCP wrappers, readable exports/cache, audit directories | [generated-workspaces.md](./generated-workspaces.md), [research-memory-and-artifacts.md](./research-memory-and-artifacts.md) |

## Guardrails And Improvement

| Type | Purpose | Examples | Detailed rules |
| --- | --- | --- | --- |
| Guidance guardrail | Reduce risky behavior before it reaches execution. | `AGENTS.md`, skills, role prompts, hooks, checklists | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) |
| Enforcement guardrail | Deterministically block risky action completion. | permissions, policy, approval checks, MCP allowlists | [safety-policy-and-execution.md](./safety-policy-and-execution.md) |
| Information barrier | Control knowledge and file-access flow. | role file walls, restricted list, secret wall | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md), [safety-policy-and-execution.md](./safety-policy-and-execution.md) |
| Improvement loop | Standardize artifact quality, learn from outcomes, and improve future workflow behavior. | workflows, quality gates, readiness labels, research memory, skill proposals, postmortems, validation feedback | [improvement-loop.md](./improvement-loop.md), [research-memory-and-artifacts.md](./research-memory-and-artifacts.md), [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) |

## Role Boundary Snapshot

TradingCodex always uses `head-manager` as the main coordinator with nine fixed
subagents. Detailed responsibilities live in
[roles-skills-and-workflows.md](./roles-skills-and-workflows.md).

| Role | Owns | Never allowed |
| --- | --- | --- |
| `head-manager` | dispatch, coordination, synthesis, validation/audit status | direct broker APIs, direct investment conclusion without role output |
| analyst roles | research, evidence, valuation, market/instrument context | order approval, execution, secret read |
| `portfolio-manager` | portfolio fit and draft order intent | self-approval, execution, arbitrary policy change |
| `risk-manager` | risk review, policy review, approval receipt | order drafting, execution, arbitrary policy change |
| `execution-operator` | submit approved orders through TradingCodex MCP | raw broker API, secret read, policy change |

## Execution Lifecycle Snapshot

Detailed execution rules live in
[safety-policy-and-execution.md](./safety-policy-and-execution.md).

```text
evidence -> analysis -> portfolio fit -> draft order -> risk review
  -> approval receipt -> MCP submit_approved_order -> adapter -> audit/postmortem
```

Every executable use case follows:

```text
principal -> capability -> policy -> schema -> approval/idempotency -> adapter -> audit
```

Paper/stub execution remains experimental local harness behavior. Live broker
adapters remain disabled and unimplemented in the initial core.

## Research Memory Snapshot

Detailed research rules live in
[research-memory-and-artifacts.md](./research-memory-and-artifacts.md).

- `ResearchArtifact` and `ResearchArtifactVersion` are canonical DB records.
- `WorkspaceContext` records Codex project provenance, not state partitioning.
- `trading/research/*.md` and `trading/reports/*/*.md` are readable exports or caches.
- Source/as-of posture, retrieved-at metadata, stale-data warnings, versioning,
  invalidation, and content hashes are more important than long-lived embedding
  memory.

## Documentation Rules

| Rule | Application |
| --- | --- |
| Durable rule change requires docs update | Product direction, safety rules, role responsibilities, execution boundary, artifact contracts, and generated behavior require docs updates. |
| Docs are source of truth | PRD and topic documents record intent above implementation. If implementation differs, decide explicitly which side changes. |
| Avoid hidden policy drift | Do not hide durable rules only in templates, tests, MCP, hooks, prompts, or skills. |
| Keep product language English | Do not add non-English durable copy to docs, generated workspace guidance, Admin UI, CLI help, role prompts, or examples. |
| Keep gateway docs readable | Add detail to topic documents; keep this reference focused on routing the reader to the right source. |
