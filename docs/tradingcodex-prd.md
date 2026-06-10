# TradingCodex PRD

This document is the top-level product contract for TradingCodex. It stays short
on purpose: detailed product rules live in the topic documents linked below.
When code, templates, prompts, or tests disagree with this PRD or its linked
source documents, resolve the mismatch in the same change.

## Product Definition

TradingCodex is a local-first trading harness that runs Codex-assisted
investment workflows as a Python/Django modular monolith. Codex projects are
workspaces and clients; the TradingCodex Django service and central local DB
are the investment ledger.

Codex, subagents, the local CLI, product web app, Django Admin, Django Ninja
API, and Django-hosted MCP all call the same service layer. Executable actions
must pass principal, capability, policy, schema, approval, idempotency, adapter,
and audit checks. Runtime state and research memory are canonical in the
Django DB; markdown/json files are human-readable export, cache, and artifact
layers for Codex.

TradingCodex is not financial, investment, legal, tax, or regulatory advice. It
is research, workflow, and execution-guardrail tooling. Users remain
responsible for decisions, broker integrations, compliance, and outcomes.

## Current Baseline

| Area | Baseline |
| --- | --- |
| Runtime | Python/Django-native local service plane; latest supported Python line and Django 5.2.x target are defined in project metadata and docs. |
| Database | Central local SQLite at `~/.tradingcodex/state/tradingcodex.sqlite3`, with `TRADINGCODEX_HOME` and `TRADINGCODEX_DB_NAME` overrides. |
| Web | Product dashboard at `/` for visual review, not execution. |
| Admin | Django Admin as a local/staff operations console, not a bypass. |
| API | Django Ninja for typed local/staff status, validation, and control APIs. |
| MCP | Django-hosted MCP endpoint and stdio bridge as the agent/tool execution boundary. |
| Execution | Paper/stub adapters only in the initial core; live broker adapters are disabled and unimplemented. |
| Research memory | Central DB-first, with markdown exports and source/as-of metadata. |
| Generated workspaces | Codex-readable clients with immutable workspace ids and provenance through `TRADINGCODEX_WORKSPACE_ROOT`, not separate investment ledgers. |
| Product language | English durable docs, generated guidance, Admin UI, CLI help, role prompts, and product copy. |
| License posture | Apache-2.0 open core with separate trademark and commercial-offering boundaries. |

## Product Goals

| Goal | Meaning |
| --- | --- |
| Codex-native workflow | Preserve `.codex/`, `.agents/skills/`, hooks, generated workspace behavior, and role prompts. |
| Durable service plane | Put policy, order, portfolio, audit, harness, integration, research, and MCP logic behind Django services. |
| DB-first memory | Store mutable runtime state, research markdown, source snapshots, order lifecycle, portfolio state, policy decisions, and audit metadata in the Django DB. |
| Visual harness dashboard | Use `/` to show the main agent, subagents, skills, MCP boundaries, policy gates, research memory, portfolio state, and ledger activity. |
| Deterministic execution boundary | Revalidate principal, capability, policy, schema, approval, idempotency, adapter, and audit before executable actions. |
| Harness operations console | Use Django Admin to inspect and operate roster, skills, proposals, policy, tools, workflow runs, approvals, executions, and audit events. |
| Typed local APIs | Use Django Ninja for local/staff REST and control APIs without bypassing MCP/service-layer execution policy. |
| Broader investment universe | Public equity is the deepest first sleeve, while ETF/index, public crypto, macro/rates/FX/commodities, options, credit-signal, and cross-asset workflows remain extensible. |
| Extensible adapters | Ship paper/stub execution first; introduce live adapters only through explicit docs, policy, approval, adapter, idempotency, and audit boundaries. |

## Non-Goals

- No built-in live broker execution in the initial core.
- No raw broker credential storage in this repository or generated workspaces.
- No REST endpoint that bypasses MCP/service-layer execution policy.
- No Django-owned SDK agent orchestration in v1.
- No automatic subagent spawning without explicit Codex/delegated workflow capability.
- No claim that research-only workflows are decision-ready.
- No hidden policy drift in templates, hooks, tests, prompts, or skills without a matching docs update.
- No trademark grant, official endorsement grant, hosted-service license, or verified-adapter/commercial-pack license through the open-core source license.

## Detailed Source Documents

| Topic | Source document |
| --- | --- |
| Top-level harness model and Guardrails/Improvement split | [harness.md](./harness.md) |
| Guidance, enforcement, and information-barrier guardrails | [guardrails.md](./guardrails.md) |
| Workflow quality, research memory, skill proposals, postmortems, validation feedback | [improvement-loop.md](./improvement-loop.md) |
| Product definition, user posture, release scope, current defaults | [product-direction.md](./product-direction.md) |
| Django architecture, apps, service layer, DB ownership, models | [system-architecture.md](./system-architecture.md) |
| Product web, Admin, REST, MCP, CLI, generated wrapper | [interfaces-and-surfaces.md](./interfaces-and-surfaces.md) |
| Guardrails, approvals, policy, execution lifecycle, blocked actions | [safety-policy-and-execution.md](./safety-policy-and-execution.md) |
| Roles, skills, workflow routing, head-manager dispatch, subagent isolation | [roles-skills-and-workflows.md](./roles-skills-and-workflows.md) |
| Research memory, source snapshots, artifacts, freshness, readiness labels | [research-memory-and-artifacts.md](./research-memory-and-artifacts.md) |
| Generated workspace contract, bootstrap behavior, MCP config | [generated-workspaces.md](./generated-workspaces.md) |
| Required validation and test plan | [validation-and-test-plan.md](./validation-and-test-plan.md) |
| Release and deployment | [deployment.md](./deployment.md) |
| Licensing and commercialization | [licensing-and-commercialization.md](./licensing-and-commercialization.md) |

## Test Expectations

Every source or test change should run the relevant checks from
[validation-and-test-plan.md](./validation-and-test-plan.md). At minimum:

```bash
pytest
python manage.py check
```

Broader Python, template, generated workspace, MCP, and research-memory changes
have additional smoke checks documented in the validation plan.
