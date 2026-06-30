# Decision Quality Spine

The spine is a cross-lane quality contract, not a workflow lane.

Apply it inside the selected lane and selected team only:

1. Preserve explicit user constraints and negations.
2. Use the selected universe, lane, blocked actions, and quality flags from hook context or the persisted prompt gate.
3. Require artifact paths, `reader_summary`, `context_summary`, `handoff_state`, source/as-of posture, `evidence_grade`, `decision_readiness`, `confidence`, `missing_evidence`, `next_recipient`, and `blocked_actions`.
4. For thesis or valuation scope, require scenario cases, contrary evidence, update triggers, invalidation conditions, and unresolved conflicts.
5. For prediction, valuation implication, scenario probability, or decision support, require forecast permission fields and either a valid forecast record or `forecast_block_reason`.
6. For backtest, signal, or model-performance scope, require anti-overfit validation.
7. For recommendation, sizing, or portfolio-fit scope, keep investor-profile gaps visible until answered.
8. Synthesize only accepted artifacts and return `waiting`, `revise`, or `blocked` when support is weak.

Artifact handoff states are `accepted`, `revise`, `blocked`, and `waiting`.
They are not terminal workflow actions. Terminal workflow actions are
`synthesize`, `blocked`, `waiting`, or `lane_escalation_proposal`.

Artifacts may include `follow_up_requests` with `trigger`, `suggested_role`,
`question`, `reason`, `materiality`, source artifact provenance, advisory
`suggested_consent_posture`, and blocked actions. Subagents propose these
requests only. Head-manager recalculates lane scope and consent from
`allowed_followup_team`, `escalation_team`, and `loop_policy` before creating
any delta follow-up brief.

Forecast ledger records live under `trading/forecasts/*.jsonl` and are
append-only. Do not create them without accepted role artifacts.
