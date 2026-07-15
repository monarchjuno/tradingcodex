# TradingCodex Core Concepts And Rules

This is the fast reference for TradingCodex operating concepts. It points to
the detailed source documents rather than repeating every rule. Use
[product-direction.md](./product-direction.md) for product scope and non-goals.

## One-Line Product Definition

TradingCodex is a local-first investment OS on Codex. It provides a pristine
research, analysis, and scoreable-forecast baseline, managed customization, and
service-gated investment actions while ensuring every executable action crosses
deterministic Django service checks.

## Core Principles

| Principle | Meaning | Implementation rule |
| --- | --- | --- |
| Agents request actions | Agents analyze, review, draft order tickets, and request validation. | Natural-language answers must not become broker actions. |
| Django service layer is canonical | Product web, Admin, REST, MCP, and CLI call the same application services. | Do not duplicate policy, order, approval, execution, research indexing, or audit logic per interface. |
| Central DB is the runtime ledger | Execution-sensitive mutable state lives in the user-level central Django DB. | Do not use workspace paths as portfolio/order/account ledgers. |
| Workspace files are Codex-native SOT | Agent config, skill bundles, and research handoff markdown live in workspace files. | Codex-visible state must be projected to files rather than hidden only in DB rows. |
| Service gates own executable actions | Executable action boundaries belong to TradingCodex service-layer and MCP paths. | Raw API keys must not appear in workspace files, shell output, prompts, API responses, MCP responses, or audit output. |
| Enforcement is deterministic | Blocking decisions are reproducible code/policy decisions. | Final order paths revalidate policy, payload shape, approval, duplicate-request status, connection, and audit. |
| Capability is an allowlist | Permission is explicit and narrow. | Allowing one action does not imply policy write, secret read, or cash movement. |
| Information barriers control knowledge flow | Roles receive only the information they need. | Maintain research, execution, policy, and secret walls. |
| Improvement loops raise work quality | The harness should improve investment work, not only block bad actions. | Manage skills, schemas, workflows, checklists, validation feedback, and postmortems together. |
| Claim discipline limits false certainty | Investment outputs separate facts, inferences, and assumptions. | Use `[factual]`, `[inference]`, and `[assumption]` in narrative handoffs where useful. |
| Handoffs prevent role overlap | Downstream roles consume accepted upstream artifacts instead of redoing predecessor work. | Missing, stale, weak, or out-of-scope artifacts return `revise`, `blocked`, or `waiting` states. |
| Head Manager owns analytical decomposition | Interpret the live mandate, begin a lightweight run, and choose or revise exact roles from accepted evidence. | Do not restore a server intent classifier, default universe team, or materialized DAG. |

## Investment OS And Harness Model

TradingCodex is the top-level investment OS. The harness is its orchestration
and runtime subsystem: it coordinates the core kernel, bundled investment
capability pack, managed user overlays, durable service plane, Codex control
plane, workspace system plane, agent roster, research memory, policy, MCP
boundary, audit ledger, and validation feedback.

Under the harness, two cross-cutting systems explain most product behavior:

```text
TradingCodex Investment OS
  -> Harness orchestration/runtime subsystem
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

Use [harness.md](./harness.md) for the orchestration/runtime model,
[guardrails.md](./guardrails.md) for safety taxonomy, and
[improvement-loop.md](./improvement-loop.md) for quality and learning loops.

## Runtime Planes

| Plane | Responsibility | Source document |
| --- | --- | --- |
| Codex control plane | Agent behavior, workflow, tool surface, prompts, skills, hooks, and Codex-level guidance | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md), [generated-workspaces.md](./generated-workspaces.md) |
| Django service plane | Durable policy/order/portfolio/audit/harness/integration logic, product web, Admin, Ninja, stdio MCP bridge, and research file indexing | [system-architecture.md](./system-architecture.md), [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) |
| Workspace system plane | Generated schemas, policy exports, MCP wrappers, research markdown, readable artifacts, audit directories | [generated-workspaces.md](./generated-workspaces.md), [research-memory-and-artifacts.md](./research-memory-and-artifacts.md) |

Workspace identity is the attached Codex workspace identity. Research handoffs are
workspace-local so agents and humans can read the same markdown. Portfolio,
order, account, and strategy identity belongs to internal paper-account runtime
scope, not workspace paths. Optional investor suitability context and
`strategy-*` skill definitions are separate workspace-file guidance for agent
judgment; they do not replace account scope, portfolio state, or execution
policy.

## Guardrails And Improvement

| Type | Purpose | Examples | Detailed rules |
| --- | --- | --- | --- |
| Guidance guardrail | Reduce risky behavior before it reaches execution. | `AGENTS.md`, skills, role prompts, hooks, checklists | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) |
| Enforcement guardrail | Deterministically block risky action completion. | permissions, policy, approval checks, MCP allowlists | [safety-policy-and-execution.md](./safety-policy-and-execution.md) |
| Information barrier | Control knowledge and file-access flow. | role skill-source boundaries, restricted list, secret wall | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md), [safety-policy-and-execution.md](./safety-policy-and-execution.md) |
| Improvement loop | Standardize artifact quality, learn from outcomes, and improve future workflow behavior. | workflows, quality gates, readiness labels, research and decision memory, file-native skill proposals, postmortems, lesson validation, validation feedback | [decision-memory.md](./decision-memory.md), [improvement-loop.md](./improvement-loop.md), [research-memory-and-artifacts.md](./research-memory-and-artifacts.md), [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) |

## Role Boundary Snapshot

TradingCodex always uses `head-manager` as the main coordinator with nine fixed
analytical and decision-support subagents. There is no execution subagent.
Detailed responsibilities live in
[roles-skills-and-workflows.md](./roles-skills-and-workflows.md).

| Role | Owns | Never allowed |
| --- | --- | --- |
| `head-manager` | dispatch, coordination, synthesis, validation/audit status | direct broker APIs, direct investment conclusion without role output |
| analyst roles | research, evidence, valuation, market/instrument context | order approval, execution, secret read |
| `portfolio-manager` | portfolio fit and draft order ticket | self-approval, execution, arbitrary policy change |
| `risk-manager` | risk review, policy review, approval receipt | order drafting, execution, arbitrary policy change |

Handoff states are `accepted`, `revise`, `blocked`, or `waiting`. Only accepted
artifacts move downstream. `head-manager` may synthesize accepted outputs and
conflicts, but must not repair missing specialist work with direct analysis.
When synthesis is allowed, the full user-facing synthesis should be saved as a
workspace Markdown report; the chat reply should stay brief and point to that
report.

## Execution Lifecycle Snapshot

Detailed execution rules live in
[safety-policy-and-execution.md](./safety-policy-and-execution.md).

```text
evidence -> analysis -> portfolio fit -> draft order -> risk review
  -> approval receipt -> exact immediate action or protected turn grant
  -> service connection
  -> audit/postmortem
```

Every executable use case follows one of two entries:

```text
exact root native action -> deterministic hook parse -> native-user permission
  -> policy -> payload validation -> approval/duplicate-request check
  -> mandatory intent audit -> connection -> finalized/uncertain audit

exact first-line $tcx-order-allow -> workspace/session/turn/prompt/mode grant
  -> protected use_order_turn_grant + PreToolUse proof -> consume once
  -> the same canonical policy/approval/idempotency/live/audit gates
```

The root `tcx-order-submit` and `tcx-order-cancel` skill bundles
carry no tools. Their complete exact prompt grammar is intercepted by
`UserPromptSubmit` and dispatched in-process to the service gateway before any
model runs. The separate `tcx-order-allow` bundle documents current-turn admission:
an exact physical first line issues one `OrderTurnGrant`, and only root Head
Manager can select one later submit or cancel through `use_order_turn_grant`.
`PreToolUse` injects proof that model input and direct MCP callers cannot supply.
The browser viewer has no mutation route. Fixed roles, public REST, generic
CLI, and unproven direct MCP calls cannot invoke the final mutation.

Paper and validation-only execution paths remain experimental local harness
behavior. Live broker adapters remain disabled and unimplemented in the initial
core.

## Research Memory Snapshot

Detailed research rules live in
[research-memory-and-artifacts.md](./research-memory-and-artifacts.md).
Decision replay, postmortem, lesson, strategy-snapshot, investor-context, and
Wiki/read-view rules live in [decision-memory.md](./decision-memory.md).

- `trading/research/*.md` and `trading/reports/*/*.md` are canonical workspace research handoff files.
- `WorkspaceContext` records Codex project provenance and powers web workspace selection.
- Source snapshots are workspace JSON files under `trading/research/source-snapshots/`; research MCP tools do not write DB tool-call ledger rows.
- MCP ledgers for non-research tools, orders, portfolio snapshots, and audit events remain DB-backed runtime records.
- Source/as-of posture, retrieved-at metadata, stale-data warnings, versioning,
  invalidation, and content hashes are more important than long-lived embedding
  memory.
- Wiki pages and graphs are rebuildable views. Frozen decisions, forecast
  events, outcomes, reviews, and source snapshots remain canonical.

## Documentation Rules

| Rule | Application |
| --- | --- |
| Durable rule change requires docs update | Product direction, safety rules, role responsibilities, approved action boundary, artifact contracts, and generated behavior require docs updates. |
| Docs are source of truth | Gateway and topic documents record intent above implementation. If implementation differs, decide explicitly which side changes. |
| Avoid hidden policy drift | Do not hide durable rules only in templates, tests, MCP, hooks, prompts, or skills. |
| Keep product language English | Do not add non-English durable copy to docs, generated workspace guidance, Admin UI, CLI help, role prompts, or examples. |
| Keep gateway docs readable | Add detail to topic documents; keep this reference focused on routing the reader to the right source. |
