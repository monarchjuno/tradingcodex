# Research Memory And Artifacts

This document owns TradingCodex research handoff memory, source/as-of posture,
artifact paths, report quality floor, readiness labels, and artifact contracts.
Research memory is part of the harness Improvement subsystem; see
[improvement-loop.md](./improvement-loop.md).

## File-Native Research Memory

Research handoff memory is workspace-file-native. Codex agents and humans must
be able to inspect the same markdown files without requiring hidden DB state.

Canonical research files live under:

- `trading/research/*.md`
- `trading/research/*.evidence.md`
- `trading/reports/<role>/*.md`

The service layer may index, validate, search, preview, version, and write
these files, but the file is the source of truth for handoff-ready research.
If a workspace has no matching markdown file, the product web and CLI should
not pretend a canonical research artifact exists only because a DB row exists.

Non-artifact research freshness records are also file-native:

- `trading/research/source-snapshots/*.json` for provider/as-of/retrieved metadata

Research artifact creation, source snapshot recording, search, get, list, and
export do not write `ResearchArtifact`, `ResearchArtifactVersion`,
`SourceSnapshot`, `AuditEvent`, or `McpToolCall` rows. Research MCP calls are
intentionally excluded from the DB call ledger so markdown, frontmatter, source
metadata, and payloads stay in workspace files only.

## Research Artifact Fields

Research markdown frontmatter should preserve:

- `artifact_id`
- `artifact_type`
- `universe`
- `workflow_type`
- `role`
- `symbol` or instrument identifiers where applicable
- `title`
- `source_as_of`
- `readiness_label`
- `version`
- `content_hash`
- `workspace_native`
- `created_by`

The markdown body should preserve source-aware claims, evidence, assumptions,
handoff conclusions, and any role-owned limitations.

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

## MCP And CLI Research Tools

Codex and subagents should use service-layer tools when available:

- `create_research_artifact`
- `get_research_artifact`
- `list_research_artifacts`
- `search_research_artifacts`
- `append_research_artifact_version`
- `export_research_artifact_md`
- `record_source_snapshot`

These tools read and write workspace markdown files for research artifacts.
They still use the Django service boundary for validation, provenance, audit,
and MCP role/capability checks. Product web renders the markdown body with a
maintained parser/sanitizer and displays frontmatter separately as metadata;
frontmatter must not be mixed into the rendered markdown body.

`create_research_artifact` creates or updates a workspace file. It must not
silently overwrite an existing artifact id with different content in the same
workspace. Use `append_research_artifact_version` or
`./tcx research append <artifact-id>` for intentional version updates.

Two workspaces may use the same `artifact_id` for different local research
files. That is expected because research handoff state is workspace-native.

## Artifact Paths

| Artifact | Path |
| --- | --- |
| Evidence packs | `trading/research/*.evidence.md` |
| Research memos | `trading/research/*.md` |
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

Execution-sensitive state is updated by the MCP/service layer. Policy
decisions, approvals, executions, portfolio snapshots, and audit ledgers remain
service-layer decisions.

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
| Handoff acceptance | Mark whether a role artifact is `accepted`, `revise`, `blocked`, or `waiting`; downstream roles should not repair upstream work outside their owned question. |

## Readiness Labels

| Label | Meaning |
| --- | --- |
| `factual-baseline` | Verified descriptive facts are present; no recommendation or implementation posture. |
| `screen-grade` | Useful for triage/watchlist work; not enough for decision or order drafting. |
| `not-decision-ready` | Missing evidence, stale data, unsupported assumptions, or unresolved conflict blocks decision support. |
| `ready-for-portfolio-risk` | Research/valuation is sufficient for portfolio and risk review, not execution. |
| `ready-for-draft` | Portfolio and risk context support draft order intent creation, subject to schema and policy checks. |
| `blocked` | Policy, data quality, role boundary, adapter support, or user instruction blocks the workflow. |

## File Behavior

Research file writes should be deterministic enough for review:

- preserve artifact ID and version
- include source/as-of metadata
- include readiness label
- include content hash
- avoid raw secrets
- use stable paths for role-owned reports
- make stale/missing source warnings visible
- keep file content readable by Codex without a DB lookup
