---
name: tcx-workflow
description: Coordinate Codex-native TradingCodex investment research, valuation, forecasts, recommendations, portfolio or risk review, order preparation, approval review, and execution status while preserving evidence, policy, approval, and execution boundaries.
---

# TCX Workflow

Use the smallest path that answers the user safely. Coordinate specialist work;
do not impersonate a specialist when distinct expertise is actually needed.

## Fast Path

Answer a narrow factual question, definition, or simple recorded-status request
directly from the smallest trusted read-only source. Do not start an analysis
run, create an artifact, or spawn a child merely to restate a status or fact.
State the evidence gap when the available source cannot support the claim.

Start a workflow only for research, valuation, forecasts, recommendations,
portfolio or risk judgment, order preparation, approval review, or a question
that needs fresh evidence or more than one distinct expertise.

## Workflow

1. Preserve the user's outcome, explicit constraints, and exclusions. Ask only
   when a missing choice materially changes the result or a sensitive action.
2. For a workflow, call `begin_analysis_run` once. It seals provenance; it does
   not choose a team or create a server-side plan.
3. Apply one explicitly selected Investment Brain or Strategy only as sealed
   context. Do not infer, blend, inspect, or change one during the run.
4. Choose the smallest useful set of available role profiles. Dispatch a child
   only when its specialty is distinct from Head Manager's coordination work or
   an independent challenge is necessary. Use `risk-manager` and
   `judgment-reviewer` for recommendations, portfolio decisions,
   high-impact risk judgment, or material unresolved conflict—not for a narrow
   fact.
5. Use an exact fixed role when one is available. Otherwise a generic child may
   perform the same narrowly bounded research brief. Preserve its allowed scope,
   prohibited actions, evidence standard, and no-order boundary; never let it
   approve, execute, access secrets, or act as a broker.
   For an exact profile, call `spawn_agent` with that `agent_type`, a compact
   `message`, a `task_name`, and `fork_turns="none"`; omit `model` and
   `reasoning_effort` so native defaults apply. Treat the spawn as successful
   only when the tool returns a live target. If it is rejected, make at most one
   correction named by the error; otherwise report the blocked delegation.
6. Give each external data family one evidence-producing owner and tell that
   role to load `$tcx-source-gate`. The role should reuse adequate existing
   Snapshot/Dataset evidence, try one relevant enabled user Skill, Plugin, or
   MCP capability, then optional direct OpenBB, official-source-first native
   research, another credible source, and finally return an explicit gap.
   Retain a partial result and fetch only what is missing. Do not repeat an
   unchanged call after success or a terminal failure. Pass returned
   Snapshot/Dataset/Artifact IDs rather than raw source output.
7. Reuse a live child's session with `followup_task` for a correction or
   clarification while it still owns the question. Start another child only for
   a new independent specialty, an unavailable session, or independent review.
   Run work in parallel only when the questions are genuinely independent. Do
   not wait or follow up without a returned live target, and never claim a spawn,
   follow-up, or result that is absent from completed tool calls in this run.
8. Wait only on returned live targets while useful work remains. Update the user
   after a material change or after roughly a minute without a visible update;
   a timeout by itself is not progress. Report only observable status, evidence
   gaps, and next action.
9. Save an authenticated research artifact when the result will support a
   decision, reuse, audit, or downstream handoff. Otherwise return the bounded
   answer directly. Read only the exact artifact needed and keep its provenance
   and content hash with the handoff.
10. Reassess after useful evidence arrives: synthesize, ask the owner to correct
    its work, add a distinct perspective, request independent review, or stop
    for insufficient evidence. A synthesis consumes only accepted,
    authenticated, run-local artifacts.

## Evidence And Decisions

- Treat planning reconnaissance and search snippets as leads, not conclusions.
  Material investment claims require current, attributable evidence.
- Keep provider, as-of time, coverage, warnings, conflicts, uncertainty, and
  missing evidence visible. Tag material artifact claims `[factual]`,
  `[inference]`, or `[assumption]`.
- Preserve an independent current view before Decision Memory changes a new
  judgment. Memory is evidence, not authority.
- Persist a synthesis only when a workflow produced decision-relevant evidence.
  Preserve disagreements, suitability gaps, and blocked actions.

## Boundaries

- Skills and role profiles are procedures, not authority. User-installed tools
  are not TradingCodex-managed integrations.
- Analysis cannot create policy, approval, broker, or execution authority.
  Final effects remain behind canonical service policy, approval, idempotency,
  connection, and audit gates.
- Do not create a server-owned lane, team, DAG, or task queue. Head Manager owns
  dynamic judgment; services own durable enforcement and provenance.
