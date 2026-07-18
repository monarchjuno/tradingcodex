# Codex-Native Orchestration

Status: v1 contract

TradingCodex uses Codex itself as the investment research orchestrator. Head
Manager interprets the user's original language, answers narrow trusted facts
directly, and uses the smallest useful role set when fresh research or distinct
expertise is needed. It decides whether to follow up, add a role, challenge a
conflict, stop, answer directly, or synthesize.

```text
User
  -> Head Manager
     -> begin_analysis_run (identity, request hash, sealed Brain/Strategy/Context provenance)
     -> optional one explicit Investment Brain inquiry overlay
     -> optional role-profile or bounded generic children
     -> optional authenticated research artifacts
     -> dynamic next-role judgment
     -> run-local synthesis artifact
```

## Why There Is No Research DAG

Investment research changes as evidence arrives. A fixed server DAG, language
keyword router, candidate-role ceiling, or Django supervisor loop duplicates
the model's reasoning and makes multilingual or novel requests brittle.
TradingCodex therefore keeps analytical orchestration in the Head Manager
skill and prompt, where Codex can use the entire request and returned artifacts.

The service does not classify investment intent, own a semantic lane, select a
team, compile stages, issue task IDs, or decide terminal workflow actions.

## Durable Run Contract

`begin_analysis_run` creates:

```text
.tradingcodex/mainagent/runs/<analysis-run-id>/run.json
```

The record contains run identity, timestamps, request hash/byte count, sealed
Strategy provenance, sealed Investor Context provenance, optional
`investment_brain_binding`, and authority labels. A Brain binding records the
validated plugin `brain_id`, version, content digest, source metadata, manifest
and source paths, and projected skill path. It does not store the raw request,
a selected team, a plan, or a task queue.

## Investment Brain Context

The native task may select at most one Primary Brain through one exact
`$investment-brain-*` skill invocation. TradingCodex resolves it through the
workspace plugin registry and seals the active validated binding before
analysis. With no Brain invocation, use the pristine baseline. Plain-language resemblance
does not activate a Brain; multiple, inactive, invalid, unresolved, or unloaded
selection fails closed as `waiting_for_investment_brain`.

An Investment Brain is a high-freedom, platform-neutral inquiry and
interpretation overlay for Head Manager. It may prioritize hypotheses, causal
questions, scenarios, falsifiers, and abstention. It may not name roles,
dispatch children, prescribe task order, call tools, select models or sandbox,
set artifact paths, retrieve or modify Decision Memory, or grant policy,
approval, broker, order, or execution authority. Head Manager translates its
domain questions into dynamic role-owned assignments. A different Brain or
Strategy requires a new analysis run.

Context is typed rather than flat. Core owns safety and run integrity; the user
mandate owns scope and prohibitions; Investor Context limits suitability;
Strategy owns explicit decision rules; Brain guides inquiry and interpretation;
methods own bounded procedures; authenticated evidence controls facts; and
Decision Memory contributes non-authoritative historical cases. Evidence may
falsify a Brain or Strategy assumption. Strategy governs a decision-rule
conflict with Brain, while Investor Context remains blocking when suitability
conflicts with Strategy.

## Delegation Contract

MultiAgent V2 exposes the generated role profiles with a depth-one boundary.
Head Manager prefers an exact profile when its specialty is useful and passes a
compact role-owned brief rather than the full root history. Role profiles inherit
the user's Codex model and reasoning defaults while retaining their projected
web posture, skills, tools, and MCP principal.

For an exact profile, Head Manager calls native `spawn_agent` with the exact
`agent_type`, a compact `message`, a `task_name`, and `fork_turns="none"`. It
does not pass `model` or `reasoning_effort`. A spawn is real only when the tool
returns a live target. A rejected spawn may receive at most one correction
explicitly named by the error; otherwise delegation remains blocked.

Use `followup_task` when a live child still owns a correction or clarification.
Start another child for a new specialty, an unavailable session, or independent
review. If an exact profile is unavailable, a generic child may take the same
bounded research-only brief, evidence standard, and no-secret/no-order
prohibitions. It cannot approve, execute, access a broker, or emulate Head
Manager. Children never delegate recursively. Head Manager waits or follows up
only on a returned live target and reports lifecycle events only when their
native tool calls completed in the current run.

When a Brain applies, the assignment contains the question Head Manager derived
from it, not the Brain body or a delegation of Brain authority. Brain content is
never copied into fixed-role configuration.

## Artifact And Lineage Contract

Decision-, reuse-, audit-, or handoff-relevant roles store their own output
through `create_research_artifact`; a narrow bounded answer need not create one.
Authenticated service code derives the producer identity and content hash,
verifies the analysis run, and resolves exact run-local `input_artifact_ids`
into immutable input hashes. Head Manager reads exact returned artifact IDs
through `get_research_artifact` and may create `synthesis_report` only with at
least one verified run-local input.

For a Brain-bound run, the service also derives
`investment_brain_id`, `investment_brain_version`, and
`investment_brain_content_digest` into each accepted artifact. Callers cannot
forge or replace this lineage. Synthesis explains the Brain's material effect
on inquiry or interpretation and preserves conflicts with Strategy, evidence,
or Decision Memory.

When similar Decision Memory may affect a new judgment, Head Manager first
forms and preserves an independent current-evidence view, then retrieves memory
and records the explicit decision delta. Direct memory listing or replay does
not require this artificial blind step.

Handoff states (`accepted`, `revise`, `blocked`, `waiting`) describe producer
readiness. Head Manager uses them as evidence, not as a Django state machine.
Artifact quality checks, source snapshots, decision-quality fields, forecast
contracts, and independent judgment review remain available.

## Django Boundary

Django remains the authority for data and actions that require deterministic
enforcement:

- MCP principal and capability checks
- research/source persistence and lineage validation
- policy and restricted-list decisions
- portfolio/account state
- order tickets and checks
- approval receipts
- idempotency and broker connections
- submission, cancellation, reconciliation, and audit

Analysis runs and research artifacts are workspace-file-native. There is no
`WorkflowRun`/`ArtifactRef` Django mirror.

## Workspace Viewer

The browser is a read-only workspace viewer and never starts Head Manager,
dispatches roles, or selects an Investment Brain. All orchestration and exact
Brain invocation occur in native Codex tasks.

## Validation

A passing native smoke proves:

1. hooks provide compact context and proof gates without semantic classification
   or generic shell-policy duplication;
2. Head Manager uses the direct fast path or calls `begin_analysis_run` only for
   fresh research and decision work;
3. Korean and English requests both route by agent understanding;
4. Head Manager and children inherit native Codex model defaults;
5. exact profiles spawn with compact fresh context, child follow-up targets a
   returned live child, and lifecycle claims match completed native tool calls;
6. bounded generic fallback preserves role and safety boundaries;
7. no Brain preserves baseline behavior, one exact Brain changes only inquiry
   and interpretation, and multiple or unresolved Brains fail closed;
8. artifacts are principal-bound and preserve run-local Brain and input lineage;
9. Head Manager dynamically revises from artifacts without old plan tools;
10. memory-influenced judgment is blind-first and reports its delta; and
11. order/approval/execution gates remain service-owned.
