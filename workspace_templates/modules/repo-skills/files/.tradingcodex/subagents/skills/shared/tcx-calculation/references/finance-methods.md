# Financial Method Checks

Use these checks in addition to the role-specific method skill. Prefer the
simplest library that preserves the required semantics.

## Choose numeric types and libraries

- Use `decimal.Decimal` for exact contractual cash amounts, prices, and
  deterministic scalar formulas when binary floating-point drift is material.
- Use NumPy/Pandas for aligned arrays and tabular time series. Normalize
  timestamps and labels before arithmetic; do not rely on accidental row order.
- Use SciPy for optimization or numerical solving and report the solver,
  constraints, tolerances, convergence flag, iterations, and residuals.
- Use Statsmodels when inferential diagnostics matter. Report specification,
  sample construction, coefficient uncertainty, residual diagnostics, and
  robust covariance choice where applicable.
- Use `numpy-financial` for standard financial functions only after confirming
  cash-flow timing and sign conventions. Do not treat IRR as unique when
  non-conventional cash flows can yield multiple or no solutions.

## Returns and risk

- State whether returns are simple or logarithmic and whether prices are raw,
  split-adjusted, or total-return adjusted.
- Align calendars, timezone, and observation frequency before combining assets.
- State the annualization factor and risk-free-rate convention. Do not
  annualize a statistic whose sampling assumptions are unsupported.
- Define drawdown from a wealth index and report the peak, trough, recovery
  posture, and observation window.
- Distinguish sample from population estimators. Report observation count,
  missing-value treatment, and overlap introduced by rolling windows.

## Valuation and cash-flow math

- Place every cash flow on an explicit timeline. State whether discounting is
  beginning-, mid-, or end-period and keep nominal/real rates consistent with
  nominal/real cash flows.
- Match currency, inflation, tax, share count, net debt, and enterprise/equity
  value bridges. State dilution and non-operating asset treatment.
- Require terminal growth below the compatible long-run discount rate for a
  perpetuity-growth model. Report terminal-value share and sensitivity.
- For IRR/XIRR, record dated cash flows and signs. Prefer NPV profiles or
  multiple-root diagnostics when cash-flow signs change more than once.
- Label scenario probabilities as assumptions unless independently supported;
  do not hide scenario weights inside a point estimate.

## Regression and statistical estimation

- Define the dependent variable, predictors, transformations, lags, and
  sampling window before fitting.
- Prevent look-ahead leakage and preserve a chronological holdout where model
  selection or prediction is involved.
- Report coefficient estimates with uncertainty, sample size, fit statistic,
  residual or autocorrelation checks, and economically relevant effect size.
- Use heteroskedasticity/autocorrelation-robust covariance only with a stated
  rationale and parameters; do not present corrected standard errors as a cure
  for a misspecified model.
- Treat p-values as diagnostics, not investment conclusions, and apply the
  assigned anti-overfit procedure when multiple variants were tried.

## Optimization, portfolio, and scenarios

- Freeze objective, constraints, bounds, covariance/return inputs, and fallback
  behavior before solving. Validate feasibility independently.
- Report gross/net exposure, concentration, turnover, liquidity, costs, and
  binding constraints. Stress input estimation error; do not rely on one
  optimizer solution as a robust allocation.
- Keep central-ledger position and account data private. Record only their
  service-provided snapshot identifiers and content hashes in Calculation
  provenance.
- For simulation, set and record the random seed, generator, distributional
  assumptions, path count, horizon, and relevant tail uncertainty.
