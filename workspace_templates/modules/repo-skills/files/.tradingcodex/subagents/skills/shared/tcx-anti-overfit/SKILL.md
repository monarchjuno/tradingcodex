---
name: tcx-anti-overfit
description: "Review backtests, signals, and model-performance claims for leakage, data snooping, costs, capacity, and live friction."
---

# Anti-Overfit Validation

Use this procedure when a workflow reviews a backtest, signal, model result,
technical rule, or paper-alpha claim.

Required output shape:

- look-ahead leakage
- survivorship bias
- data snooping and multiple testing
- walk-forward or out-of-sample coverage
- transaction costs, slippage, borrow, funding, and taxes where relevant
- liquidity and capacity constraints
- regime sensitivity
- signal decay
- paper alpha versus live implementation friction

Quality floor:

- Treat validation as review, not strategy creation.
- Before comparing candidates, freeze the hypothesis, selection rule, parameter
  trial budget, chronological train/validation/holdout windows, and costs. Log
  every generated, tried, revised, or discarded variant rather than only the
  winner.
- Treat a holdout as single-use. Repeated inspection or tuning on its result
  turns it into training feedback and requires a new untouched holdout or
  live-forward period.
- In the `data_snooping` result, state the observed trial count or defensible
  effective count and the multiple-testing adjustment used. Use methods such as
  a reality check, Deflated Sharpe Ratio, or probability of backtest overfitting
  only when their assumptions and required inputs are actually supported; do
  not make one statistic a universal gate.
- Mark unsupported performance claims `not-decision-ready`, `revise`, or
  `blocked`.
- Do not imply execution readiness from a chart, backtest, or signal alone.
- Separate empirical performance from economic plausibility. A high in-sample
  Sharpe ratio alone is not evidence of a robust effect.

When these checks are in scope, set `anti_overfit_required: true` and write an
`anti_overfit_checks` object into artifact frontmatter. Include every key below
with `status` (`pass`, `fail`, or `not_applicable`), a non-empty `reason`, and
an `evidence_refs` list; `pass` and `fail` require at least one exact evidence
reference.

```yaml
anti_overfit_checks:
  leakage:
    status: pass
    reason: "State the observed check result."
    evidence_refs: ["exact-artifact-or-source-snapshot-id"]
```

Use that object shape for `leakage`, `survivorship_bias`, `data_snooping`,
`out_of_sample`, `walk_forward_consistency`, `monte_carlo_permutation`,
`bootstrap_sharpe_ci`, `cost_assumptions`, `capacity`, and `live_friction`.
Replace the example with observed status and exact evidence. Do not rely on
English body keywords to activate or prove validation; the structured fields
are language-neutral machine contracts.
