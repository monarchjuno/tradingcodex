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
- `trading/forecasts/*.jsonl` for append-only forecast ledger records

Research artifact creation, source snapshot recording, search, get, list, and
export do not create Django research model rows or research-owned DB tables.
They also do not write `AuditEvent` or `McpToolCall` rows. Research MCP calls
are intentionally excluded from the DB call ledger so markdown, frontmatter,
source metadata, and payloads stay in workspace files only.

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
- `context_summary`: concise downstream context before opening full markdown
- `reader_summary`: plain-language first-read summary for non-expert users
- `next_action`: the next allowed action, wait state, or reviewer to use
  without implying an order or recommendation
- `handoff_state`: `accepted`, `revise`, `blocked`, or `waiting`
- `confidence`: a conservative confidence label or score
- `missing_evidence`: explicit missing, stale, or weak evidence as a list
- `next_recipient`: the next eligible role or reviewer, or a terminal state such
  as `none`
- `blocked_actions`: actions still blocked by evidence, role boundary, policy,
  approval, execution, or user scope
- `source_snapshot_ids`: source snapshot files that support the artifact, when
  available
- `follow_up_requests`: optional structured artifact-driven follow-up proposals
  with trigger, suggested fixed role, delta question, reason, materiality,
  provenance, advisory consent posture, and blocked actions; these proposals do
  not dispatch subagents or decide lane scope
- decision-quality fields when applicable: `evidence_grade`,
  `source_freshness`, `source_quality`, `conflict_status`,
  `decision_readiness`, forecast permission fields, scenario cases, contrary
  evidence, update triggers, invalidation conditions, and investor-profile gaps
- `version`
- `content_hash`
- `workspace_native`
- `created_by`

The markdown body should preserve source-aware claims, evidence, assumptions,
handoff conclusions, and any role-owned limitations. Material narrative claims
should use `[factual]`, `[inference]`, or `[assumption]` tags when the
distinction affects downstream use.

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

`tcx quality-check <artifact-path>` gives a friendly diagnostic pass/fail for
empty files and invalid JSON while surfacing warnings for weak research
metadata and context size. `tcx quality-check <artifact-path> --strict` is the
handoff gate for research markdown: it fails when source/as-of posture,
`context_summary`, claim tags, handoff state, confidence, missing-evidence
fields, next-recipient routing, or source snapshot metadata are absent.
`reader_summary` and `next_action` are preserved and surfaced for better
reader UX. Missing values produce warnings so older artifacts are not rejected
solely for lacking beginner-facing first-read metadata. Long-run
`tcx subagents context-audit --strict` output also aggregates those missing
reader-first fields as warnings so teams can spot weak handoffs without
blocking legacy research files.

Forecast ledger records under `trading/forecasts/*.jsonl` are validated by the
same quality-check path. Strict validation requires open/closed status,
resolvable target, horizon, probability or probability range, evidence IDs,
contrary evidence, resolution source, and review date. Initial validation checks
schema and open/closed state only; Brier scoring and calibration review wait
until enough forecasts resolve.

These tools read and write workspace markdown files for research artifacts.
They still use the Django service boundary for validation, provenance, audit,
and MCP role/capability checks. Product web renders the markdown body with a
maintained parser/sanitizer and displays frontmatter separately as metadata;
frontmatter must not be mixed into the rendered markdown body.

Research and order file path inputs are workspace-contained. Service-layer path
arguments must be relative paths, must not contain `..`, must not resolve through
symlinks outside the workspace, and must remain under the artifact directory
allowed for the operation: research markdown under `trading/research/` or
`trading/reports/`, source snapshots under
`trading/research/source-snapshots/`, draft/approved/executed orders under
`trading/orders/`, and approval receipts under `trading/approvals/`.

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
| Forecast ledger records | `trading/forecasts/*.jsonl` |
| Order tickets | central DB `OrderTicket` records |
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
| Forecast discipline | Required forecasts must be horizon-bound, evidence-aware, updateable, and either valid for the ledger or blocked with `forecast_block_reason`. |
| Anti-overfit validation | Backtest, signal, and model-performance artifacts must address leakage, survivorship bias, data snooping, out-of-sample coverage, costs, capacity, regime sensitivity, and live friction. |

## Readiness Labels

| Label | Meaning |
| --- | --- |
| `factual-baseline` | Verified descriptive facts are present; no recommendation or implementation posture. |
| `screen-grade` | Useful for triage/watchlist work; not enough for decision or order drafting. |
| `not-decision-ready` | Missing evidence, stale data, unsupported assumptions, or unresolved conflict blocks decision support. |
| `ready-for-portfolio-risk` | Research/valuation is sufficient for portfolio and risk review, not execution. |
| `ready-for-draft` | Portfolio and risk context support draft order-ticket creation, subject to schema and policy checks. |
| `blocked` | Policy, data quality, role boundary, adapter support, or user instruction blocks the workflow. |

## File Behavior

Research file writes should be deterministic enough for review:

- preserve artifact ID and version
- include source/as-of metadata
- include readiness label
- include concise `context_summary` so downstream roles can start from bounded
  context
- include handoff state, confidence, missing-evidence, next-recipient, blocked
  actions, and source snapshot IDs
- include content hash
- avoid raw secrets
- use stable paths for role-owned reports
- make stale/missing source warnings visible
- keep file content readable by Codex without a DB lookup
