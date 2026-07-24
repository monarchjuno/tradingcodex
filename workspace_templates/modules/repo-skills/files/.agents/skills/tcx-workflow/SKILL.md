---
name: tcx-workflow
description: Coordinate Codex-native TradingCodex investment research, valuation, forecasts, recommendations, portfolio or risk review, order preparation, approval review, and execution status while preserving evidence, policy, approval, and execution boundaries.
---

# TCX Workflow

Use the smallest safe path. Coordinate rather than impersonate specialists.

When a named tool is deferred, use one names-only exact-name lookup, then
inspect the selected exact name's schema once. Never print full tool records,
scan descriptions, or repeat a schema lookup.

## Fast Path

Answer a narrow fact, definition, or status from a trusted read-only source.
Do not start a run, artifact, or child to restate it. State unsupported gaps.

Start a workflow only for research, valuation, forecasts, recommendations,
portfolio or risk judgment, order preparation, approval review, or a question
that needs fresh evidence or more than one distinct expertise.

## Workflow

1. Preserve the user's outcome, constraints, and exclusions. Ask only when a
   missing choice changes the result or a sensitive action.
2. For a workflow, call `begin_analysis_run` once. It seals provenance but
   creates no child. Never wait for it: either dispatch a chosen role through
   `spawn_agent` or stop.
3. Apply one explicitly selected Investment Brain or Strategy only as sealed
   context. Do not infer, blend, inspect, or change one during the run.
4. For fresh research, valuation, forecasts, recommendations, or material
   portfolio/risk judgment, read the
   [Research Framing playbook](playbooks/research-framing.md), find the smallest
   decision-relevant unknowns, and choose the smallest useful role set. Skip the
   playbook for a narrow fact or recorded order, approval, or execution status.
   Dispatch only for distinct expertise or independent challenge. Use
   `risk-manager` where its separate risk authority materially improves the
   result. Use `judgment-reviewer` when a recommendation, portfolio/risk
   decision, material conflict, or high-consequence uncertainty benefits from
   adversarial adjudication; do not force it onto narrow facts or requests
   without a meaningful competing case.
5. Use an exact fixed role when one is available. Only an unavailable
   evidence-producing role may use a generic child for the same bounded brief
   and no-order boundary; it cannot approve, execute, access secrets, or act as
   a broker. Do not replace an independent
   `risk-manager` or `judgment-reviewer` review with a generic child: return an
   explicit profile gap as `blocked` or `waiting`. Call `spawn_agent` for an
   exact profile with its `agent_type`, a compact `message`, `task_name`, and
   `fork_turns="none"`; let its role TOML supply
   the fixed model settings. Success requires a live target; correct a rejected
   spawn only from its error.
6. Before refetching a reusable data family, check only current-workflow Snapshot/Dataset candidates.
   Give it one owner and brief every child with
   that ownership, exact reusable IDs, and the missing slice. Require reuse of
   supplied IDs; no child recollects another owner's complete family. Tell the
   owner to load `$tcx-source-gate` and return IDs, not raw output.
7. Reuse a live child's session with `followup_task` for work it still owns.
   Start another only for a new specialty, unavailable session, or independent
   review; parallelize independent questions. Never claim activity absent from
   native tool and child-lifecycle results in this run.
8. Call `wait` only after `spawn_agent` or `followup_task` has returned a live
   target in this run and only while that child has useful work. Otherwise do
   not call it. Update only on observable material change; a timeout is not
   progress.
9. Save an authenticated role artifact when evidence changes a conclusion,
   supports a consequential judgment or handoff, records a material conflict or
   gap, or has future reuse or audit value. Do not save a narrow fact, status,
   temporary exploration, or restatement of one existing artifact.
10. After each accepted result, apply the Feedback Loop below. Synthesize only
    accepted, authenticated, run-local artifacts.

## Feedback Loop

1. Compare the result with its brief and causal map; keep only material gaps.
2. Same owner: use one bounded follow-up via `followup_task`. Label its target
   owner Artifact ID (append/revise) separately from triggering cross-role
   Artifact IDs (consumed evidence), then give the exact delta. If the target
   should exist but its ID is missing, return `waiting`; say `create new
   artifact` only when that is intended.
3. New specialty: dispatch its role with the Artifact IDs as triggers, no target
   ID and an explicit `create new artifact` instruction, plus the causal question
   and missing evidence. Use independent review for material conflict.
4. Recheck the changed link. Continue only while an obtainable gap could change
   the result; otherwise synthesize or preserve its state.

Illustrative ownership examples, not a mandatory sequence:

- **Same owner:** Macro evidence finds material FX exposure missing from an
  exporter's fundamental work. Follow up with its Artifact ID and the gap.
- **New specialty:** Fundamental work finds an unassigned commodity dependency.
  Add `macro-analyst`; do not make the current owner emulate it.
- **Independent review:** Accepted artifacts materially disagree. Add
  `judgment-reviewer` or `risk-manager`.

## Evidence And Decisions

- Treat planning reconnaissance and search snippets as leads, not conclusions.
  Material investment claims require current, attributable evidence.
- Keep provider, as-of time, coverage, warnings, conflicts, uncertainty, and
  missing evidence visible. Distinguish sourced facts, analysis, and scenario
  assumptions in natural prose where that distinction matters.
- For a final synthesis, recommendation, portfolio/risk result, or other
  high-consequence judgment, use a short natural structure that separately
  identifies verified facts and sources, analysis and implications, key
  assumptions, and uncertainty, gaps, disagreements, or blocked actions. Do
  not require per-sentence tags or impose this structure on narrow factual
  answers or intermediate role output.
- For a forecast, recommendation, valuation, portfolio decision, or other
  high-consequence judgment, add only what the structure needs: a relevant base
  rate or comparison (or the gap), base/upside/downside or appropriate
  alternatives, key assumptions and the main falsifier, contrary evidence or
  material disagreement, and an update trigger plus current action/readiness
  limit.
- Preserve an independent current view before Decision Memory changes a new
  judgment. Memory is evidence, not authority.
- Brief `judgment-reviewer` with the exact decision question, downstream
  consequence, accepted Artifact IDs, and a compact judgment frame derived
  from the user mandate, applicable Investor Context, Brain questions or
  falsifiers, and Strategy rules. Ask it to test application under that frame,
  not replace the frame.
- Persist at most one synthesis identity per run, with later corrections as
  appended versions. Save it only when multiple artifacts are actually
  integrated; a recommendation, valuation, forecast, portfolio/risk judgment,
  durable abstention, Memory delta, future Decision/Postmortem baseline, or
  user-requested durable report needs it. Do not save simple facts, status,
  single-artifact restatements, or follow-ups with no new judgment.
- Synthesis is a point-in-time integrated judgment, not a role-by-role digest.
  Directly answer the question and connect 3–7 load-bearing claims from evidence
  to implication to judgment. Preserve conflicts, strongest contrary evidence,
  uncertainty, update triggers, invalidation, readiness, possible next steps,
  and blocked actions. Add specialized modules only when applicable; do not
  force headings or prose tags.
- After an accepted decision-grade synthesis with future evaluation value,
  autonomously call `record_judgment_snapshot`. Do not freeze factual or
  screening synthesis. A JudgmentSnapshot is evidence-only, not user adoption
  or execution permission.
- The final chat answer must stand alone. Give the conclusion, decisive evidence
  and implications, contrary evidence and uncertainty, both readiness axes, and
  useful next actions at suitable detail. Never narrate the report's table of
  contents. A full-report request gets an executive-report-quality chat answer;
  receipts, hashes, and complete provenance remain in the artifact.
- After an authenticated Head Manager `synthesis_report` receipt, link its
  saved report in the final reply. Resolve the returned `path` against the
  current workspace root and use its service-returned path:
  `[Open final research report](/absolute/path/to/report.md)`. Do not invent,
  relativize, use `file://`, or re-export the canonical report.

## Boundaries

- Skills and roles are procedures, not authority; user tools are unmanaged.
- Analysis cannot create policy, approval, broker, or execution authority.
  Final effects remain behind canonical service policy, approval, idempotency,
  connection, and audit gates.
- Do not create a server-owned lane, team, DAG, or task queue. Head Manager owns
  dynamic judgment; services own durable enforcement and provenance.
