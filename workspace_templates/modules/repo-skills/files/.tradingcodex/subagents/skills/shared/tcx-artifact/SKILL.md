---
name: tcx-artifact
description: "Prepare, persist, and repair TradingCodex research artifacts and forecast records when a role must hand off run-bound evidence through MCP tools."
---

# Persist An Artifact

Use the authoritative MCP schema for `create_research_artifact`,
`append_research_artifact_version`, or `issue_forecast`. Never approximate a
tool name or write ledger state directly.

## Write once, then return the receipt

1. Record source snapshots before citing them and use only returned IDs.
2. Pass the assigned `workflow_run_id`, every exact consumed
   `input_artifact_id`, `dataset_id`, Data Acquisition Receipt ID, and any
   conclusion-relevant current-run Calculation ID. Include each receipt's
   returned Dataset and Source Snapshot IDs too. The service derives all four
   lineage hash maps.
3. Include Markdown, non-empty conservative `readiness_label`, source/as-of
   posture, `context_summary`, `reader_summary`, confidence, missing evidence,
   next action, blocked actions, and explicit handoff state.
4. Keep optional gates truthful. Do not fabricate fields merely to pass a
   validator. `accepted` means ready for Head Manager review.
5. On terminal success, stop and make the final handoff begin with one compact
   receipt line: `ARTIFACT <artifact_id> <path> <handoff_state>`. Copy all three
   values from the authenticated result; never reconstruct them.

When binding source snapshots, Datasets, or acquisition receipts, set the
timezone-qualified `knowledge_cutoff` at or after the maximum snapshot
`known_at`, Dataset `knowledge_cutoff`, and receipt `recorded_at`. Prefer that
exact maximum and never guess a future or date-only cutoff.

`follow_up_requests[].required_inputs` is an array of strings. Use one
lower/upper `probability_range`, such as `[0.3, 0.4]`; put multiple ranges in
`scenario_cases`. Allowed follow-up triggers are `coverage_gap`,
`freshness_gap`, `contradiction`, `material_driver`, `assumption_change`,
`method_gap`, `scope_boundary`, `forecast_gap`, and
`investor_context_gap`. A valuation sensitivity is an improvement type, not a
follow-up trigger.

## Stop unchanged tool loops

- Treat every documented terminal success, including `stored`, `updated`,
  `existing`, `reused`, and `prepared`, as completion. Never repeat the same
  canonical arguments hoping for another status.
- After a deterministic validation, permission, policy, or immutable-conflict
  error, make at most one correction directly supported by returned field
  guidance. Never submit the unchanged arguments again.
- If the same reason recurs, stop, lower readiness, and return `waiting` with
  the bounded error and owning next action.

## Set thesis state honestly

When decision quality is required:

- `exploring`: state only.
- `testing`: add evidence references or top-level snapshot/evidence IDs.
- `validated`: add evidence run card, validation card, and reviewer acceptance.
- `rejected`: add an invalidation note.
- `monitoring`: add a monitoring artifact or cadence.

## Issue forecasts only after acceptance

Call `issue_forecast` only when the assignment requires a scoreable forecast
and its supporting artifact is accepted. Use timezone-qualified RFC 3339
`horizon` and `knowledge_cutoff`; normally omit `issued_at`. Bind a base-rate
snapshot at or before the cutoff with cohort, sample size, and selection rule.
Match binary, categorical, or continuous payload fields to `target_type`. If
the evidence cannot support that contract, set `forecast_allowed: false` and a
precise block reason instead of issuing a forecast.
