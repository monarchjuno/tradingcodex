---
name: tcx-evidence
description: "Collect source-backed investment evidence at the start of analyst workflows. Use for research intake, source lists, fact versus assumption separation, and missing-evidence tracking before analysis."
---

# Collect Evidence

Build the smallest evidence pack that can answer the assigned question.

1. Identify the universe and workflow type. Use only relevant, callable source
   classes; mark missing universe support or unavailable routes as gaps.
2. Apply `tcx-source-gate` before external retrieval. Reuse a satisfying
   Dataset first, then one relevant enabled user MCP/skill, supported OpenBB,
   and finally TradingCodex official/web fallback. Record provider/tool,
   stable locator or material query, source/retrieval time, units, coverage,
   warnings, conflicts, and credential failures.
3. Separate `[factual]` observations, source/management claims,
   `[inference]`, and `[assumption]`. Prefer opened primary filings, releases,
   and exchange/regulator records over snippets, secondary news, stale data,
   or unsupported assumptions.
4. State the identifier, source list, source-trust notes, market context,
   missing evidence, freshness, decision readiness, confidence, update
   triggers, invalidation conditions, and contrary evidence that matter.
5. Apply the shared artifact quality floor and persist the pack under
   `trading/research/` only through authenticated MCP.

For a row result, call `record_external_data_result` immediately and preserve
all validated rows. Carry only its Snapshot, Dataset, and acquisition receipt
IDs plus a compact card into calculation or handoff context. Do not summarize
away the source rows or make another provider call for the same promoted
coverage.

For `record_source_snapshot`, omit caller-owned `snapshot_id`, `retrieved_at`,
and `recorded_at`. Supply `known_at` only when an exact timezone-aware knowable
time is genuinely supported; never repair validation with a guessed time.
When an artifact cites returned `source_snapshot_ids`, set `knowledge_cutoff`
to a timezone-qualified RFC 3339 time at or after the maximum service-returned
snapshot `known_at`, preferably that exact maximum. Never use end-of-day or another future time, and never send a date-only value. If no exact bound is
available, omit the optional cutoff; if no snapshot exists, use `[]`.

Use `factual-baseline`, `screen-grade`, or `not-decision-ready` when gaps limit
downstream use. Include high/medium/low confidence with one reason. An
`accepted` artifact must pass service quality; correct a rejected payload
instead of weakening its handoff. Use `follow_up_requests=[]` when none apply;
otherwise provide structured objects with `trigger`, `suggested_role`,
`question`, `reason`, and `materiality`.
