# Research Memory And Artifacts

This document owns TradingCodex DB-first research memory, source/as-of posture,
artifact export paths, report quality floor, readiness labels, and artifact
contracts. Research memory is part of the harness Improvement subsystem; see
[improvement-loop.md](./improvement-loop.md).

## DB-First Research Memory

Runtime research memory is central-DB-first. Markdown and JSON files are
Codex-readable exports, caches, or artifacts unless a document explicitly says
otherwise.

Canonical records:

- `ResearchArtifact`
- `ResearchArtifactVersion`
- `SourceSnapshot`
- `WorkspaceContext`
- `WorkflowRun`
- `ArtifactRef`
- `AuditEvent`

If a DB artifact exists, runtime memory exists even when no export file exists.
If only a file exists without a DB record, it is not canonical research memory.

## Research Artifact Fields

Research artifacts should preserve:

- title
- artifact type
- universe
- symbol/instrument identifiers where applicable
- owning role or workflow
- markdown body
- metadata
- version
- content hash
- readiness label
- source/as-of posture
- retrieved-at timestamp where applicable
- stale-data warnings
- workspace provenance
- user/role provenance

## Source Snapshot Discipline

Market-sensitive and source-sensitive claims should record:

- source name/provider
- source URL or stable locator where available
- retrieval timestamp
- as-of date/time
- publication date/time when relevant
- content hash or request/result hash where practical
- coverage limitations
- stale/missing warnings

Research quality focuses on source/as-of discipline, retrieved-at metadata,
stale-data warnings, versioning, and invalidation rather than long-lived
embedding memory.

## MCP Research Tools

Codex and subagents should use MCP tools when DB access is available:

- `create_research_artifact`
- `get_research_artifact`
- `list_research_artifacts`
- `search_research_artifacts`
- `append_research_artifact_version`
- `export_research_artifact_md`
- `record_source_snapshot`

MCP tools read/write through the service layer instead of scraping markdown
files.

## Artifact Export Paths

Canonical state lives in the central Django DB. Paths below are readable
export/cache/artifact paths for Codex and humans.

| Artifact | Path |
| --- | --- |
| Evidence packs | `trading/research/*.evidence.md` |
| Fundamental reports | `trading/reports/fundamental/` |
| Technical reports | `trading/reports/technical/` |
| News reports | `trading/reports/news/` |
| Macro reports | `trading/reports/macro/` |
| Instrument reports | `trading/reports/instrument/` |
| Valuation reports | `trading/reports/valuation/` |
| Portfolio reports | `trading/reports/portfolio/` |
| Risk/policy reports | `trading/reports/risk/`, `trading/reports/policy/` |
| Draft orders | `trading/orders/draft/*.order_intent.json` |
| Approved orders | `trading/orders/approved/*.order_intent.json` |
| Approval receipts | `trading/approvals/*.approval_receipt.json` |
| Executed orders | `trading/orders/executed/*.execution_result.json` |
| Postmortems | `trading/reports/postmortem/*.postmortem_report.json` |
| Skill change proposals | `.tradingcodex/mainagent/skill-change-proposals/*.yaml` |

Execution-sensitive state is updated by the MCP/service layer. Policy decisions
remain service-layer decisions.

## Quality Harness Floor

Investment reports, role handoffs, and final syntheses share a quality floor.

| Rule | Application |
| --- | --- |
| Claim tags | Mark material narrative claims as `[factual]`, `[inference]`, or `[assumption]` in handoff narrative where useful. |
| `[factual]` | Use only for verified data, cited source content, existing artifact content, or directly observed command/tool output. |
| `[inference]` | Use for analytical conclusions, risk judgments, and thesis-change judgments derived from evidence. |
| `[assumption]` | Use for scenario inputs, transaction cost, capacity, correlation, liquidity, sizing, and modeling choices. |
| Anti-fabrication | Do not invent metrics, factor loadings, transaction costs, validation results, source dates, market prices, filings, approvals, executions, or artifact content. |
| Uncertainty disclosure | Disclose small samples, thin regime coverage, high parameter sensitivity, and weak source coverage. |
| Suggestive vs conclusive | If evidence is suggestive, say so instead of turning it into a conclusion. |
| Empirical vs economic | Separate empirical stability from economic plausibility. |
| Paper vs live | State when paper alpha may disappear under live implementation friction. |
| Confidence | Lower confidence when data quality, source coverage, sample size, regime coverage, or validation setup is weak. |
| Source/as-of posture | For market-sensitive inputs, record source date, as-of, retrieved-at, provider/tool, and missing/stale warnings. |
| Hero/support artifact split | Choose the user-facing report, tracker, workbook, or synthesis first; keep CSV/JSON/run log/source indexes as support/audit layers. |
| Conservative readiness | Use conservative labels such as `factual-baseline`, `screen-grade`, `not-decision-ready`, `ready-for-portfolio-risk`, `ready-for-draft`, or `blocked`. |

## Readiness Labels

| Label | Meaning |
| --- | --- |
| `factual-baseline` | Verified descriptive facts are present; no recommendation or implementation posture. |
| `screen-grade` | Useful for triage/watchlist work; not enough for decision or order drafting. |
| `not-decision-ready` | Missing evidence, stale data, unsupported assumptions, or unresolved conflict blocks decision support. |
| `ready-for-portfolio-risk` | Research/valuation is sufficient for portfolio and risk review, not execution. |
| `ready-for-draft` | Portfolio and risk context support draft order intent creation, subject to schema and policy checks. |
| `blocked` | Policy, data quality, role boundary, adapter support, or user instruction blocks the workflow. |

## Export Behavior

Exports should be deterministic enough for review:

- preserve artifact ID and version where practical
- include source/as-of metadata
- include readiness label
- avoid raw secrets
- avoid claiming export files are canonical
- make stale/missing source warnings visible
- use stable paths for role-owned reports

Generated workspace exports are useful for Codex reading and human review, but
the DB record remains the canonical runtime object.
