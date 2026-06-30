# Harness Components

This document owns the component-first maintenance model for TradingCodex.
Components are the implementation and change units. Guardrails and Improvement
remain product taxonomy tags and review lenses over those components.

The canonical registry lives in
`tradingcodex_service.application.components`. Docs, API responses, product web
views, and generated workspace indexes are projections of that Python registry.

## Component Contract

Each component has:

- `id`, `label`, `summary`, and `status`
- taxonomy `tags`, such as `guardrail.guidance`,
  `guardrail.enforcement`, `guardrail.information_barrier`,
  `improvement.workflow_quality`, or `improvement.research_memory`
- `surfaces`, such as instructions, skills, hooks, services, templates,
  models, MCP tools, and tests
- `depends_on`, `owned_capabilities`, and `validation`

Tags do not grant permissions and do not define implementation ownership. They
help humans, the API, and the product web view explain why a component exists.

Implementation refactors should follow component and service-use-case
boundaries. Do not create primary ownership packages named after taxonomy
lenses such as `guardrails` or `improvement`; those labels can span multiple
components. When a component grows too large, prefer smaller modules for its
registry data, validation, file projection, rendering, adapters, dispatch, or
ledger behavior while keeping the `0.2.0` canonical routes and direct import
paths clear.

## Current Components

| Component | Purpose | Primary tags |
| --- | --- | --- |
| `investment-request-routing` | Classifies user intent and activates fixed-role workflows. | `guardrail.guidance`, `improvement.workflow_quality` |
| `fixed-role-dispatch` | Maintains head-manager, fixed subagent routing, and no-overlap handoff boundaries. | `guardrail.guidance`, `guardrail.information_barrier`, `improvement.workflow_quality` |
| `research-memory` | Stores source-aware research artifacts, versions, snapshots, and exports. | `improvement.research_memory` |
| `workflow-quality-gates` | Defines lane selection, Artifact Supervisor Loop policy, Decision Quality Spine, handoff acceptance, artifact readiness, claim discipline, and synthesis gates. | `guardrail.guidance`, `improvement.workflow_quality` |
| `decision-package` | Wraps workflow plans, artifact paths, profile gaps, blocked actions, and next steps in Codex-readable workspace markdown. | `guardrail.guidance`, `improvement.workflow_quality`, `improvement.research_memory` |
| `artifact-quality-contract` | Evaluates workspace artifacts and forecast ledgers for source/as-of posture, claim tags, handoff state, confidence, missing evidence, and next-recipient routing metadata. | `guardrail.guidance`, `improvement.workflow_quality`, `improvement.research_memory` |
| `context-efficiency-contract` | Keeps workflows bounded through compact briefs, artifact references, context summaries, source snapshot IDs, targeted full-artifact reads, and `subagents context-audit` validation. | `guardrail.guidance`, `guardrail.information_barrier`, `improvement.workflow_quality`, `improvement.context_efficiency` |
| `responsibility-boundary-contract` | Separates durable role identity, tool permissions, skill procedures, artifact contracts, and projection ownership so changes stay local. | `guardrail.guidance`, `guardrail.information_barrier`, `improvement.workflow_quality`, `improvement.skill_evolution` |
| `external-data-source-gate` | Keeps external evidence read-only and source-aware. | `guardrail.guidance`, `improvement.workflow_quality` |
| `external-mcp-proxy-gate` | Registers external MCP connections, imports metadata, classifies risk, manages lifecycle/review state, and blocks unsafe direct connection paths. | `guardrail.enforcement`, `guardrail.information_barrier` |
| `broker-center` | Acts as the local broker control plane for connector state, capability profiles, source drift, read-only account discovery, sync runs, mapping review, reconciliation, and service-gated execution. | `guardrail.enforcement`, `improvement.workflow_quality` |
| `secret-wall` | Blocks raw broker secrets from workspace files, prompts, shell paths, and role context. | `guardrail.enforcement`, `guardrail.information_barrier` |
| `policy-and-restricted-list` | Evaluates principals, capabilities, explicit deny rules, restricted symbols, and limits. | `guardrail.enforcement` |
| `approval-gate` | Validates order tickets, JSON order inputs, and approval receipts before execution-sensitive action. | `guardrail.enforcement` |
| `execution-boundary` | Keeps execution behind role action allowlists, approval, duplicate-request, connection, and audit checks. | `guardrail.enforcement`, `guardrail.information_barrier` |
| `audit-ledger` | Records policy, MCP, order, approval, execution, and hook events. | `guardrail.enforcement`, `improvement.validation_feedback` |
| `skill-improvement-loop` | Keeps core skills, strategy skills, and role-local optional skill files visible through validation, generated manifests, and read-only status. | `improvement.skill_evolution`, `guardrail.guidance` |
| `postmortem-loop` | Turns rejected orders, process failures, thesis changes, artifact-loop blocks/escalations, and executions into improvements. | `improvement.postmortems`, `improvement.validation_feedback` |
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
any affected prompts, skills, hooks, services, templates, tests, and docs listed
in that component's surfaces.

Do not split implementation work by Guardrails or Improvement taxonomy alone.
A single component may intentionally carry multiple tags.
