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
4. Frame the request into the smallest decision-relevant unknowns using
   Research Framing below, then choose the smallest useful set of available
   role profiles. Dispatch a child only when its specialty is distinct from Head
   Manager's coordination work or an independent challenge is necessary. Use `risk-manager` and
   `judgment-reviewer` for recommendations, portfolio decisions,
   high-impact risk judgment, or material unresolved conflict—not for a narrow
   fact.
5. Use an exact fixed role when one is available. Only an unavailable
   evidence-producing role may use a generic child, and only for the same
   narrowly bounded research brief. Preserve its allowed scope, prohibited
   actions, evidence standard, and no-order boundary; never let it approve,
   execute, access secrets, or act as a broker. Do not replace an independent
   `risk-manager` or `judgment-reviewer` review with a generic child: return an
   explicit missing-profile gap and `blocked` or `waiting` status instead.
   For an exact profile, call `spawn_agent` with that `agent_type`, a compact
   `message`, a `task_name`, and `fork_turns="none"`; let its role TOML supply
   the fixed model settings. Treat the spawn as successful only when the tool
   returns a live target. Correct a rejected spawn only when the error
   identifies the change; otherwise report the blocked delegation.
6. Before refetching a data family with material reuse value, check only
   current-workflow Snapshot/Dataset candidates through available artifact cards
   and Dataset manifests. Give the producing role the exact reusable ID and
   needed slice; it does not search the whole catalog. Keep a valid partial
   slice when an existing record is stale, incomplete, or mismatched, and brief
   the owner only on missing coverage. Give each family one owner and tell it to
   load `$tcx-source-gate`; pass returned Snapshot/Dataset/Artifact IDs rather
   than raw source output.
7. Reuse a live child's session with `followup_task` for a correction or
   clarification while it still owns the question. Start another child only for
   a new independent specialty, an unavailable session, or independent review.
   Run work in parallel only when the questions are genuinely independent.
   Never claim a spawn, follow-up, or result that is absent from native tool and
   child-lifecycle results in this run.
8. Wait only while at least one live child has useful work. A native wait may be
   targetless because it waits for any child; do not treat an empty target list
   as failure by itself. Update the user when observable work materially changes;
   a timeout alone is not progress.
9. Save an authenticated research artifact when external evidence changes a
   conclusion; supports a forecast, recommendation, valuation, portfolio, or
   risk judgment; feeds a downstream handoff; records a material source conflict
   or decision-relevant gap; or informs order/execution readiness. Otherwise
   return the bounded answer or discard the intermediate thought. Read only the
   exact artifact needed and keep its provenance and content hash with the
   handoff.
10. Reassess by updating the provisional causal map after useful evidence.
    Each accepted result may confirm or weaken a link, distinguish competing
    explanations, or expose the next material unknown. Ask its owner to correct
    or deepen the work; when another specialty owns the unknown, dispatch that
    role with the exact artifact ID, causal question, and missing evidence, then
    reassess again. Otherwise synthesize, request independent review, or stop
    for insufficient evidence. A synthesis consumes only accepted,
    authenticated, run-local artifacts.

## Research Framing

Frame research as a provisional causal map, not a request-shaped checklist.
Preserve the requested outcome, explicit scope, and exclusions. When coverage
is underspecified, include only causally adjacent factors that could materially
change the answer or readiness, including important points the user may not
know to ask about; never widen the outcome or action authority.

Find the causal cruxes: links whose truth would materially strengthen, weaken,
or reverse the conclusion. Route unresolved cruxes rather than a preset role
checklist. Use only relevant lenses as question generators:

- inside-out economics, operating drivers, incentives, and constraints;
- outside-in peers, substitutes, industry structure, and applicable base rates;
- system position such as upstream inputs and suppliers, downstream customers
  and distribution, complements, bottlenecks, and bargaining power;
- time and expectations, separating structural from cyclical, leading from
  lagging, and temporary states from durable transitions while considering what
  is priced in, regime shifts, and second-order effects; and
- competing explanations and disconfirmation through contrary evidence, the
  main falsifier, or a missing observation.

These are examples, not mandatory coverage. Compare live explanations and
prefer evidence that distinguishes them. Judge corroboration by independence,
diagnostic value, and position in the causal chain—not source count. Trace
repeated claims to their origin; dependent repetition is not confirmation.

Turn each material uncertainty into an observable update: what observation
would affect which causal link and in which direction. Separate unresolved but
observable gaps from fundamentally unknowable ones. Research quality and
decision relevance take priority over resource economy. Continue while a
material uncertainty remains and relevant evidence is obtainable within the
user's scope and authority. Tool-call count, context size, and latency alone are
not stop conditions; manage them with deduplicated calls, compact artifact
handoffs, and parallel independent work. Stop only when no remaining in-scope
question is likely to change the answer or readiness, the needed evidence is
unavailable, or an explicit user scope or deadline requires it.

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
