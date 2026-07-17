---
name: tcx-artifact
description: "Prepare, persist, and repair TradingCodex research artifacts and forecast records when a role must hand off run-bound evidence through MCP tools."
---

# Artifact Persistence

Use this procedure whenever the role must call `create_research_artifact`,
`append_research_artifact_version`, or `issue_forecast`. The MCP schema is the
authoritative input contract. Search for and call the exact deferred tool; do
not approximate a tool name or write a ledger record directly.

## Persist the artifact first

1. Record source snapshots before citing them, and use the exact returned IDs.
2. Pass the assigned `workflow_run_id` and exact `input_artifact_ids` consumed.
   When a recorded calculation affects the conclusion, also pass its current
   workflow `calculation_run_id`; the service derives hashes and reuse origin.
3. Include a non-empty `readiness_label`, markdown, handoff state, evidence
   posture, and the role's bounded conclusion.
4. Keep optional gates honest. If forecasting or decision-quality review is
   outside the assignment, use `false` or omit the optional field; do not
   fabricate fields merely to satisfy a gate.
5. Apply every field correction returned by the service in one targeted
   resubmission. If the same contract error repeats, stop retrying and return a
   `waiting` handoff with the exact error.

`follow_up_requests[].required_inputs` is always an array of strings.
`probability_range` is one lower/upper range such as `[0.3, 0.4]` or `30-40%`.
Put multiple scenario-specific ranges in `scenario_cases`.

## Use the thesis lifecycle correctly

When `decision_quality_required` is true, include `thesis_lifecycle.state` and
the evidence required for that state:

- `exploring`: `{state: exploring}` is sufficient.
- `testing`: add `evidence_refs`, or cite top-level `source_snapshot_ids` or
  `evidence_ids`.
- `validated`: add `evidence_run_card` or `evidence_run_cards`,
  `validation_card` or `validation_cards`, and `reviewer_acceptance`.
- `rejected`: add `invalidation_note`.
- `monitoring`: add either `monitoring_artifact` or `review_cadence`.

`monitoring_artifact_or_cadence` is an error label, not an input field.

## Issue a ledger forecast only after acceptance

Artifact forecast metadata does not create a forecast ledger record. Call
`issue_forecast` only after the supporting artifact is accepted and only when
the assignment calls for a scoreable forecast.

- Use RFC 3339 timestamps with explicit timezones for `horizon` and
  `knowledge_cutoff`.
- Normally omit `issued_at`; the service records receipt time.
- Set `base_rate.cohort`, `base_rate.source_snapshot_id`, positive
  `base_rate.sample_size`, and `base_rate.selection_rule`.
- For binary targets add `base_rate.value`; for categorical targets add
  category-matched `base_rate.probabilities` summing to 1; for continuous
  targets add `base_rate.prediction`.
- Keep the base-rate snapshot at or before `knowledge_cutoff`.
- Match the forecast payload to `target_type`: binary probability,
  categorical probabilities summing to 1, or continuous prediction/interval.

If the evidence cannot support that contract, set `forecast_allowed: false`
on the artifact with a precise block reason and do not call `issue_forecast`.
