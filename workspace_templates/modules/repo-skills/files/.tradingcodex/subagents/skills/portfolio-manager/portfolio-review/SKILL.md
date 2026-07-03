---
name: portfolio-review
description: "Evaluate portfolio fit before an order draft, including exposure, sizing context, concentration, liquidity, holding period, opportunity cost, and draft readiness."
---

# Portfolio Review

Use this skill to evaluate portfolio fit before an order ticket is drafted or before an investment view is escalated into risk/sizing work.

Codex-native state access:

- Prefer TradingCodex MCP read/status tools such as `list_broker_connections`,
  `get_portfolio_snapshot`, and `list_reconciliation_runs` over manually
  reconstructing portfolio state from chat context.
- Use `sync_broker_account` only when the user or workflow asks for a fresh
  read-only sync and the broker connection is already reviewed for read scope.
- Treat reconciliation warnings or stale sync state as readiness constraints,
  not as data to smooth over.

Universe method:

- Identify asset universe, instrument, account/context, benchmark, existing exposure, and whether the installed harness supports the instrument.
- Public equity and ETF/index reviews should consider active weight, sector/factor exposure, liquidity, concentration, benchmark comparison, rolling contribution, timing attribution, and opportunity cost when data exists.
- Use Brinson-style allocation/selection language, factor attribution, or timing attribution only when holdings, benchmark, and return data are explicit; otherwise name the missing inputs.
- Crypto, macro, FX, rates, commodities, options, and credit-linked exposures require instrument-specific implementation inputs; mark missing support as `not-decision-ready` rather than sizing from thin context.
- Separate portfolio fit from personal investment advice.

Expected output:

- Universe, instrument, and portfolio context
- Existing exposure
- Position size context
- Concentration risk
- Liquidity and holding period fit
- Opportunity cost
- Performance attribution or benchmark-relative contribution when supportable
- Portfolio recommendation
- Draft readiness: ready, revise, or no-order
- Source/as-of posture and missing implementation inputs

Decision quality fields when applicable:

- `evidence_grade`, `source_freshness`, `source_quality`
- `decision_readiness`, `confidence`, `investor_profile_gaps`
- `forecast_required`, `forecast_allowed`, `forecast_block_reason`
- `contrary_evidence`, `update_triggers`, `invalidation_conditions`

Quality floor:

- Apply the shared artifact quality floor.
- Tag material narrative claims as `[factual]`, `[inference]`, or `[assumption]`.
- Tie sizing and fit to the user's stated portfolio context.
- Name concentration, correlation, liquidity, and opportunity-cost assumptions.
- State the binding constraint and whether the output is `screen-grade`, `ready-for-portfolio-risk`, `ready-for-draft`, or `not-decision-ready`.
- Do not fabricate positions, cash, costs, capacity, or user constraints.
- State whether the next step is review-only, ready for risk review, or ready for draft.

Write outputs under `trading/reports/portfolio/`.
