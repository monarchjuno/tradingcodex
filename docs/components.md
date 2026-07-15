# Harness Components

This document owns the component-first maintenance model for the TradingCodex
harness subsystem. TradingCodex is the investment OS; the harness is its
orchestration and runtime subsystem. Components are implementation and change
units inside that subsystem. Guardrails and Improvement remain product taxonomy
tags and review lenses over those components.

The canonical registry lives in
`tradingcodex_service.application.components`. Docs, API responses, product web
views, and generated workspace indexes are projections of that Python registry.

## Investment OS Layer Contract

Product layers and harness components answer different questions. Layers define
what may be replaced or extended; components identify where implementation and
validation work belongs.

| Product layer | Contract | Component relationship |
| --- | --- | --- |
| Core kernel | Evidence, method-fit, quality-gate, policy, approval, execution, audit, and provenance invariants cannot be removed or weakened by customization. | Cross-cutting invariants implemented and validated by multiple harness components. |
| Bundled investment capability pack | Fixed roles, built-in investment skills, method profiles, and evaluation profiles provide the pristine research, analysis, and forecast baseline. | Projected and coordinated through routing, dispatch, research, artifact-quality, and evaluation-related components. |
| Managed user overlays | Additional instructions, optional role skills, strategies, and explicitly installed Investment Brains add user methods and reasoning frames. | Managed by projection and skill-improvement surfaces while remaining subject to the kernel. |

Globally installed or plugin-provided host skills are discoverable external
capabilities, not part of the bundled pack. They require explicit user opt-in
for the current workflow or managed activation. The current component and
projection model must not be described as hard runtime isolation until the
active runtime passes clean-host, populated-host, name-collision, and invocation
tests.

## Component Contract

Each component has:

- `id`, `label`, `summary`, and `status`
- taxonomy `tags`, such as `guardrail.guidance`,
  `guardrail.enforcement`, `guardrail.information_barrier`,
  `improvement.workflow_quality`, or `improvement.research_memory`
- `surfaces`, such as instructions, skills, hooks, services, frontend views,
  APIs, workspace files, models, MCP tools, and tests
- `depends_on`, `owned_capabilities`, and `validation`

Tags do not grant permissions and do not define implementation ownership. They
help humans, the API, and the product web view explain why a component exists.

Implementation refactors should follow component and service-use-case
boundaries. Do not create primary ownership packages named after taxonomy
lenses such as `guardrails` or `improvement`; those labels can span multiple
components. When a component grows too large, prefer smaller modules for its
registry data, validation, file projection, rendering, adapters, dispatch, or
ledger behavior while keeping the v1 canonical routes and direct import paths
clear.

## Current Components

| Component | Purpose | Primary tags |
| --- | --- | --- |
| `codex-native-orchestration` | Lets Head Manager interpret multilingual requests and dynamically coordinate exact fixed roles. | `guardrail.guidance`, `improvement.workflow_quality` |
| `investment-brain-plugins` | Supports privacy-reviewed user-owned local Brain authoring, strict community bundle validation, explicit Head Manager-only projection, immutable versions, and receipt-authenticated run/artifact/Decision Memory provenance. | `guardrail.guidance`, `improvement.skill_evolution`, `improvement.research_memory` |
| `fixed-role-dispatch` | Maintains head-manager, fixed subagent routing, and no-overlap handoff boundaries in native Codex. | `guardrail.guidance`, `guardrail.information_barrier`, `improvement.workflow_quality` |
| `versioned-workspace` | Keeps generated workspaces in user-owned Git worktrees with privacy-first ignores and no automatic stage, commit, remote, or publication. | `guardrail.guidance`, `guardrail.information_barrier`, `improvement.validation_feedback` |
| `research-memory` | Stores source-aware research artifacts, versions, snapshots, Evidence Run Cards, Validation Cards, and exports. | `improvement.research_memory` |
| `workflow-quality-gates` | Defines artifact quality, Decision Quality Spine, agent judgment review, claim discipline, and synthesis lineage without a Django workflow loop. | `guardrail.guidance`, `improvement.workflow_quality` |
| `decision-package` | Freezes run provenance, artifact paths, forecasts, investor-context gaps, and outcomes in Codex-readable workspace records. | `guardrail.guidance`, `improvement.workflow_quality`, `improvement.research_memory` |
| `artifact-quality-contract` | Evaluates workspace artifacts, Evidence Run Cards, Validation Cards, source snapshots, and forecast ledgers for source/as-of posture, source trust, data-boundary warnings, claim tags, handoff state, confidence, missing evidence, judgment-review fields, and next-recipient routing metadata. | `guardrail.guidance`, `improvement.workflow_quality`, `improvement.research_memory` |
| `context-efficiency-contract` | Keeps workflows bounded through compact briefs, artifact references, context summaries, source snapshot IDs, targeted full-artifact reads, and `subagents context-audit` validation. | `guardrail.guidance`, `guardrail.information_barrier`, `improvement.workflow_quality`, `improvement.context_efficiency` |
| `responsibility-boundary-contract` | Separates durable role identity, tool permissions, skill procedures, artifact contracts, and projection ownership so changes stay local. | `guardrail.guidance`, `guardrail.information_barrier`, `improvement.workflow_quality`, `improvement.skill_evolution` |
| `tcx-source-gate` | Keeps external evidence read-only and source-aware. | `guardrail.guidance`, `improvement.workflow_quality` |
| `external-mcp-proxy-gate` | Registers external MCP connections, imports metadata, classifies risk, manages lifecycle/review state, and blocks unsafe direct connection paths. | `guardrail.enforcement`, `guardrail.information_barrier` |
| `broker-center` | Acts as the local broker control plane for connector state, capability profiles, source drift, read-only account discovery, sync runs, mapping review, reconciliation, and service-gated execution. | `guardrail.enforcement`, `improvement.workflow_quality` |
| `secret-wall` | Blocks raw broker secrets from workspace files, prompts, shell paths, and role context. | `guardrail.enforcement`, `guardrail.information_barrier` |
| `policy-and-restricted-list` | Evaluates principals, capabilities, explicit deny rules, restricted symbols, and limits. | `guardrail.enforcement` |
| `approval-gate` | Validates order tickets, JSON order inputs, and approval receipts before execution-sensitive action. | `guardrail.enforcement` |
| `execution-boundary` | Keeps execution behind role action allowlists, approval, duplicate-request, connection, and audit checks. | `guardrail.enforcement`, `guardrail.information_barrier` |
| `audit-ledger` | Records policy, MCP, order, approval, execution, and hook events. | `guardrail.enforcement`, `improvement.validation_feedback` |
| `skill-improvement-loop` | Keeps bundled skills and managed strategy or role-local optional skill overlays visible through validation, generated manifests, and read-only status without allowing overlays to replace kernel requirements. | `improvement.skill_evolution`, `guardrail.guidance` |
| `decision-memory-postmortems` | Preserves evidence-bound decision and process reviews so independently reviewed lessons can mature without a server-authored workflow. | `improvement.postmortems`, `improvement.validation_feedback` |
| `paper-execution` | Provides experimental local paper and validation-only execution paths behind the approved action boundary. | `guardrail.enforcement` |

## Runtime Surfaces

The registry is exposed through:

- service helpers: `list_harness_components`, `get_harness_component`, and
  `list_components_by_tag`
- Django Ninja API: `/api/harness/components` and
  `/api/harness/components/{component_id}`
- product web diagnostics: component maintenance map when exposed outside Admin
- generated workspace index:
  `.tradingcodex/generated/component-index.json`

## Change Rule

When a feature changes, update the component that owns the feature. Then update
any affected prompts, skills, hooks, services, frontend views, APIs, workspace
files, tests, and docs listed in that component's surfaces.

Do not split implementation work by Guardrails or Improvement taxonomy alone.
A single component may intentionally carry multiple tags.

Method profiles such as `general_evidence_v1`, `event_research_v1`,
`quant_signal_v1`, and `listed_equity_fcff_dcf_v1`, plus evaluation profiles
such as `core_investment_v1`, are product contracts coordinated across the
research-memory, workflow-quality, artifact-quality, and evaluation surfaces.
They do not need one speculative component per method.
