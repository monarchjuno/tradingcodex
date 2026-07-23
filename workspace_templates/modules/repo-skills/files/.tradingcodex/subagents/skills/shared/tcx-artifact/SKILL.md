---
name: tcx-artifact
description: "Prepare, persist, and repair compact TradingCodex v2 research artifacts and forecast records for authenticated run-bound handoffs."
---

# Persist An Artifact

Use the authoritative MCP schema for `create_research_artifact`,
`append_research_artifact_version`, or `issue_forecast`. Never approximate a
tool name or write canonical records directly.

## Quality Floor

- Answer the assigned question and distinguish facts, analysis, and assumptions
  in natural prose where material.
- Preserve source/as-of posture, conflicts, uncertainty, confidence and its
  basis, gaps, blocked actions, and handoff state.
- Use only real service IDs. The service derives hashes, versions, role,
  identity where applicable, paths, receipt provenance, and recorded time.
- Use `accepted` only when ready for Head Manager review. Acceptance is
  completeness, not action authority.

## V2 Write Contract

1. Record source snapshots before citing them and use only returned IDs.
2. Put type, title, universe, optional symbol, Markdown, and one non-empty
   `summary` at top level.
3. Put `handoff`, `evidence_readiness`, `action_readiness`, `confidence`,
   `confidence_basis`, missing evidence, and blocked actions in `status`.
4. Put the run, timezone-qualified cutoff, and every consumed Artifact,
   Snapshot, Dataset, and Calculation ID in `lineage`. The receipt owns hashes
   and exact input versions.
5. Use only `factual`, `screen`, `decision-grade`, or `insufficient` evidence
   readiness and `research-only`, `portfolio-review`, `draft-eligible`, or
   `blocked` action readiness. Portfolio review and draft eligibility require
   decision-grade evidence.
6. Include only applicable `requirements`: `decision_quality`, `forecast`,
   `investor_context`, or `anti_overfit`. Inherited requirements cannot be
   removed. Omit empty optional blocks.
7. Put detailed scenarios, contrary evidence, trust explanation, and domain
   analysis in Markdown. Actual forecast probability, base rate, horizon, and
   resolution belong only in the Forecast ledger. Improvements belong only in
   Judgment Review or Postmortem.
8. On success, stop and return `ARTIFACT <artifact-id> <path> <handoff>` using
   values from the response. Never reconstruct them.

For append, keep the target `artifact_id` separate from consumed input IDs. A
Head Manager synthesis never supplies its own ID or path; the service derives
one identity per run and append reuses it.

When decision quality is required, bind the accepted independent reviewer in
`decision_quality.review_artifact_ids`; keep update triggers and invalidation
conditions compact. Detailed thesis lifecycle belongs in the reviewer artifact
or Validation Card.

## Stop Unchanged Tool Loops

Treat `stored`, `updated`, `existing`, `reused`, and `prepared` as completion.
Permit the initial write and one evidence-backed corrected retry. After another
deterministic failure, stop as `waiting` with the bounded error and owner.

## Forecasts

Issue a scoreable forecast only after its supporting artifact is accepted and
bind the required independent base-rate snapshot. If the ledger contract cannot
be met, store `forecast: {posture: blocked, block_reason: ...}` rather than an
invented probability.
