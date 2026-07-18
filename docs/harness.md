# TradingCodex Harness

This page owns the durable contract between native Codex, generated workspace
files, and TradingCodex services. Detailed order and Build rules live in
[Safety, Policy, And Execution](safety-policy-and-execution.md); source routing
lives in [Data Sources And OpenBB](data-sources-and-openbb.md).

## Responsibility Split

| Plane | Owns | Does not own |
| --- | --- | --- |
| Native Codex | request interpretation, tool use, capability discovery, delegation, progress, and synthesis judgment | policy, approval, broker effects, or durable identity |
| Generated workspace | concise prompts and skills, role profiles, native permission profiles, hooks, launchers, and file-native research | semantic routing or a parallel agent runtime |
| Django services | authenticated research identity, lineage, portfolio/account state, policy, orders, approvals, brokers, execution, and audit | analyst scheduling, role selection, or a research DAG |

The viewer is read-only. It displays workspace and service state but never starts
Codex or mutates research, skills, policy, brokers, or orders.

## Native-First Runtime

TradingCodex does not pin a model or reasoning effort. Head Manager and child
profiles inherit the user's current Codex defaults. A role profile supplies a
specialty, tool surface, web posture, and MCP principal; it is optional unless
that distinct expertise is needed.

The `trading-research` profile is the normal analysis environment. Native Codex
governs ordinary shell, public network, browser, and user-file access. Generated
permissions deny secrets, TradingCodex runtime state, protected research and
order ledgers, and local/private services. Deterministic numeric work uses the
separate `tcx-calc` launcher and calculation runtime; its launcher and runner
validate the scratch-local script boundary.

Do not duplicate native permission, tool-discovery, or command parsing in a
hook. Put a restriction in TradingCodex only when it protects a TradingCodex
invariant that native Codex cannot enforce by itself.

## Orchestration

Head Manager takes the smallest safe path:

1. Answer a narrow trusted fact or recorded status directly.
2. Begin one lightweight analysis run only when fresh research, decision
   support, or multiple specialties are needed.
3. Dispatch the smallest useful set of role profiles. Independent questions may
   run in parallel.
4. Reuse a live child with `followup_task` for its own correction or
   clarification. Add another child for a new specialty or independent review.
5. Use a bounded generic child when an exact profile is unavailable, preserving
   the same research-only brief and all secret, broker, policy, approval, and
   execution prohibitions.
6. Persist a result only when it supports a decision, reuse, audit, or a
   downstream handoff. Otherwise return the bounded answer directly.

Progress updates report material changes or prevent about a minute of visible
silence. A wait timeout alone is not progress. No server lane, preset team,
stored DAG, task queue, or supervisor loop decides the workflow.

## Evidence And Context

One role owns each external data family. Its `tcx-source-gate` procedure uses:

```text
adequate Snapshot/Dataset
  -> one relevant user capability
  -> optional direct OpenBB MCP
  -> official-source-first native research
  -> another credible source
  -> explicit gap
```

Partial success is retained and only missing coverage falls through. External
sources become SourceSnapshots; reusable structured rows become immutable
Datasets. Handoffs carry compact Snapshot/Dataset/Artifact IDs rather than raw
source dumps. This is skill guidance, not a provider router or trust engine.

## Hook Boundary

The generated hook is deliberately small. It owns:

- redacted session/run and subagent lifecycle context;
- redacted native spawn and follow-up call metadata without role selection;
- exact parsing of reserved root action prompts;
- current-turn proof issue, reservation, and injection for Build, Brain,
  Strategy, and final order service calls;
- direct secret-path, raw-credential, service-owned ledger, and broker/order
  bypass blocks; and
- immediate submit/cancel dispatch through the canonical execution gateway.

It does not classify investment language, choose roles, modify native spawn or
follow-up inputs, decide whether to delegate or wait, validate generic shell
syntax, constrain ordinary public retrieval, reimplement Codex workdir policy,
or parse calculation commands. Native permission profiles and the relevant
launcher own those behaviors.

## Durable Research And Effects

`begin_analysis_run` stores lightweight run identity, request hash/size, and
sealed context provenance without storing a team or plan. Authenticated
research writes derive producer identity, content hash, and consumed lineage.
A saved synthesis uses accepted run-local inputs.

Natural language, skills, and child agents never create execution authority.
Final effects still require the canonical ticket, policy, risk, approval,
idempotency, connection, confirmation, and audit path. The Build/Brain/Strategy
proof bridge grants only the matching lifecycle service call; it does not grant
filesystem permission, secret access, broker authority, or an order effect.

## Validation

Validate the combined contract, not Python alone:

- generate a disposable development workspace and run `./tcx doctor`;
- inspect effective prompts, skills, role profiles, permissions, hooks, and MCP
  exposure;
- test secret, service-owned state, lifecycle proof, and order boundaries;
- run a native Codex smoke for direct fast path, optional delegation, child
  follow-up, source provenance, and accepted synthesis; and
- compare observable tool calls, context size, progress cadence, artifacts, and
  answer quality rather than private chain of thought.

The complete matrix is in [Validation And Test Plan](validation-and-test-plan.md).
