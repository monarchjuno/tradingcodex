---
name: forecasting-discipline
description: "Require horizon-bound, evidence-aware, updateable forecast fields when prediction, valuation implication, or decision support is in scope."
---

# Forecasting Discipline

Use this procedure when a workflow asks for prediction, scenario probability,
valuation implication, or decision support.

Forecast fields are an agentic judgment contract, not a trading model, feature
store, autonomous signal, or execution trigger. Use them to make role judgment
reviewable, horizon-bound, falsifiable, updateable, and suitable for
postmortem.

Required output shape:

- `forecast_required`
- `forecast_allowed`
- `forecast_block_reason` when probability should not be produced
- `forecast_target`
- `forecast_horizon`
- `probability` or `probability_range` when allowed
- `base_rate` or missing-base-rate note
- `evidence_ids`
- `contrary_evidence`
- `resolution_source`
- `review_date`
- `update_triggers`
- `invalidation_conditions`

Quality floor:

- Bound each forecast to a resolvable target and horizon.
- Separate factual data, model output, assumption, and judgment.
- Use a probability range when precision is weak.
- Treat forecast probability as role judgment that needs evidence, contrary
  evidence, review date, and invalidation conditions.
- If `probability` and `probability_range` both appear, keep the point value
  inside the range.
- If evidence is weak, use `forecast_allowed: false`,
  `not-decision-ready`, `revise`, or `blocked` with a clear block reason.
- When forecast scope is negated, provide qualitative scenarios only; do not
  create probability fields or forecast ledger records.

Write scoreable records under `trading/forecasts/` only after accepted
evidence supports the forecast.
