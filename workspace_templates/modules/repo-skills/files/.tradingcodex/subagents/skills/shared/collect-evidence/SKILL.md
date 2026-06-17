---
name: collect-evidence
description: "Collect source-backed investment evidence at the start of analyst workflows. Use for research intake, source lists, fact versus assumption separation, and missing-evidence tracking before analysis."
---

# Collect Evidence

Use this skill at the start of an investment workflow.

Before using external data, apply read-only source, provider, as-of, and coverage checks.

Universe and source posture:

- Identify the investment universe before collecting evidence: public equity, ETF/index, crypto public market, macro/rates/FX/commodity, cross-asset overlay, credit signal, or unsupported/unclear.
- For public equity, the detailed evidence shape usually includes company filings/IR, transcripts/presentations, market data/estimates, internal/user notes, portfolio/model/tracker context, and news.
- For other universes, collect only source categories that are actually available and relevant; label missing installed workflows or unavailable source routes as support gaps.
- Record source/as-of or retrieved-at timestamps for market-sensitive data.
- Keep support files such as source indexes, raw exports, normalized CSVs, and logs secondary to the evidence pack unless explicitly requested.

Expected output:

- Universe and workflow type
- Company or asset identifier
- Source list
- Filing, news, price, and market context references
- Facts versus assumptions
- Missing evidence
- Source/as-of posture and support gaps

Quality floor:

- Apply the shared artifact quality floor.
- Tag material narrative claims as `[factual]`, `[inference]`, or `[assumption]`.
- Include source dates or retrieval dates when available.
- Include provider/tool names, query parameters, warnings, and credential or coverage failures for external sources.
- Separate verified facts, source claims, assumptions, and analyst inference.
- Flag stale, missing, or conflicting evidence.
- Label the evidence pack `factual-baseline`, `screen-grade`, or `not-decision-ready` when source gaps limit downstream use.
- Do not fabricate source dates, prices, filings, metrics, or tool output.
- Include confidence: high, medium, or low, with one reason.
- When writing markdown, include context summary, handoff state, confidence,
  missing-evidence, next-recipient, blocked-action, and source-snapshot metadata
  in frontmatter.

Write evidence packs under `trading/research/`.
