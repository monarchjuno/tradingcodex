---
name: tcx-macro
description: "Analyze macro, rates, FX, commodities, policy, liquidity, and cross-asset transmission for investment workflows."
---

# Macro Analysis

Use this skill when an investment workflow depends on macro, rates, FX, commodities, policy shocks, liquidity, inflation, funding, central banks, sector read-throughs, or cross-asset transmission.

Universe method:

- Identify the macro universe and instrument/channel: rates, FX, commodities, inflation, labor, growth, liquidity, fiscal policy, central bank policy, sector/macro shock, or cross-asset overlay.
- Treat macro evidence as an input to thesis, valuation, risk, or portfolio fit.
- State data source, region, period, release date, as-of timestamp, and whether the series is revised, delayed, or preliminary.
- Do not pretend that macro research support implies futures, FX, commodity, options, or rates execution support.

Expected output:

- Universe, region, and transmission channel
- Source/as-of posture and key data releases
- Macro facts versus policy/market interpretation
- Cross-asset read-through to the requested security, sector, asset, or portfolio
- Scenario path and what would falsify it
- Confidence, stale data, and missing source gaps
- Open macro questions and missing support

Decision quality fields when applicable:

- `evidence_grade`, `source_freshness`, `source_quality`
- `conflict_status`, `decision_readiness`, `confidence`
- `scenario_cases`, `contrary_evidence`, `update_triggers`
- `invalidation_conditions` and forecast permission fields when relevant

Quality floor:

- Apply the shared artifact quality floor.
- Tag material narrative claims as `[factual]`, `[inference]`, or `[assumption]`.
- Distinguish economic data, policy statements, market pricing, user assumptions, and analyst inference.
- State when evidence is suggestive rather than conclusive, especially around regime shifts and small samples.
- For historical or replay work, bind first-release, vintage, or real-time-period
  observations. Label a currently revised series as hindsight evidence rather
  than treating it as known at the historical cutoff.
- Use `screen-grade` or `not-decision-ready` when market anchors, source dates, or instrument-specific inputs are missing.
- Do not fabricate economic releases, policy dates, market levels, source timestamps, or tool output.

Write outputs under `trading/reports/macro/`.
