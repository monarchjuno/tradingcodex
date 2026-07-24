---
name: tcx-evidence
description: "Collect source-backed investment evidence at the start of analyst workflows. Use for research intake, source lists, fact versus assumption separation, and missing-evidence tracking before analysis."
---

# Collect Evidence

Build the smallest evidence set that can answer the assigned question.

## Choose one artifact by default

- Create an `evidence_pack` under `trading/research/` only when source intake
  has independent reuse or cross-role handoff value, or preserves a material
  source conflict or gap separately from a role conclusion.
- Create a `role_report` under `trading/reports/<role>/` when the output includes
  that role's analysis, conclusion, or downstream handoff. When collected
  evidence only supports that same report, create the role report directly;
  an evidence pack is not a prerequisite or default duplicate.
- When both are justified, create the evidence pack first. Put its authenticated
  Artifact ID in the role report's `input_artifact_ids`, retain every exact
  Snapshot/Dataset ID the report uses, and rely on service receipts/hashes for
  lineage. Do not copy the same body into both artifacts.
- When persistence has no reuse, provenance, decision, or audit value, hand off
  compact IDs without creating either artifact.

1. Identify the universe and workflow type. Use only relevant, callable source
   classes; mark missing universe support or unavailable routes as gaps.
2. Apply `tcx-source-gate` before external retrieval and retain its returned
   source IDs and gaps.
   If analysis exposes a material gap, conflict, stale anchor, or identifier
   mismatch within your assigned question and specialty, collect the additional
   evidence needed to resolve it without waiting for a new field-by-field
   instruction. Use evidence value, not a fixed source or call count, as the
   stopping rule.
3. Distinguish observations, source or management claims, analysis, and
   assumptions in natural prose where ambiguity matters. Use opened filings,
   releases, and exchange/regulator records when source-of-record status
   matters. Otherwise accept attributable OpenBB/provider data, credible
   institutional data, and reputable secondary reporting for the claims they
   competently and currently cover. A search snippet or unsupported assumption
   is not evidence.
4. State the identifier, source list, source-trust notes, market context,
   missing evidence, freshness, decision readiness, confidence, update
   triggers, invalidation conditions, and contrary evidence that matter.
5. If persisting, apply `$tcx-artifact`, the canonical shared quality floor,
   and use authenticated MCP.

Carry Snapshot, Dataset, and Artifact IDs plus a compact card into calculation
or handoff context. Do not summarize away used Dataset rows or repeat an
unchanged source call. Do not expand into another role's complete data family
or broad just-in-case collection; hand off a genuinely cross-specialty gap.

For `record_source_snapshot`, omit caller-owned `snapshot_id`, `retrieved_at`,
and `recorded_at`. Supply `known_at` only when an exact timezone-aware knowable
time is genuinely supported; never repair validation with a guessed time.
When an artifact cites returned `source_snapshot_ids`, set `knowledge_cutoff`
to a timezone-qualified RFC 3339 time at or after the maximum service-returned
snapshot `known_at`, preferably that exact maximum. Never use end-of-day or another future time, and never send a date-only value. If no exact bound is
available, omit the optional cutoff; if no snapshot exists, use `[]`.

Use `ready-for-portfolio-risk` when every conclusion-driving claim has current,
attributable, fit-for-purpose support and material conflicts and gaps are
explicit; mixed source classes are allowed. Use `factual-baseline` for
descriptive work, and `screen-grade` or `not-decision-ready` only when a
material gap limits downstream use. Include high/medium/low confidence with one
reason independent of readiness. Use
`follow_up_requests=[]` when none apply; otherwise provide structured objects
with `trigger`, `suggested_role`, `question`, `reason`, and `materiality`.
