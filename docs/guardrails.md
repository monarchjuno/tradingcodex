# Guardrails

Guardrails are the safety subsystem under the top-level TradingCodex workspace orchestration model.
They reduce risky behavior, isolate sensitive information, and block unsafe
executable actions.

Guardrails are not the whole harness. They sit beside Improvement loops, which
raise work quality but do not authorize execution.

In implementation, guardrail classes are descriptive tags on harness
components. They are not folders, modules, or ownership buckets. Developers
change component-owned surfaces such as services, skills, hooks, prompts, MCP
tools, and tests; guardrail tags explain how those surfaces reduce, isolate, or
block risk.

## Guardrail Taxonomy

| Guardrail class | Purpose | Examples | Limit |
| --- | --- | --- | --- |
| Guidance guardrails | Reduce the chance of risky behavior before final action. | `AGENTS.md`, role prompts, skills, hooks, checklists, starter prompt nudges | Guidance is not enforcement unless it deterministically blocks final action. |
| Enforcement guardrails | Deterministically block risky action completion. | project-wide analysis sandbox, service permissions, policy, schemas, approval checks, MCP allowlists, adapter disablement, idempotency | Must sit on the final execution path. |
| Information barriers | Restrict knowledge flow, file access, secret exposure, and role tool surfaces. | role-local context, restricted list, secret wall, PreToolUse source/secret blocks, role MCP allowlists | Isolation is necessary but does not prove output quality. |

## Guidance Guardrails

Guidance guardrails shape behavior early:

- role instructions
- workflow prompts
- repo skills
- user-facing starter prompts
- hooks that classify prompts or warn about secrets
- checklists that remind roles of evidence and claim discipline

Guidance should use clear language, but it cannot be trusted as the final
approved action boundary. A model can misunderstand guidance; the service layer must
still enforce policy.

## Enforcement Guardrails

Enforcement guardrails block final action paths:

- active requester identity (`Principal`) requirement
- explicit action permission (`Capability`) allow/deny checks
- restricted symbol policy
- schema validation
- approval receipt validation
- self-approval denial
- idempotency/duplicate execution blocking
- live adapter disablement
- MCP role allowlists
- audit-required actions

Executable action enforcement follows:

```text
requester -> permission -> policy -> payload validation -> approval/duplicate-request check -> connection -> audit
```

## Information Barriers

Information barriers are guardrails that control what roles know and can access.
They are especially important because TradingCodex uses specialized role
subagents.

Information barriers cover:

- research walls
- execution walls
- policy walls
- secret walls
- role skill-source boundaries
- role-specific MCP allowlists
- the project-wide `trading-research` filesystem/network/environment boundary
- PreToolUse source, control, and secret-path blocks

Information barriers are also a maintainability boundary. A new role or tool
surface should update role TOML, hook boundaries, the MCP allowlist, and tests
together. Generic skills should not become the hidden place where file access,
role eligibility, or tool authority is widened.

The root `head-manager` may coordinate and inspect, but should not silently
perform role work that belongs to specialist workflows.

## What Guardrails Do Not Do

Guardrails do not:

- make research decision-ready
- replace source/as-of metadata
- prove valuation quality
- authorize execution by themselves
- make non-live execution look like production trading infrastructure
- turn product web routes into execution surfaces

Those concerns belong to Improvement loops, service-layer execution policy, or
explicit product scope decisions.
