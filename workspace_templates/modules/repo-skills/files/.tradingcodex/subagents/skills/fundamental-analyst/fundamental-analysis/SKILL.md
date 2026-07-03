---
name: fundamental-analysis
description: "Analyze business fundamentals for an investment workflow. Use for business model, financial statement quality, management, capital allocation, and fundamental risk reports."
---

# Fundamental Analysis

Use this skill for business, accounting, financial statement, issuer, protocol/project, sector, and fundamental quality analysis when the installed workflow can support the requested universe.

Universe method:

- Public equity: analyze business model, financial statement linkage, earnings quality, cash conversion, DuPont-style driver decomposition when useful, management, capital allocation, operating KPIs, source/as-of posture, and evidence gaps.
- ETF/index: analyze constituent, sector, factor, liquidity, passive-flow, and methodology fundamentals only when supported by evidence.
- Crypto public markets: analyze protocol economics, supply, usage, governance, liquidity, and ecosystem fundamentals as research context only.
- Macro/rates/FX/commodities: identify the relevant fundamental transmission channel and call out specialist underwriting gaps rather than inventing unsupported coverage.
- Credit signals may inform common-equity or portfolio downside; do not underwrite debt securities unless a dedicated credit workflow is installed.

Expected output:

- Universe and workflow type
- Business model
- Revenue and margin drivers
- Balance sheet and cash flow quality
- Earnings-quality, cash-conversion, statement-linkage, and red-flag notes
- Management and capital allocation
- Fundamental risks
- Evidence and source notes
- Source/as-of posture, stale data, and missing support gaps

Decision quality fields when applicable:

- `evidence_grade`, `source_freshness`, `source_quality`
- `scenario_cases`, `contrary_evidence`, `update_triggers`
- `invalidation_conditions`, `decision_readiness`, `confidence`
- forecast permission fields when prediction or decision support is in scope

Quality floor:

- Apply the shared artifact quality floor.
- Tag material narrative claims as `[factual]`, `[inference]`, or `[assumption]`.
- State what matters most for the company instead of listing generic ratios.
- Explain drivers, risks, and uncertainty in plain causal terms.
- Separate facts from interpretation.
- Distinguish issuer/company fundamentals from security actionability; good fundamentals alone are not a buy decision.
- Use `factual-baseline`, `screen-grade`, or `not-decision-ready` when missing evidence prevents a stronger conclusion.
- Do not fabricate financial metrics, source facts, filings, or validation results.
- Name missing evidence and confidence.
- End with unresolved questions and evidence needed for a stronger conclusion.

Write outputs under `trading/reports/fundamental/`.
